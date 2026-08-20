"""请求追踪 + 👍👎（M11 P1 / P2）。

**这两件事是一张表，测试也放在一起。** 分表的话一个 👎 就只是个计数器——
你复现不了当时检索到了什么、rerank 打了多少分。合成一张表，
「用户差评 → 找失败原因 → 加进评测集」这个闭环才转得起来，
而这一组题量的就是那个闭环的每一节：

    问一句 → 表里有一行，且带着检索命中和耗时     （验收标准第 3 条）
    点一个 👎 → 从这条反馈能翻出当时的完整链路     （验收标准第 4 条）
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import ask, parts
from sqlalchemy import delete, select

from copilot.db.models import RequestTrace


@pytest.fixture(autouse=True)
async def clean_traces(maker, logged_in):
    """用完把自己造的行删掉，别污染真实台账。"""
    yield
    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.user_id == logged_in))
        await s.commit()


async def _traces(maker, user_id):
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(RequestTrace)
                    .where(RequestTrace.user_id == user_id)
                    .order_by(RequestTrace.created_at)
                )
            ).scalars()
        )


async def test_trace_row_has_the_whole_chain(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    """一行里该有的：路由、命中几块、最高分、首字时间、token、答案长度。

    ⭐ **`chunk_count` 和 `top_score` 必须都在。** 只有它们俩合起来才分得出
    「压根没召回」和「召回了但都不相关」——这两种失败的修法完全不同，
    而只看答案文本，它们长得一模一样（都是一句「知识库暂无此内容」）。
    """
    _title, body = public_chunk
    assert (await ask(api_client, body)).status_code == 200

    rows = await _traces(maker, logged_in)
    assert len(rows) == 1, f"一条请求该有且只有一行，实际 {len(rows)}"
    row = rows[0]

    assert row.route == "direct"
    assert row.question == body
    assert row.chunk_count == 1, "假 reranker 只放行第一块"
    assert row.top_score is not None and row.top_score > 0
    assert row.ttfb_ms is not None, "首字时间没记上"
    assert row.total_ms is not None and row.total_ms >= row.ttfb_ms
    assert row.tokens > 0, "token 要算上送进去的上下文，不只是答案"
    assert row.answer_chars > 0
    assert row.ok is True and row.error is None
    assert row.conversation_id is not None
    assert row.message_id is not None, "得指得到那条回答，否则翻历史时点不了赞踩"


async def test_small_talk_is_traced_too(api_client, logged_in, fake_providers, maker):
    """⭐ 寒暄也记一行。

    它是这张表里最便宜的一类样本：一次模型调用都不花，却是唯一能看出
    「有多少提问其实只是打招呼」的地方。这一类占比一高，
    该做的是引导用户怎么提问，而不是继续调模型。
    """
    assert (await ask(api_client, "你好")).status_code == 200

    rows = await _traces(maker, logged_in)
    assert [r.route for r in rows] == ["canned"]
    assert rows[0].tokens == 0, "固定回复一个字都没送进模型"
    assert rows[0].chunk_count == 0
    assert rows[0].model is None


async def test_stream_carries_the_trace_id(api_client, logged_in, fake_providers, public_chunk):
    """trace id 要**在流里**发出来，而且得在正文之前。

    用户点踩是在读到烂答案的第一秒，那时候流还没结束——
    等结束再发，那一秒就没有按钮可点。
    """
    _title, body = public_chunk
    r = await ask(api_client, body)
    frames = parts(r.text)

    kinds = [f.get("type") for f in frames]
    assert "data-trace" in kinds
    trace_at = kinds.index("data-trace")
    assert "text-start" not in kinds or trace_at < kinds.index("text-start"), (
        "trace id 必须排在正文之前"
    )

    sent = next(f for f in frames if f["type"] == "data-trace")
    uuid.UUID(sent["data"]["id"])  # 解不出来就抛，正好


async def test_thumbs_down_links_back_to_the_whole_chain(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    """⭐ 验收标准第 4 条：点一条 👎，能从它直接翻出当时的完整链路。

    **这一条就是「不建独立 feedback 表」那个决定的验收。**
    分表的话这里能断言的只有「计数器 +1」。
    """
    _title, body = public_chunk
    r = await ask(api_client, body)
    trace_id = next(f for f in parts(r.text) if f["type"] == "data-trace")["data"]["id"]

    vote = await api_client.post(
        "/api/feedback",
        json={"traceId": trace_id, "vote": "down", "reason": "should_know"},
    )
    assert vote.status_code == 200

    recent = await api_client.get("/api/feedback/recent")
    assert recent.status_code == 200
    row = next(x for x in recent.json() if x["traceId"] == trace_id)

    # 一条差评点开，当时的全链路都在
    assert row["vote"] == "down"
    assert row["reason"] == "should_know"
    assert row["question"] == body
    assert row["route"] == "direct"
    assert row["chunks"] == 1
    assert row["topScore"] is not None
    assert row["totalMs"] is not None


async def test_vote_can_be_changed(api_client, logged_in, fake_providers, public_chunk, maker):
    """先点👍再改成👎是很常见的（读完发现有一句错了）。留下的该是最后那个意思。"""
    _title, body = public_chunk
    r = await ask(api_client, body)
    trace_id = next(f for f in parts(r.text) if f["type"] == "data-trace")["data"]["id"]

    await api_client.post("/api/feedback", json={"traceId": trace_id, "vote": "up"})
    await api_client.post(
        "/api/feedback", json={"traceId": trace_id, "vote": "down", "reason": "wrong"}
    )

    async with maker() as s:
        row = await s.get(RequestTrace, uuid.UUID(trace_id))
        assert row.feedback == "down"
        assert row.feedback_reason == "wrong"
        assert row.feedback_at is not None


async def test_up_vote_clears_a_stale_reason(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    """👍 不带原因。留着上次点👎时选的原因会更糟——
    表里会出现「vote=up, reason=答错了」这种自相矛盾的行。"""
    _title, body = public_chunk
    r = await ask(api_client, body)
    trace_id = next(f for f in parts(r.text) if f["type"] == "data-trace")["data"]["id"]

    await api_client.post(
        "/api/feedback", json={"traceId": trace_id, "vote": "down", "reason": "wrong"}
    )
    await api_client.post("/api/feedback", json={"traceId": trace_id, "vote": "up"})

    async with maker() as s:
        row = await s.get(RequestTrace, uuid.UUID(trace_id))
        assert row.feedback == "up"
        assert row.feedback_reason is None


async def test_cannot_vote_on_someone_elses_trace(api_client, logged_in, maker):
    """别人的 trace id 一律当**不存在**（404 而非 403）。

    和会话接口同一个理由，而且这里更要紧：trace id 是会随 SSE 发到浏览器里的，
    比会话 id 更容易被顺手拿去试。
    """
    other = uuid.uuid4()
    async with maker() as s:
        s.add(
            RequestTrace(
                id=other, user_id=None, route="direct", question="别人的那一轮", mode="fast"
            )
        )
        await s.commit()

    r = await api_client.post("/api/feedback", json={"traceId": str(other), "vote": "down"})
    assert r.status_code == 404

    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.id == other))
        await s.commit()


async def test_history_carries_trace_id_so_you_can_vote_later(
    api_client, logged_in, fake_providers, public_chunk
):
    """⭐ 翻历史也要能点赞踩。

    trace id 平时是随 SSE 发的，刷新一次就没了——而用户回头看到一条
    当时没细看的烂答案才想点踩，是最常见的情形。所以 `list_messages`
    要把 trace id 和已有的投票一起带出来。
    """
    _title, body = public_chunk
    r = await ask(api_client, body)
    frames = parts(r.text)
    conv_id = next(f for f in frames if f["type"] == "data-conversation")["data"]["id"]
    trace_id = next(f for f in frames if f["type"] == "data-trace")["data"]["id"]

    await api_client.post("/api/feedback", json={"traceId": trace_id, "vote": "up"})

    msgs = (await api_client.get(f"/api/conversations/{conv_id}/messages")).json()
    answer = next(m for m in msgs if m["role"] == "assistant")
    assert answer["trace_id"] == trace_id
    assert answer["feedback"] == "up", "点过的那一侧刷新后要保持亮着"

    # 提问那条没有 trace（trace 记的是「这一轮」，挂在回答上）
    question_row = next(m for m in msgs if m["role"] == "user")
    assert question_row["trace_id"] is None


async def test_trace_failure_never_breaks_the_answer(
    api_client, logged_in, fake_providers, public_chunk, monkeypatch
):
    """⚠️ **写台账失败绝不能影响回答。**

    台账记漏一次的代价，远小于「答案已经生成好了、却因为写台账报错
    而在用户面前变成一句报错」。
    """
    from copilot.api import trace as trace_module

    def broken_session():
        raise RuntimeError("连接池炸了")

    # 打断的是**真正会炸的那一层**（开会话），而不是把 `save` 换成一个会抛的桩：
    # 后者验的是「调用方接没接住」，而 save 的契约是**它自己永远不抛**。
    # 契约写在哪一层，就在哪一层验。
    monkeypatch.setattr(trace_module, "SessionLocal", broken_session)

    _title, body = public_chunk
    r = await ask(api_client, body)
    assert r.status_code == 200
    # 假模型是**一个字一个字**吐的，所以不能直接在 r.text 里找整句——
    # 得先把 text-delta 拼回来
    answer = "".join(
        f["delta"] for f in parts(r.text) if f.get("type") == "text-delta"
    )
    assert "先绑定物流账号" in answer, "答案该照常出来"
    assert "[DONE]" in r.text, "流也要正常收尾，不能卡在半截"
