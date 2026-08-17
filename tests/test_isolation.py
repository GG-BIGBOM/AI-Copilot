"""数据隔离测试——本项目最不能出的 bug。

用户 A 绝不能检索到用户 B 上传的文档。一旦漏了，不会有任何报错，
只会在某天被用户发现自己的私有资料出现在别人的答案里。

这些测试用假 embedder，不打网络，可以随便跑。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from copilot.db.models import Chunk, Document, InviteCode, User
from copilot.providers.base import RerankResult
from copilot.retrieve import _visibility_filter, search

DIM = 1024


class FakeEmbedder:
    """把文本映射成确定性向量，同样的文本永远得到同样的向量。"""

    dim = DIM

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * DIM
        for i, ch in enumerate(text[:64]):
            v[(ord(ch) * 7 + i) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class PassThroughReranker:
    """不改顺序，给每条一个足够高的分数，避免被阈值滤掉。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=i, score=1.0) for i in range(min(top_k, len(documents)))]


@pytest.fixture
async def two_users_with_docs(engine):
    """造两个用户，各有一篇私有文档，外加一篇公共文档。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    emb = FakeEmbedder()
    tag = uuid.uuid4().hex[:8]

    async with maker() as s:
        alice = User(email=f"alice-{tag}@t.local", password_hash="x")
        bob = User(email=f"bob-{tag}@t.local", password_hash="x")
        s.add_all([alice, bob])
        await s.flush()

        specs = [
            (alice.id, f"爱丽丝的私密报价单-{tag}", "爱丽丝的客户报价是每单三十元"),
            (bob.id, f"鲍勃的私密合同-{tag}", "鲍勃的客户合同金额是五十万元"),
            (None, f"公共手册-{tag}", "公共知识库里的订单同步说明"),
        ]
        for owner_id, title, body in specs:
            doc = Document(
                owner_id=owner_id,
                source_type="upload",
                title=title,
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
            )
            s.add(doc)
            await s.flush()
            s.add(
                Chunk(
                    document_id=doc.id,
                    owner_id=owner_id,  # 必须跟着文档走
                    ordinal=0,
                    content=body,
                    embedding=emb.embed_query(body),
                    title=title,
                )
            )
        await s.commit()
        yield maker, alice.id, bob.id, tag

        # 清理，别污染真实数据
        for uid in (alice.id, bob.id):
            await s.execute(delete(Chunk).where(Chunk.owner_id == uid))
            await s.execute(delete(Document).where(Document.owner_id == uid))
        await s.execute(delete(Document).where(Document.title == f"公共手册-{tag}"))
        await s.execute(delete(User).where(User.id.in_([alice.id, bob.id])))
        await s.commit()


# ---------- 过滤条件本身 ----------


def test_anonymous_sees_only_public():
    """未登录只能看公共库。"""
    clause = str(_visibility_filter(None))
    assert "owner_id IS NULL" in clause
    assert "=" not in clause.split("IS NULL")[-1], "匿名不该带上任何 owner 相等条件"


def test_logged_in_sees_public_plus_own():
    uid = uuid.uuid4()
    clause = str(_visibility_filter(uid))
    assert "IS NULL" in clause
    assert "OR" in clause.upper()


# ---------- 真库检索 ----------


async def test_alice_cannot_see_bobs_document(two_users_with_docs):
    """核心用例：A 搜不到 B 的私有文档。"""
    maker, alice_id, bob_id, tag = two_users_with_docs
    async with maker() as s:
        result = await search(
            s,
            "鲍勃的客户合同金额是五十万元",  # 直接用 B 的原文去搜，最严苛的情形
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=alice_id,
            top_k=20,
            rerank_k=20,
        )
    titles = [c.citation.title for c in result.chunks]
    assert not any("鲍勃" in t for t in titles), f"泄漏了！爱丽丝看到了：{titles}"


async def test_bob_cannot_see_alices_document(two_users_with_docs):
    maker, alice_id, bob_id, tag = two_users_with_docs
    async with maker() as s:
        result = await search(
            s,
            "爱丽丝的客户报价是每单三十元",
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=bob_id,
            top_k=20,
            rerank_k=20,
        )
    titles = [c.citation.title for c in result.chunks]
    assert not any("爱丽丝" in t for t in titles), f"泄漏了！鲍勃看到了：{titles}"


async def test_owner_can_see_own_document(two_users_with_docs):
    """隔离不能矫枉过正——自己得看得到自己的。"""
    maker, alice_id, bob_id, tag = two_users_with_docs
    async with maker() as s:
        result = await search(
            s,
            "爱丽丝的客户报价是每单三十元",
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=alice_id,
            top_k=20,
            rerank_k=20,
        )
    titles = [c.citation.title for c in result.chunks]
    assert any("爱丽丝" in t for t in titles), "自己的文档反而搜不到了"


async def test_anonymous_sees_neither_private_document(two_users_with_docs):
    maker, alice_id, bob_id, tag = two_users_with_docs
    async with maker() as s:
        result = await search(
            s,
            "客户报价合同金额",
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=None,
            top_k=20,
            rerank_k=20,
        )
    titles = [c.citation.title for c in result.chunks]
    assert not any("爱丽丝" in t or "鲍勃" in t for t in titles), f"匿名看到了私有文档：{titles}"


async def test_public_document_visible_to_everyone(two_users_with_docs):
    maker, alice_id, bob_id, tag = two_users_with_docs
    async with maker() as s:
        for uid in (None, alice_id, bob_id):
            result = await search(
                s,
                "公共知识库里的订单同步说明",
                FakeEmbedder(),
                PassThroughReranker(),
                user_id=uid,
                top_k=20,
                rerank_k=20,
            )
            titles = [c.citation.title for c in result.chunks]
            assert any("公共手册" in t for t in titles), f"user_id={uid} 看不到公共文档"


# ---------- 端到端：真上传 → 真 worker → 真检索 ----------


@pytest.fixture
async def alice_uploaded(api_client, maker):
    """走完整链路造一篇私有文档：HTTP 上传 → jobs 队列 → worker 解析 → 入库。

    上面那些用例是直接往库里插 chunk 造出来的，验的是 `_visibility_filter`。
    这个验的是**另一件事**：`owner_id` 能不能从 cookie 里的登录用户，
    一路正确地传到 `chunks.owner_id`——中间要穿过上传接口、队列 payload、
    worker、`write_chunks` 四段代码。任何一段漏了，隔离就形同虚设，
    而且不会有任何报错。

    这是 M6 验收里「换账号登录搜不到那份文档」的自动化版本。
    """
    import shutil

    from copilot.auth.invites import create_invite_codes
    from copilot.config import get_settings
    from copilot.db.models import Job
    from copilot.jobs.worker import run_once

    tag = uuid.uuid4().hex[:8]
    secret = f"爱丽丝的客户报价是每单三十元-{tag}"

    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    r = await api_client.post(
        "/api/auth/register",
        json={
            "email": f"iso-{tag}@test.local",
            "password": "test-password-2026",
            "inviteCode": code,
        },
    )
    assert r.status_code == 201, r.text
    alice_id = uuid.UUID(r.json()["id"])

    # 正文**不加 Markdown 标题**：切分器会把章节路径拼到块正文开头，
    # 而 FakeEmbedder 的桶下标带字符位置（`ord(ch)*7 + i`），前面多几个字
    # 就会把整个向量错开，拿原文去搜反而搜不到自己。真实 embedder 没这个毛病，
    # 但测试要的是「隔离对不对」，不该被假向量的位置敏感性干扰。
    r = await api_client.post(
        "/api/documents",
        files={"file": (f"报价单-{tag}.md", secret.encode(), "text/markdown")},
    )
    assert r.status_code == 201, r.text
    doc_id = uuid.UUID(r.json()["document"]["id"])

    # 真 worker 把队列跑空（embedder 是假的，不打网络）。
    # 不写成「跑一轮」：队列是全库共享的，别的用例可能留下一条任务，
    # 那样这一轮取到的就不是刚才这篇——测试会以一种极难查的方式随机失败
    emb = FakeEmbedder()
    while await run_once(emb, maker) != "idle":
        pass
    async with maker() as s:
        assert (await s.get(Document, doc_id)).status == "done"

    async with maker() as s:
        bob = User(email=f"iso-bob-{tag}@t.local", password_hash="x")
        s.add(bob)
        await s.commit()
        bob_id = bob.id

    yield alice_id, bob_id, secret, doc_id

    async with maker() as s:
        await s.execute(delete(Job).where(Job.payload["document_id"].astext == str(doc_id)))
        await s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id.in_([alice_id, bob_id])))
        await s.commit()
    shutil.rmtree(get_settings().upload_dir / str(alice_id), ignore_errors=True)


async def test_uploaded_document_never_reaches_another_user(alice_uploaded, maker):
    """⭐ 本里程碑最不能红的一条。用 A 的原文去搜，是最严苛的情形。"""
    alice_id, bob_id, secret, _ = alice_uploaded
    async with maker() as s:
        result = await search(
            s, secret, FakeEmbedder(), PassThroughReranker(), user_id=bob_id, top_k=20, rerank_k=20
        )
    hits = [c.content for c in result.chunks]
    assert not any(secret in h for h in hits), f"泄漏了！鲍勃搜到了爱丽丝上传的内容：{hits}"


async def test_uploaded_document_is_findable_by_its_owner(alice_uploaded, maker):
    """隔离不能矫枉过正——自己传的东西自己得搜得到，否则上传功能等于没有。"""
    alice_id, _, secret, _ = alice_uploaded
    async with maker() as s:
        result = await search(
            s,
            secret,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=alice_id,
            top_k=20,
            rerank_k=20,
        )
    assert any(secret in c.content for c in result.chunks), "自己上传的文档反而搜不到"


async def test_anonymous_never_reaches_uploaded_documents(alice_uploaded, maker):
    alice_uploaded_ids = alice_uploaded
    _, _, secret, _ = alice_uploaded_ids
    async with maker() as s:
        result = await search(
            s, secret, FakeEmbedder(), PassThroughReranker(), user_id=None, top_k=20, rerank_k=20
        )
    assert not any(secret in c.content for c in result.chunks)


async def test_every_chunk_owner_matches_its_document(engine):
    """全库体检：chunks.owner_id 与所属 document 必须一致。

    这两个字段是冗余的，一旦写入时漏同步，隔离就形同虚设，且没有任何报错。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        rows = await s.execute(
            select(Chunk.id)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.owner_id.is_distinct_from(Document.owner_id))
            .limit(5)
        )
        bad = list(rows.scalars())
    assert not bad, f"发现 {len(bad)} 个 chunk 的 owner_id 与其文档不一致：{bad}"
