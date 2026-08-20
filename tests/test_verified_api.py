"""答案订正：/api/verified，以及它在检索里的优先级。

这一路的承诺只有一句话：**「我改了答案，下次就照我改的答」**。
所以这里测的重点不是「存下来了没有」，而是这句承诺的三个落点：

  1. 保存时**当场**进索引（`applied`）
  2. 进索引的块被标成 `verified`
  3. 检索里够格的 `verified` 块排在语雀原文前面

⚠️ 和 `test_corrections_api.py` 一样，embedding 用假的——真的要打外部 API。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import PASSWORD
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.db.models import Chunk, Document, InviteCode, User, VerifiedAnswer
from copilot.retrieve import VERIFIED_PROMOTE_SCORE, _verified_first

QUESTION = "退货入库的不良品要怎么处理"
ANSWER = "先在【退货入库单】里勾选不良品，再指定不良品仓位，最后审核。"


@pytest.fixture
async def author(api_client, maker):
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    email = f"vf-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "inviteCode": code}
    )
    assert r.status_code == 201, r.text
    user_id = uuid.UUID(r.json()["id"])

    yield user_id

    async with maker() as s:
        docs = list(
            (
                await s.execute(select(Document).where(Document.source_type == "verified"))
            ).scalars()
        )
        for d in docs:
            await s.execute(delete(Chunk).where(Chunk.document_id == d.id))
            await s.delete(d)
        await s.execute(delete(VerifiedAnswer))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


def _payload(**over):
    body = {"question": QUESTION, "answer": ANSWER}
    body.update(over)
    return body


# ---------- 接口 ----------


async def test_save_indexes_it_right_away(api_client, author, fake_providers, maker):
    """⭐ 保存 = 立刻生效。回执里的 `applied` 就是这个承诺。"""
    r = await api_client.post("/api/verified", json=_payload())
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["applied"] is True
    assert "生效" in data["note"]
    assert data["verified"]["question"] == QUESTION

    async with maker() as s:
        doc = (
            await s.execute(
                select(Document).where(
                    Document.source_type == "verified",
                    Document.source_url == data["verified"]["id"],
                )
            )
        ).scalar_one()
        # 公共：它要盖住**所有人**的错误答案，不只是订正者自己的
        assert doc.owner_id is None
        chunks = list(
            (await s.execute(select(Chunk).where(Chunk.document_id == doc.id))).scalars()
        )

    assert chunks, "订正没有进索引，等于没生效"
    assert all(c.verified for c in chunks), "块没打上 verified，检索里就排不到前面"
    # 问题本身也要进正文——检索拿的是用户的问法去比对，只放答案会召不回
    assert QUESTION in chunks[0].content
    assert ANSWER in chunks[0].content


async def test_second_save_replaces_instead_of_piling_up(api_client, author, fake_providers, maker):
    """同一个问题再订正一次是**更新**。

    两条互相打架的"标准答案"会被检索随机命中，那种错的样子是
    「答案时好时坏」——最难查的一种。
    """
    r1 = await api_client.post("/api/verified", json=_payload())
    r2 = await api_client.post("/api/verified", json=_payload(answer="改第二遍的答案"))
    assert r2.status_code == 201, r2.text
    assert r1.json()["verified"]["id"] == r2.json()["verified"]["id"]

    async with maker() as s:
        rows = list((await s.execute(select(VerifiedAnswer))).scalars())
        assert len(rows) == 1
        assert rows[0].answer == "改第二遍的答案"

        docs = list(
            (
                await s.execute(select(Document).where(Document.source_type == "verified"))
            ).scalars()
        )
        assert len(docs) == 1, "同一个问题不该留下两篇订正文档"
        chunks = list(
            (await s.execute(select(Chunk).where(Chunk.document_id == docs[0].id))).scalars()
        )
        assert all("改第二遍" in c.content for c in chunks), "旧那版的块没被换掉"


async def test_delete_takes_it_out_of_the_index(api_client, author, fake_providers, maker):
    """撤销之后，知识库要**真的**回到原来的样子——索引里那份也得删干净。"""
    vid = (await api_client.post("/api/verified", json=_payload())).json()["verified"]["id"]
    assert (await api_client.delete(f"/api/verified/{vid}")).status_code == 204

    async with maker() as s:
        assert list((await s.execute(select(VerifiedAnswer))).scalars()) == []
        docs = list(
            (
                await s.execute(
                    select(Document).where(
                        Document.source_type == "verified", Document.source_url == vid
                    )
                )
            ).scalars()
        )
        assert docs == [], "订正文档还留在索引里，撤销就是假的"


async def test_everyone_sees_every_verified(api_client, author, fake_providers):
    """列表不按作者过滤——它们改的是同一个公共知识库。"""
    await api_client.post("/api/verified", json=_payload())
    r = await api_client.get("/api/verified")
    assert r.status_code == 200
    assert any(v["question"] == QUESTION for v in r.json())


async def test_requires_login(api_client):
    """未登录一律 401——订正改的是所有人都会看到的内容。"""
    assert (await api_client.post("/api/verified", json=_payload())).status_code == 401
    assert (await api_client.get("/api/verified")).status_code == 401


async def test_answer_cannot_be_empty(api_client, author, fake_providers):
    """空答案是误操作。存下来的话，用户会得到一条空的"标准答案"。"""
    assert (await api_client.post("/api/verified", json=_payload(answer=""))).status_code == 422


# ---------- 排序规则（纯函数）----------


class FakeChunk:
    def __init__(self, name: str, verified: bool) -> None:
        self.name = name
        self.verified = verified

    def __repr__(self) -> str:  # 断言失败时看得懂
        return self.name


def test_verified_goes_first():
    """够格的订正排到最前面，其余顺序不动。"""
    yuque = FakeChunk("语雀原文", verified=False)
    fixed = FakeChunk("订正", verified=True)
    out = _verified_first([(yuque, 0.9), (fixed, 0.8)])
    assert [c.name for c, _ in out] == ["订正", "语雀原文"]


def test_low_scoring_verified_stays_put():
    """⚠️ 不是无条件置顶。

    一条为别的问题写的订正，勉强过了门槛也不该挤掉真正对的那一篇——
    那种错的表现是「自从我改了那个答案，别的问题也开始答错了」。
    """
    yuque = FakeChunk("语雀原文", verified=False)
    weak = FakeChunk("蹭上来的订正", verified=True)
    out = _verified_first([(yuque, 0.9), (weak, VERIFIED_PROMOTE_SCORE - 0.01)])
    assert [c.name for c, _ in out] == ["语雀原文", "蹭上来的订正"]


def test_no_verified_is_left_untouched():
    """一条订正都没有时，原样返回——这是绝大多数请求走的路。"""
    a, b = FakeChunk("a", False), FakeChunk("b", False)
    pairs = [(a, 0.9), (b, 0.8)]
    assert _verified_first(pairs) == pairs
