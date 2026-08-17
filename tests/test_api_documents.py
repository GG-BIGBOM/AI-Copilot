"""上传 / 列表 / 删除 的端到端测试（打真实 ASGI + 真实数据库）。

上传是这个项目对外暴露的最危险的接口——它让匿名注册来的人往服务器磁盘上写文件。
所以这里每条安全项都有一个用例，且**用例名写的是「不做会怎样」**：

    路径穿越       文件名叫 `../../../evil.md` 也只能落在自己的目录里
    大小上限       超限要在写盘途中就掐掉，且不留半截文件
    文档数上限     否则一个账号能慢慢把 40G 磁盘填满
    跨用户         B 看不到、删不掉 A 的文档；公共库谁也删不掉
    删除           必须连向量块一起删，否则「已删除」的文档还会出现在引用里
"""

from __future__ import annotations

import shutil
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.config import get_settings
from copilot.db.models import Chunk, Document, InviteCode, Job, User

PASSWORD = "test-password-2026"
MD = ("面单手册.md", "# 面单设置\n\n先绑定物流账号，再选择模板打印。".encode(), "text/markdown")


# ---------- 夹具 ----------


async def _register(client: AsyncClient, maker) -> tuple[uuid.UUID, str]:
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    r = await client.post(
        "/api/auth/register",
        json={
            "email": f"docs-{uuid.uuid4().hex[:10]}@test.local",
            "password": PASSWORD,
            "inviteCode": code,
        },
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"]), code


async def _purge(maker, user_ids: list[uuid.UUID], codes: list[str]) -> None:
    async with maker() as s:
        doc_ids = list(
            (
                await s.execute(select(Document.id).where(Document.owner_id.in_(user_ids)))
            ).scalars()
        )
        if doc_ids:
            await s.execute(
                delete(Job).where(Job.payload["document_id"].astext.in_([str(d) for d in doc_ids]))
            )
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await s.execute(delete(Document).where(Document.id.in_(doc_ids)))
        await s.execute(delete(InviteCode).where(InviteCode.code.in_(codes)))
        await s.execute(delete(User).where(User.id.in_(user_ids)))
        await s.commit()

    for uid in user_ids:
        shutil.rmtree(get_settings().upload_dir / str(uid), ignore_errors=True)


@pytest.fixture
async def alice(api_client, maker):
    """主 client 上的登录用户。"""
    user_id, code = await _register(api_client, maker)
    yield user_id
    await _purge(maker, [user_id], [code])


@pytest.fixture
async def bob(maker):
    """另一个用户，自带一个独立的 client（cookie 罐子分开才算两个人）。"""
    from copilot.api.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        user_id, code = await _register(client, maker)
        yield user_id, client
    await _purge(maker, [user_id], [code])


@pytest.fixture
async def public_doc(maker):
    """一篇公共库文档（owner_id IS NULL）：不该出现在任何人的「我的文档」里。"""
    async with maker() as s:
        doc = Document(
            owner_id=None,
            source_type="yuque",
            title=f"公共手册-{uuid.uuid4().hex[:8]}",
            source_url="https://www.yuque.com/wdterpqjb/x",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await s.commit()
        doc_id = doc.id

    yield doc_id

    async with maker() as s:
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


async def _upload(client, name="面单手册.md", content=None, mime="text/markdown"):
    body = MD[1] if content is None else content
    return await client.post("/api/documents", files={"file": (name, body, mime)})


# ---------- 未登录 ----------


async def test_all_document_endpoints_require_login(api_client):
    assert (await _upload(api_client)).status_code == 401
    assert (await api_client.get("/api/documents")).status_code == 401
    r = await api_client.delete(f"/api/documents/{uuid.uuid4()}")
    assert r.status_code == 401


# ---------- 上传 ----------


async def test_upload_returns_pending_and_enqueues_a_job(api_client, alice, maker):
    """接口立刻返回 pending，真正的解析交给 worker——**不在请求里做**。

    一份 20MB 的 pptx 解析加向量化要几十秒，卡在 HTTP 请求里浏览器早超时了，
    而且那是同步 CPU 活，会顶住 API 进程唯一的事件循环。
    """
    r = await _upload(api_client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate"] is False
    doc = body["document"]
    assert doc["status"] == "pending"
    assert doc["original_filename"] == "面单手册.md"
    assert doc["chunk_count"] == 0

    async with maker() as s:
        job = (
            await s.execute(select(Job).where(Job.payload["document_id"].astext == doc["id"]))
        ).scalar_one()
        assert job.status == "pending"
        assert job.type == "parse_upload"


async def test_response_does_not_leak_the_server_path(api_client, alice):
    """落盘路径对用户毫无用处，却会把目录结构和 uuid 命名规则透出去。"""
    r = await _upload(api_client)
    assert "stored_path" not in r.text
    assert "uploads" not in r.text


async def test_unsupported_extension_rejected(api_client, alice):
    r = await _upload(api_client, name="木马.exe", content=b"MZ\x90\x00", mime="application/x-msdownload")
    assert r.status_code == 415
    assert "不支持" in r.json()["detail"]


async def test_extensionless_file_rejected(api_client, alice):
    assert (await _upload(api_client, name="README")).status_code == 415


async def test_empty_file_rejected(api_client, alice):
    """0 字节的文件入库只会得到一篇空文档，状态「已完成」却搜不到任何东西。"""
    r = await _upload(api_client, name="空.md", content=b"")
    assert r.status_code == 400
    assert "空" in r.json()["detail"]


async def test_oversize_rejected_and_leaves_no_partial_file(api_client, alice, monkeypatch):
    """⭐ 超限要在写盘途中掐掉，并删掉已经写下的那半截。

    留着的话，一个人反复上传超大文件就能在磁盘上攒出一堆没人认领的碎片——
    而数据库里没有任何记录指向它们，谁也不会去清。
    """
    s = get_settings()
    monkeypatch.setattr(s, "upload_max_bytes", 1024)  # monkeypatch 会自动还原

    updir = s.upload_dir / str(alice)
    before = len(list(updir.glob("*"))) if updir.exists() else 0

    r = await _upload(api_client, name="大.md", content=b"x" * 5000)
    assert r.status_code == 413
    assert "上限" in r.json()["detail"]

    after = len(list(updir.glob("*"))) if updir.exists() else 0
    assert after == before, "超限的文件留下了半截在磁盘上"


async def test_path_traversal_filename_cannot_escape(api_client, alice, maker):
    """⭐ 落盘名用 uuid，原始文件名只进数据库——所以这个名字压根没被拼进路径。

    靠「过滤 ..」那种黑名单迟早会漏（`..%2f`、`....//`、UTF-8 变体），
    这里连过滤都不需要。
    """
    evil = "../../../../../../tmp/evil.md"
    r = await _upload(api_client, name=evil)
    assert r.status_code == 201, r.text

    doc_id = r.json()["document"]["id"]
    async with maker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
        # 原始文件名照原样存着（要显示给用户），但落盘路径必须在自己的目录里
        assert doc.original_filename == evil
        assert doc.stored_path.startswith(f"{alice}/")
        assert ".." not in doc.stored_path
        # 真实落盘位置必须在 uploads/ 之内
        resolved = get_settings().upload_path(doc.stored_path)
        assert get_settings().upload_dir.resolve() in resolved.parents
        assert resolved.exists()
        # 标题也不该带着路径片段去显示
        assert "/" not in doc.title and "\\" not in doc.title


async def test_document_quota_enforced(api_client, alice, monkeypatch):
    """没有这条上限，一个注册用户可以慢慢把 40G 磁盘填满。"""
    monkeypatch.setattr(get_settings(), "upload_max_docs_per_user", 1)

    assert (await _upload(api_client, name="第一份.md")).status_code == 201
    r = await _upload(api_client, name="第二份.md", content=b"# another\n\nother content here")
    assert r.status_code == 409
    assert "上限" in r.json()["detail"]


async def test_same_file_twice_reuses_the_row(api_client, alice):
    """手滑双击不该在列表里留下两条一样的记录，也不该重复烧 embedding 额度。"""
    first = await _upload(api_client)
    second = await _upload(api_client)
    assert second.status_code == 201
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]

    listed = (await api_client.get("/api/documents")).json()
    assert len(listed) == 1


async def test_reupload_after_failure_retries_on_the_same_row(api_client, alice, maker):
    """上次解析失败的那篇，重传要复用同一行——否则列表里堆着一串同名的失败记录。"""
    doc_id = uuid.UUID((await _upload(api_client)).json()["document"]["id"])
    async with maker() as s:
        doc = await s.get(Document, doc_id)
        doc.status = "failed"
        doc.error = "上次坏了"
        await s.commit()

    again = await _upload(api_client)
    assert again.json()["duplicate"] is False
    assert again.json()["document"]["id"] == str(doc_id)
    assert again.json()["document"]["status"] == "pending"
    assert again.json()["document"]["error"] is None


# ---------- 列表 ----------


async def test_list_shows_only_my_documents(api_client, alice, bob, public_doc):
    """「我的文档」只列自己的：公共库那 746 篇语雀文档不在这里，别人的更不在。"""
    bob_id, bob_client = bob
    await _upload(api_client, name="爱丽丝的报价单.md")
    await _upload(bob_client, name="鲍勃的合同.md", content=b"# bob\n\nbob's private contract text")

    mine = (await api_client.get("/api/documents")).json()
    titles = [d["title"] for d in mine]
    assert titles == ["爱丽丝的报价单"]
    assert not any("鲍勃" in t for t in titles), f"看到了别人的文档：{titles}"
    assert str(public_doc) not in [d["id"] for d in mine]


# ---------- 删除 ----------


async def test_delete_removes_chunks_and_file(api_client, alice, maker):
    """⭐ 只删 documents 行是不够的：chunks 还在，那篇「已删除」的文档
    会继续出现在答案的引用里，用户以为删干净了，其实没有。"""
    doc_id = uuid.UUID((await _upload(api_client)).json()["document"]["id"])

    async with maker() as s:
        doc = await s.get(Document, doc_id)
        path = get_settings().upload_path(doc.stored_path)
        assert path.exists()
        s.add(
            Chunk(
                document_id=doc_id,
                owner_id=alice,
                ordinal=0,
                content="先绑定物流账号",
                embedding=[0.0] * get_settings().embedding_dim,
                title=doc.title,
            )
        )
        await s.commit()

    assert (await api_client.delete(f"/api/documents/{doc_id}")).status_code == 204

    async with maker() as s:
        assert await s.get(Document, doc_id) is None
        left = list(
            (await s.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars()
        )
        assert not left, "文档删了，向量块还在"
        # 还没跑的任务也该撤掉，别让队列攒一堆注定作废的行
        jobs = list(
            (
                await s.execute(
                    select(Job).where(Job.payload["document_id"].astext == str(doc_id))
                )
            ).scalars()
        )
        assert not jobs, "文档删了，待办任务还留在队列里"
    assert not path.exists(), "落盘文件没删"


async def test_cannot_delete_someone_elses_document(api_client, alice, bob):
    """别人的文档一律当**不存在**（404 而非 403）：403 等于告诉对方这个 id 是真的。"""
    _, bob_client = bob
    bob_doc = (
        await _upload(bob_client, name="鲍勃的合同.md", content=b"# bob\n\nbob private contract")
    ).json()["document"]["id"]

    r = await api_client.delete(f"/api/documents/{bob_doc}")
    assert r.status_code == 404

    still_there = (await bob_client.get("/api/documents")).json()
    assert [d["id"] for d in still_there] == [bob_doc], "别人把我的文档删了"


async def test_cannot_delete_public_document(api_client, alice, public_doc, maker):
    """公共库（语雀那些）谁也不能删——它们不属于任何用户。"""
    assert (await api_client.delete(f"/api/documents/{public_doc}")).status_code == 404
    async with maker() as s:
        assert await s.get(Document, public_doc) is not None


async def test_delete_unknown_id_is_404(api_client, alice):
    assert (await api_client.delete(f"/api/documents/{uuid.uuid4()}")).status_code == 404
