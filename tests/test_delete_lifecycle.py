"""删一段会话，到底有哪些东西跟着没了（M13 P7）。

⭐ **这一组题要消灭的是「不知道会不会删」这个状态本身。**

删除链路横跨四张表加一个文件系统，而它们的规则各不相同：

    messages         ON DELETE CASCADE      跟着删
    exports/*.xlsx   路由里手动 unlink      跟着删
    request_trace    **没有外键，不删**      刻意留下
    feedback         在 trace 那一行上       跟着 trace 留下

前两条已经有测试守着（见 `test_api_chat.py`）。**这里补的是后两条**——
它们是「决定了不删」，而一个没有测试的「决定了不删」和「忘了删」
在代码上长得一模一样。半年后谁也说不清当初是哪一种，
于是要么不敢动，要么顺手"修"掉一个本来是对的行为。

⚠️ **为什么 trace 不跟着删**（决定，不是疏忽）：
它记的是**「系统那天表现如何」**，不是「他说过什么」。
一条差评的价值在于能复现当时的检索链路——而用户删掉那段会话的动机，
很多时候恰恰是「这轮答得不好」。跟着删的话，最该留下的样本
会被最想让你看到它的那个动作抹掉。

被留下的那一行里没有答案正文（`TraceDraft.answer` 不落库），
问题原文截到 2000 字，且 30/90 天后由 `copilot prune-traces` 清掉
（见 `cli.py` 的保留策略和 `test_prune_traces.py`）。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import PASSWORD, ask, parts
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.db.models import Conversation, InviteCode, Message, RequestTrace, User


@pytest.fixture(autouse=True)
async def clean_traces(maker, logged_in):
    yield
    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.user_id == logged_in))
        await s.commit()


async def _one_turn(api_client, body: str) -> tuple[str, str]:
    """问一句，返回 (conversation_id, trace_id)。"""
    r = await ask(api_client, body)
    assert r.status_code == 200
    got = parts(r.text)
    conv = next(p for p in got if p["type"] == "data-conversation")["data"]["id"]
    trace = next(p for p in got if p["type"] == "data-trace")["data"]["id"]
    return conv, trace


async def test_delete_conversation_cleanup(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """删会话之后，四样东西各自的下场都要**明确**。

    这条用例的断言里有一半是「它还在」——那不是漏写，是决定。
    """
    _title, body = public_chunk
    conv_id, trace_id = await _one_turn(api_client, body)

    async with maker() as s:
        msgs = list(
            (
                await s.execute(
                    select(Message.id).where(Message.conversation_id == uuid.UUID(conv_id))
                )
            ).scalars()
        )
        assert msgs, "这一轮该有消息落库，否则下面的断言证明不了什么"
        trace = await s.get(RequestTrace, uuid.UUID(trace_id))
        assert trace is not None and trace.conversation_id == uuid.UUID(conv_id)

    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 204

    async with maker() as s:
        # ---- 跟着删的 ----
        assert await s.get(Conversation, uuid.UUID(conv_id)) is None
        left = list(
            (
                await s.execute(
                    select(Message.id).where(Message.conversation_id == uuid.UUID(conv_id))
                )
            ).scalars()
        )
        assert left == [], "messages 该被 ON DELETE CASCADE 带走"

        # ---- 刻意留下的 ----
        trace = await s.get(RequestTrace, uuid.UUID(trace_id))
        assert trace is not None, (
            "request_trace 不跟着删是**决定**：它记的是系统那天表现如何，"
            "而用户删会话的动机常常正是「这轮答得不好」——"
            "跟着删就等于让最该留下的样本被最想让你看到它的动作抹掉"
        )
        # ⚠️ 于是这一列会指向一段已经不存在的会话。**这是容忍的**，
        # 不是 bug：读的时候当它不存在即可（models.py 里 message_id 那段注释
        # 写的是同一件事）。加外键 + SET NULL 会把「哪一轮被踩了」也一起抹掉
        assert trace.conversation_id == uuid.UUID(conv_id)
        assert trace.message_id is not None


async def test_feedback_survives_conversation_delete(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """⭐ 差评在会话删掉之后仍然查得到，**这正是 trace 不跟着删的理由**。

    「用户差评 → 找失败原因 → 加进评测集」这个闭环有时要跨好几周。
    中间用户清一次会话列表就把样本清空的话，这个闭环根本转不动。
    """
    _title, body = public_chunk
    conv_id, trace_id = await _one_turn(api_client, body)

    r = await api_client.post(
        "/api/feedback", json={"traceId": trace_id, "vote": "down", "reason": "wrong"}
    )
    assert r.status_code == 200, r.text

    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 204

    recent = await api_client.get("/api/feedback/recent")
    assert recent.status_code == 200
    assert trace_id in [row["traceId"] for row in recent.json()], "会话删掉之后差评就查不到了"


async def test_cannot_touch_other_users_trace(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """⭐ 别人的 trace 一律当**不存在**（404，不是 403）。

    trace id 会随 SSE 发到浏览器里，比会话 id 更容易被顺手试；
    403 等于确认「这个 id 是有效的」。

    ⚠️ 今天没有「删除 trace」的接口，所以能测的是**唯一那个能改到它的入口**：
    投票。将来真加了删除接口，这条用例就是它该照抄的形状。
    """
    _title, body = public_chunk
    _conv_id, trace_id = await _one_turn(api_client, body)

    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    await api_client.post("/api/auth/logout")
    reg = await api_client.post(
        "/api/auth/register",
        json={
            "email": f"other-{uuid.uuid4().hex[:8]}@test.local",
            "password": PASSWORD,
            "inviteCode": code,
        },
    )
    assert reg.status_code == 201
    other_id = uuid.UUID(reg.json()["id"])
    try:
        r = await api_client.post(
            "/api/feedback", json={"traceId": trace_id, "vote": "down", "reason": "wrong"}
        )
        assert r.status_code == 404, "别人给我的 trace 打了分"

        # 也不该在他的「最近反馈」里看到我的那一行
        recent = await api_client.get("/api/feedback/recent")
        assert recent.status_code == 200
        assert trace_id not in [row["traceId"] for row in recent.json()]

        # 我的那一行没被改动
        async with maker() as s:
            mine = await s.get(RequestTrace, uuid.UUID(trace_id))
            assert mine is not None and mine.feedback is None
    finally:
        async with maker() as s:
            await s.execute(delete(InviteCode).where(InviteCode.code == code))
            await s.execute(delete(User).where(User.id == other_id))
            await s.commit()


async def test_deleting_the_user_keeps_the_trace_but_drops_the_owner(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """删号之后台账留着，但不再指向任何人（`user_id` 是 ON DELETE SET NULL）。

    统计和复盘看的是「系统那天表现如何」，不该因为某个账号被删就凭空
    少掉一段历史；而「是谁问的」本来就该跟着账号一起消失。

    ⚠️ 和邀请码那一条对照着看（`test_api_auth.py`）：那边的结论正好相反——
    `used_by` 被清空**不能**影响「这个码用过没有」。两处的判据不同：
    台账要的是「那天发生过什么」，邀请码要的是「这个码不能再用」。
    """
    _title, body = public_chunk
    _conv_id, trace_id = await _one_turn(api_client, body)

    async with maker() as s:
        await s.execute(delete(User).where(User.id == logged_in))
        await s.commit()

    async with maker() as s:
        trace = await s.get(RequestTrace, uuid.UUID(trace_id))
        assert trace is not None, "删号把台账一起带走了"
        assert trace.user_id is None, "user_id 该被 SET NULL"
        assert trace.question == body, "问题原文要留着，否则这一行没法复盘"
