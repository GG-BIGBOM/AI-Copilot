"""网页版勘误：/api/corrections。

和 `test_corrections.py`（文件那一路）分开：那边测的是 `corrections/*.md`
的解析与覆盖，这边测的是**数据库那一路**和它的接口。

⚠️ 这里一律把 `_reingest_one` 换成假的。真的那个要扫 786 篇语雀原文
再跑一次 embedding——放进单元测试就是让整套测试慢十倍、还依赖外部 API。
「重新入库真的生效了吗」由端到端手测覆盖，不在这里。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import PASSWORD
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.auth.security import create_access_token
from copilot.db.models import Correction, InviteCode, User

URL = "https://www.yuque.com/wdterpqjb/test/correction-case"


@pytest.fixture
def no_reingest(monkeypatch):
    """把重新入库换成「成功，3 个片段」。"""
    from copilot.api.routes import corrections as mod

    async def fake(session, target_url):
        return 3

    monkeypatch.setattr(mod, "_reingest_one", fake)
    return fake


@pytest.fixture
async def author(api_client, maker):
    """注册并登录一个用户，结束时把他和他写的勘误一起删掉。"""
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    email = f"corr-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "inviteCode": code}
    )
    assert r.status_code == 201, r.text
    user_id = uuid.UUID(r.json()["id"])

    yield user_id

    async with maker() as s:
        await s.execute(delete(Correction).where(Correction.author_id == user_id))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


@pytest.fixture
async def editor(author, maker):
    """`author`，但是管理员。

    ⚠️ 提交勘误**人人可做**（见 `test_any_logged_in_user_can_submit`）；
    这个夹具给的是要**审核 / 发布 / 撤销**那几道题。
    下面「邀请码」那一节仍然用 `author`——它要先验一次普通用户被拒。
    """
    async with maker() as s:
        (await s.get(User, author)).is_admin = True
        await s.commit()
    return author


def _payload(**over):
    body = {
        "target_url": URL,
        "title": "打印位置",
        "reason": "原文写的上限是 100，实际是 300，已和产品确认",
        "body": "# 正确的内容\n\n操作位置：设置-打印设置。",
    }
    body.update(over)
    return body


async def test_submit_reports_that_it_is_not_effective_yet(api_client, author, no_reingest):
    """⭐⭐ 回执要如实说**还没生效**。

    这条题原来断言的是相反的事（`applied is True`、note 里有「已生效」），
    因为那时提交就是覆盖公共知识库。现在提交只进审核队列，
    **回执必须说清楚**——说成"已生效"的话，用户改完再问一遍发现答案没变，
    只会认定这个功能是假的（而这一次它其实是对的，只是还没审）。
    """
    r = await api_client.post("/api/corrections", json=_payload())
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["applied"] is False
    assert data["chunks"] == 0
    assert "审核" in data["note"]
    assert data["correction"]["status"] == "pending"
    assert data["correction"]["target_url"] == URL


async def test_resubmitting_the_same_doc_updates_my_own_pending_one(
    api_client, author, no_reingest, maker
):
    """同一个人对同一篇再提一次是**更新自己那条待审的**，不是堆一串草稿。

    ⚠️ 注意这条不变量比原来**弱**：原来 `target_url` 是全表唯一，
    现在只有「已发布」之间唯一。两个**不同的人**给同一篇各提一条待审的，
    是完全正常的事（见 `test_two_users_may_both_submit_for_the_same_doc`）。
    """
    await api_client.post("/api/corrections", json=_payload())
    r = await api_client.post("/api/corrections", json=_payload(body="# 改第二遍"))
    assert r.status_code == 201, r.text

    async with maker() as s:
        rows = list(
            (
                await s.execute(
                    select(Correction).where(
                        Correction.target_url == URL, Correction.author_id == author
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].body == "# 改第二遍"


async def test_reason_is_required(api_client, author, no_reingest):
    """理由必填。这是**覆盖公共知识库**的东西，说不清理由的覆盖比不覆盖更危险。"""
    r = await api_client.post("/api/corrections", json=_payload(reason=""))
    assert r.status_code == 422


async def test_target_must_be_a_link(api_client, author, no_reingest):
    """target_url 是和语雀原文对齐的唯一键，填错的表现是「保存成功但一个字都没生效」。"""
    r = await api_client.post("/api/corrections", json=_payload(target_url="打印设置那篇"))
    assert r.status_code == 422


async def test_i_can_always_see_my_own_submission(api_client, author, no_reingest):
    """自己提的，任何状态都看得见。

    ⚠️ 「已发布的人人可见」那一半在 `test_correction_review.py` 里验——
    它要先真的走完发布，而这个文件里 `_reingest_one` 是假的。
    """
    await api_client.post("/api/corrections", json=_payload())
    r = await api_client.get("/api/corrections")
    assert r.status_code == 200
    assert any(c["target_url"] == URL for c in r.json())

    mine = await api_client.get("/api/corrections/mine")
    assert mine.status_code == 200
    assert [c["status"] for c in mine.json() if c["target_url"] == URL] == ["pending"]


async def test_delete_refuses_a_pending_one(api_client, editor, no_reingest, maker):
    """⭐ `DELETE` 是「撤销一条**已发布**的」，不是「删掉这一行」。

    还没发布的东西没有"撤销"可言——作者要收回自己那条走
    `PATCH {"action": "withdraw"}`。管理员对着一条 pending 点 DELETE
    应该拿到 409 而不是把它删掉：物理删掉的话，那条提交连同它的作者、
    时间、理由一起消失，而审核队列上刚才还显示着它。
    """
    cid = (await api_client.post("/api/corrections", json=_payload())).json()["correction"]["id"]
    r = await api_client.delete(f"/api/corrections/{cid}")
    assert r.status_code == 409, r.text

    # ⚠️ 按 **id** 查，不按 target_url 数行数。开发库是共享的，
    # 一次半路失败的历史运行会留下同 URL 的孤儿行（`author_id` 是
    # `ON DELETE SET NULL`，删用户删不掉它）——那正是 ISSUES I-3 那一族
    async with maker() as s:
        row = await s.get(Correction, uuid.UUID(cid))
    assert row is not None, "被物理删掉了"
    assert row.status == "pending"


async def test_requires_login(api_client):
    """未登录一律 401——勘误改的是所有人都会看到的内容。"""
    assert (await api_client.post("/api/corrections", json=_payload())).status_code == 401
    assert (await api_client.get("/api/corrections")).status_code == 401


async def test_any_logged_in_user_can_submit(api_client, maker, no_reingest):
    """⭐⭐ **提交人人可做。** 这条题是上一轮那道 `test_writing_needs_admin` 的反面。

    上一轮把 POST 收紧成管理员，安全了，但**把用户挡在了纠错之外**——
    而发现原文写错的恰恰是天天在用的那些人，不是管理员。
    正确的切法不是"谁能提交"，是"提交之后要不要过审"。

    ⚠️ 用 Bearer 而不是 cookie：`api_client` 的罐子里可能已经有别人的登录态，
    而 `extract_token` **先看 cookie 再看 Authorization**（同 test_admin_api）。
    """
    email = f"plain-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        u = User(email=email, password_hash="x", is_active=True, is_admin=False)
        s.add(u)
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(u.id)}"}
        uid = u.id

    try:
        api_client.cookies.clear()
        r = await api_client.post("/api/corrections", json=_payload(), headers=headers)
        assert r.status_code == 201, r.text
        # ⚠️ 但它**没有生效**：落的是 pending
        assert r.json()["correction"]["status"] == "pending"
        assert r.json()["applied"] is False
        assert (await api_client.get("/api/corrections", headers=headers)).status_code == 200
    finally:
        async with maker() as s:
            await s.execute(delete(Correction).where(Correction.author_id == uid))
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


# ---------- 两路合并 ----------


def test_db_wins_over_file():
    """同一篇两边都有时，以数据库那条为准。

    理由是时序：文件那条是上一次提交时的想法，数据库那条是刚刚在网页上写的。
    """
    from pathlib import Path

    from copilot.ingest.corrections import Correction as CorrectionFile
    from copilot.ingest.corrections import merge_corrections

    from_file = {
        URL: CorrectionFile(path=Path("a.md"), target_url=URL, reason="旧", body="文件版")
    }
    from_db = {URL: CorrectionFile(path=None, target_url=URL, reason="新", body="网页版")}

    merged = merge_corrections(from_file, from_db)
    assert merged[URL].body == "网页版"


def test_db_correction_has_a_readable_name():
    """数据库那条没有文件，`.name` 不能炸——cli 的告警里要用它。"""
    from copilot.ingest.corrections import Correction as CorrectionFile

    c = CorrectionFile(path=None, target_url=URL, reason="r", title="打印位置")
    assert "打印位置" in c.name


# ---------- 邀请码（管理员） ----------


async def test_invites_need_admin(api_client, author):
    """非管理员一律 403。留个自助升级的口子，邀请制就形同虚设了。"""
    assert (await api_client.get("/api/invites")).status_code == 403
    assert (await api_client.post("/api/invites", json={"count": 1})).status_code == 403


async def test_admin_can_generate(api_client, author, maker):
    """管理员能生成，返回的是全部未使用的码。"""
    async with maker() as s:
        u = await s.get(User, author)
        u.is_admin = True
        await s.commit()

    r = await api_client.post("/api/invites", json={"count": 3})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["unused"] >= 3
    assert len(data["codes"]) >= 3

    r2 = await api_client.get("/api/invites")
    assert r2.status_code == 200
    assert set(data["codes"]) <= set(r2.json()["codes"])


async def test_count_is_capped(api_client, author, maker):
    """一次最多 20 个——手滑打成 1000 该被挡下来。"""
    async with maker() as s:
        u = await s.get(User, author)
        u.is_admin = True
        await s.commit()
    assert (await api_client.post("/api/invites", json={"count": 1000})).status_code == 422


async def test_me_reports_admin_flag(api_client, author, maker):
    """前端靠 /me 里的 is_admin 决定要不要显示那个入口。"""
    assert (await api_client.get("/api/auth/me")).json()["is_admin"] is False
    async with maker() as s:
        u = await s.get(User, author)
        u.is_admin = True
        await s.commit()
    assert (await api_client.get("/api/auth/me")).json()["is_admin"] is True
