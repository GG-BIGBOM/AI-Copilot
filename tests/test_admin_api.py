"""只读管理台后端（M15-A）。

这一组盯三件事，别的都是次要的：

1. **每个 `/api/admin/*` 都在服务端卡管理员。** 前端那道 `/admin` guard 只管
   体验，挡不住任何一个会开控制台的人。漏一个接口的表现是：任何注册用户
   都能拉到全站的用户名单和问题原文，而页面上看不出任何异常。
2. **概览页不出现问题原文。** 一个人问过的问题连起来就是他在处理哪个客户、
   哪个故障。详情页给全文是管理员的一次明确动作，仪表盘顺带摊开不是。
3. **分页在 SQL 里，上限写死在服务端。** 客户端传 `limit=100000` 必须被拒，
   否则分页等于没有——而这台机器一共 1.6GB 内存，OOM 会把问答服务一起带走。

⚠️ 全站聚合的用例一律用**增量断言**（先取一次基线，插入几行，再取一次比差）。
开发库里攒着几百行别人造的台账，写死等式的话，测试会在某个无关的用例
往库里多塞一行时莫名其妙地红。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from copilot.auth.security import create_access_token
from copilot.db.models import Conversation, Document, Message, RequestTrace, User


@pytest.fixture
async def admin_headers(maker):
    """一个管理员，以及打它接口用的 Bearer 头。

    ⚠️ 用 Bearer 而不是 cookie：`api_client` 的罐子里往往已经有别人的登录态，
    而 `extract_token` **先看 cookie 再看 Authorization**——不清干净的话，
    「管理员能看到」这条断言可能是普通用户跑出来的。
    """
    email = f"root-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        u = User(email=email, password_hash="x", is_active=True, is_admin=True)
        s.add(u)
        await s.commit()
        token = create_access_token(u.id)
        admin_id = u.id

    yield {"Authorization": f"Bearer {token}"}, admin_id

    async with maker() as s:
        await s.execute(delete(User).where(User.id == admin_id))
        await s.commit()


@pytest.fixture
async def traces(maker, logged_in):
    """往台账里塞已知的几行，用完删干净。"""
    made: list[uuid.UUID] = []

    async def add(**kw) -> uuid.UUID:
        row_id = uuid.uuid4()
        kw.setdefault("route", "direct")
        kw.setdefault("question", f"问题-{uuid.uuid4().hex[:8]}")
        kw.setdefault("created_at", datetime.now(UTC) - timedelta(hours=1))
        async with maker() as s:
            s.add(RequestTrace(id=row_id, user_id=logged_in, **kw))
            await s.commit()
        made.append(row_id)
        return row_id

    yield add

    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.id.in_(made)))
        await s.commit()


# ---------- 1. 服务端鉴权 ----------

ENDPOINTS = ("/api/admin/overview", "/api/admin/users", "/api/admin/feedback")


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_admin_endpoints_need_a_login(api_client, path):
    api_client.cookies.clear()
    r = await api_client.get(path)
    assert r.status_code == 401, r.text


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_a_normal_user_is_refused(api_client, logged_in, path):
    """⭐ 普通用户 403。

    ⚠️ 这里**故意是 403 不是 404**：管理接口存不存在不是秘密，藏起来只会
    让排查变难。私有**数据**的越权才用 404（`/api/images/`），
    那里 404 和 403 的区别本身就会泄露「这个 id 存在」。
    """
    r = await api_client.get(path)
    assert r.status_code == 403, r.text


async def test_a_deactivated_admin_loses_access(api_client, maker, admin_headers):
    """停用之后手里的旧 token 立刻失效——判定在 `get_current_user_optional`。"""
    headers, admin_id = admin_headers
    api_client.cookies.clear()
    assert (await api_client.get("/api/admin/overview", headers=headers)).status_code == 200

    async with maker() as s:
        (await s.get(User, admin_id)).is_active = False
        await s.commit()

    assert (await api_client.get("/api/admin/overview", headers=headers)).status_code == 401


async def test_the_user_detail_endpoint_is_guarded_too(api_client, logged_in):
    """带路径参数的那个也不能漏——它给的是别人的问题原文。"""
    r = await api_client.get(f"/api/admin/users/{logged_in}")
    assert r.status_code == 403, r.text


# ---------- 2. 概览 ----------


async def test_overview_counts_what_was_added(api_client, admin_headers, traces):
    """增量断言：插进去几行，概览就该多几行。"""
    headers, _ = admin_headers
    api_client.cookies.clear()

    before = (await api_client.get("/api/admin/overview?range=7d", headers=headers)).json()

    await traces(answer_source="kb", ttfb_ms=200, total_ms=900, tokens=100, feedback="up")
    await traces(answer_source="no_answer", ttfb_ms=100, total_ms=300, tokens=10, feedback="down")

    after = (await api_client.get("/api/admin/overview?range=7d", headers=headers)).json()

    assert after["questions"] - before["questions"] == 2
    assert after["thumbs_up"] - before["thumbs_up"] == 1
    assert after["thumbs_down"] - before["thumbs_down"] == 1
    assert after["by_source"].get("kb", 0) - before["by_source"].get("kb", 0) == 1


async def test_overview_uses_the_same_bypass_rule_as_the_cli(api_client, admin_headers, traces):
    """⭐ 「越过工具直答」的判据只有一处（`metrics.summarize`）。

    Agent 路 + 一个工具都没调 + 写出了有出处样子的答案，才算违规。
    tools 为空**不等于**违规——追问和寒暄都不该调工具，它单独报一个数。
    """
    headers, _ = admin_headers
    api_client.cookies.clear()
    before = (await api_client.get("/api/admin/overview", headers=headers)).json()

    await traces(route="agent", tools=[], answer_source="kb")  # 违规
    await traces(route="agent", tools=[], answer_source="general_knowledge")  # 追问，不违规
    await traces(route="agent", tools=["检索知识库"], answer_source="kb")  # 正常

    after = (await api_client.get("/api/admin/overview", headers=headers)).json()

    assert after["tool_bypass"] - before["tool_bypass"] == 1
    assert after["agent_requests"] - before["agent_requests"] == 3
    assert after["agent_without_tools"] - before["agent_without_tools"] == 2


# 概览里允许出现的**文本**字段，一个不多。别的一律是数字。
# ⚠️ 这份清单就是断言本身：往概览加一个字符串字段（"最近的问题"、
# "越线的那几条长什么样"）会让下面那条测试当场红——而那正是要拦的事。
_OVERVIEW_TEXT_FIELDS = {"range", "since", "feedback_rate"}


async def test_overview_never_leaks_the_question_text(api_client, admin_headers, traces):
    """⭐⭐ 概览页一个字的问题原文都不给。见 `metrics.Summary` 的注释。

    两道断言，缺一不可：

      具体的那道  刚插进去的这句问题不许出现在响应里
      结构的那道  响应里**只有** `_OVERVIEW_TEXT_FIELDS` 这几个文本字段，
                  也没有任何列表。只写第一道是拦不住的——泄漏往往是
                  「顺手加一个最近 5 条」，而那 5 条里未必有你造的这行
    """
    headers, _ = admin_headers
    api_client.cookies.clear()
    secret = f"星辰电商的对账口径-{uuid.uuid4().hex[:8]}"
    # 违规那一类是最容易顺手带出原文的：命令行版就打了问题前 40 个字
    await traces(route="agent", tools=[], answer_source="kb", question=secret)

    r = await api_client.get("/api/admin/overview", headers=headers)
    body = r.json()

    assert secret not in r.text

    texts = {k for k, v in body.items() if isinstance(v, str)}
    assert texts == _OVERVIEW_TEXT_FIELDS, f"概览多了/少了文本字段：{texts}"
    assert not [k for k, v in body.items() if isinstance(v, list)], "概览不该出现列表"
    # by_source 是「来源 → 条数」，值必须是数
    assert all(isinstance(v, int) for v in body["by_source"].values())


async def test_an_unknown_range_is_refused(api_client, admin_headers):
    headers, _ = admin_headers
    api_client.cookies.clear()
    r = await api_client.get("/api/admin/overview?range=1y", headers=headers)
    assert r.status_code == 422


# ---------- 3. 用户列表 ----------


async def test_users_page_is_capped_and_paginated(api_client, admin_headers):
    headers, _ = admin_headers
    api_client.cookies.clear()

    first = (await api_client.get("/api/admin/users?limit=1&offset=0", headers=headers)).json()
    second = (await api_client.get("/api/admin/users?limit=1&offset=1", headers=headers)).json()

    assert len(first["items"]) == 1
    assert first["total"] >= 2
    assert first["items"][0]["id"] != second["items"][0]["id"], "offset 没生效，两页是同一行"


async def test_the_page_size_cap_is_server_side(api_client, admin_headers):
    """⭐ 客户端说了不算。上限没在服务端的话，分页等于没做。"""
    headers, _ = admin_headers
    api_client.cookies.clear()
    r = await api_client.get("/api/admin/users?limit=100000", headers=headers)
    assert r.status_code == 422


async def test_users_can_be_filtered_by_email(api_client, maker, admin_headers, logged_in):
    headers, _ = admin_headers
    api_client.cookies.clear()
    async with maker() as s:
        email = (await s.get(User, logged_in)).email

    got = (await api_client.get(f"/api/admin/users?q={email}", headers=headers)).json()

    assert [i["email"] for i in got["items"]] == [email]
    assert got["total"] == 1


async def test_a_users_row_carries_their_usage(api_client, maker, admin_headers, logged_in, traces):
    headers, _ = admin_headers
    api_client.cookies.clear()
    await traces(route="agent", tools=["检索知识库"], answer_source="kb", ttfb_ms=1234)
    await traces(answer_source="no_answer", no_answer=True, feedback="down")

    async with maker() as s:
        email = (await s.get(User, logged_in)).email
    row = (await api_client.get(f"/api/admin/users?q={email}", headers=headers)).json()["items"][0]

    assert row["requests"] == 2
    assert row["agent_requests"] == 1
    assert row["no_answer"] == 1
    assert row["thumbs_down"] == 1
    assert row["ttfb_p95"] == 1234
    assert row["last_active_at"] is not None


# ---------- 4. 用户详情 ----------


async def test_user_detail_shows_the_questions(api_client, admin_headers, logged_in, traces):
    """详情页**给**问题原文——管理员点进来是一次明确的动作。"""
    headers, _ = admin_headers
    api_client.cookies.clear()
    question = f"退货入库怎么走-{uuid.uuid4().hex[:8]}"
    await traces(question=question, answer_source="kb", ttfb_ms=300, total_ms=1000)

    got = (await api_client.get(f"/api/admin/users/{logged_in}", headers=headers)).json()

    assert got["questions"] >= 1
    assert question in [r["question"] for r in got["recent"]]
    assert got["trend"], "趋势是空的"
    assert got["ttfb"]["p50"] is not None


async def test_user_detail_lists_their_documents(
    api_client, maker, admin_headers, logged_in, flagship_id
):
    headers, _ = admin_headers
    api_client.cookies.clear()
    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        s.add(
            Document(
                owner_id=logged_in,
                knowledge_space_id=flagship_id,
                source_type="upload",
                title=f"客户约定-{tag}",
                content_hash=uuid.uuid4().hex,
                status="failed",
                error="解析失败",
            )
        )
        await s.commit()

    try:
        got = (await api_client.get(f"/api/admin/users/{logged_in}", headers=headers)).json()
        mine = [d for d in got["documents"] if tag in d["title"]]
        assert mine and mine[0]["status"] == "failed"
        assert mine[0]["error"] == "解析失败", "解析失败的原因要看得到，否则排查不了"
    finally:
        async with maker() as s:
            await s.execute(delete(Document).where(Document.title.like(f"%{tag}%")))
            await s.commit()


async def test_an_unknown_user_is_404(api_client, admin_headers):
    headers, _ = admin_headers
    api_client.cookies.clear()
    r = await api_client.get(f"/api/admin/users/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404


# ---------- 5. 反馈中心 ----------


@pytest.fixture
async def a_bad_answer(maker, logged_in, flagship_id, traces):
    """一条差评，连着它的会话、消息和全链路数据。"""
    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        conv = Conversation(user_id=logged_in, knowledge_space_id=flagship_id, title=f"c-{tag}")
        s.add(conv)
        await s.flush()
        msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=f"这是那条被踩的回答-{tag}",
            citations=[{"n": 1, "title": "电子面单"}],
            images=[{"n": 1, "url": "/images/ab/cd.png"}],
        )
        s.add(msg)
        await s.commit()
        conv_id, msg_id = conv.id, msg.id

    trace_id = await traces(
        route="agent",
        tools=["检索知识库"],
        answer_source="kb",
        question=f"这一轮问了什么-{tag}",
        conversation_id=conv_id,
        message_id=msg_id,
        chunk_count=5,
        top_score=0.87,
        model="deepseek-chat",
        ttfb_ms=900,
        total_ms=4200,
        feedback="down",
        feedback_reason="wrong",
        feedback_at=datetime.now(UTC),
    )

    yield tag, trace_id

    async with maker() as s:
        await s.execute(delete(Message).where(Message.id == msg_id))
        await s.execute(delete(Conversation).where(Conversation.id == conv_id))
        await s.commit()


async def test_feedback_row_carries_the_whole_chain(api_client, admin_headers, a_bad_answer):
    """⭐ 这正是「👍👎 不另建表」换来的东西：点开一条差评能看到全链路。

    分表且不关联的话，这里只剩一个计数器——复现不了当时检索到什么、
    调了什么工具、rerank 打了多少分。
    """
    headers, _ = admin_headers
    api_client.cookies.clear()
    tag, trace_id = a_bad_answer

    page = (await api_client.get("/api/admin/feedback?range=7d", headers=headers)).json()
    row = next(r for r in page["items"] if r["id"] == str(trace_id))

    assert row["feedback"] == "down"
    assert row["feedback_reason"] == "wrong"
    assert tag in row["question"]
    assert row["answer"] and tag in row["answer"], "答案正文要从 messages 表捞出来"
    assert row["citations"] and row["images"]
    assert row["tools"] == ["检索知识库"]
    assert row["chunk_count"] == 5 and row["top_score"] == 0.87
    assert row["knowledge_space"] == "flagship"
    assert row["model"] == "deepseek-chat"
    assert row["user_email"]


async def test_feedback_defaults_to_the_bad_ones(api_client, admin_headers, traces):
    headers, _ = admin_headers
    api_client.cookies.clear()
    up_id = await traces(feedback="up", feedback_at=datetime.now(UTC))
    down_id = await traces(feedback="down", feedback_at=datetime.now(UTC))

    ids = [r["id"] for r in (
        await api_client.get("/api/admin/feedback?limit=200", headers=headers)
    ).json()["items"]]

    assert str(down_id) in ids
    assert str(up_id) not in ids, "默认只看差评——好评没什么可排查的"

    both = (await api_client.get("/api/admin/feedback?kind=all&limit=200", headers=headers)).json()
    assert str(up_id) in [r["id"] for r in both["items"]]


async def test_feedback_can_be_filtered_by_reason(api_client, admin_headers, traces):
    headers, _ = admin_headers
    api_client.cookies.clear()
    wrong = await traces(feedback="down", feedback_reason="wrong", feedback_at=datetime.now(UTC))
    no_img = await traces(
        feedback="down", feedback_reason="no_image", feedback_at=datetime.now(UTC)
    )

    ids = [r["id"] for r in (
        await api_client.get("/api/admin/feedback?reason=no_image&limit=200", headers=headers)
    ).json()["items"]]

    assert str(no_img) in ids
    assert str(wrong) not in ids


async def test_a_feedback_row_survives_a_deleted_message(api_client, maker, admin_headers, traces):
    """⚠️ `message_id` 没有外键（消息删了 trace 要留着），所以它可能指向一条
    已经不存在的消息。读不到就当没有——不能让整页 500。"""
    headers, _ = admin_headers
    api_client.cookies.clear()
    trace_id = await traces(
        feedback="down", feedback_at=datetime.now(UTC), message_id=uuid.uuid4()
    )

    page = (await api_client.get("/api/admin/feedback?limit=200", headers=headers)).json()
    row = next(r for r in page["items"] if r["id"] == str(trace_id))

    assert row["answer"] is None
