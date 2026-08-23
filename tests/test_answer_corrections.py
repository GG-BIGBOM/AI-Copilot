"""答案纠错：提交 → 审核 → 发布（M16）。

这一组守的是 M16 的四条完成条件，一条一节：

    1. **未审核不进 RAG**   提交只是排队，一个字都不许影响检索
    2. **发布同空间生效**   旗舰版发布的答案不许出现在企业版的会话里
    3. **不经 LLM 改写**    命中人写定的答案就原样返回，不再交给模型重述
    4. **修订可追溯**       每改一版留一行，谁改的、为什么

⚠️ 还有一条同样重要、但不在验收清单上的：**M16 之前「答错了，我来改」是
任何登录用户提交即公共生效、无人审核的**——那是一个任何注册用户都能往公共
知识库里塞内容的入口。这一组里有两条测试专门钉死它现在被堵上了。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from chat_helpers import FakeEmbedder
from sqlalchemy import delete, select
from test_isolation import PassThroughReranker

from copilot import corrections_flow as flow
from copilot.auth.security import create_access_token
from copilot.db.models import (
    AnswerCorrection,
    Chunk,
    Conversation,
    Document,
    Message,
    RequestTrace,
    User,
    VerifiedAnswer,
    VerifiedAnswerRevision,
)
from copilot.retrieve import search

QUESTION = "京东电子面单模板要在哪里设置"
BAD_ANSWER = "在【设置-打印】里选模板。"
GOOD_ANSWER = "在【物流管理-电子面单-模板管理】里选模板，先绑定京东账号。"


class BoomLLM:
    """一被调用就炸。用来证明「命中标准答案时一次模型都没调」。"""

    model = "boom"

    def stream_parts(self, messages):  # noqa: ANN001, ARG002
        raise AssertionError("命中标准答案之后还调了模型——那就等于让它重写了人写定的答案")

    def complete(self, messages, **kw):  # noqa: ANN001, ARG002
        raise AssertionError("命中标准答案之后还调了模型")


@pytest.fixture
async def admin_headers(maker):
    email = f"root-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        u = User(email=email, password_hash="x", is_active=True, is_admin=True)
        s.add(u)
        await s.commit()
        token, admin_id = create_access_token(u.id), u.id

    yield {"Authorization": f"Bearer {token}"}, admin_id

    async with maker() as s:
        await s.execute(delete(User).where(User.id == admin_id))
        await s.commit()


@pytest.fixture
async def answered(maker, logged_in, flagship_id):
    """一轮已经发生过的问答：会话 + 用户提问 + 助手回答。返回 (conv_id, msg_id)。"""
    now = datetime.now(UTC)
    async with maker() as s:
        conv = Conversation(user_id=logged_in, knowledge_space_id=flagship_id, title="面单")
        s.add(conv)
        await s.flush()
        s.add(
            Message(
                conversation_id=conv.id,
                role="user",
                content=QUESTION,
                created_at=now - timedelta(seconds=10),
            )
        )
        answer = Message(
            conversation_id=conv.id,
            role="assistant",
            content=BAD_ANSWER,
            citations=[{"n": 1, "title": "电子面单设置"}],
            images=[{"n": 1, "url": "/images/ab/cd.png"}],
            created_at=now,
        )
        s.add(answer)
        await s.commit()
        ids = (conv.id, answer.id)

    yield ids

    async with maker() as s:
        await s.execute(delete(AnswerCorrection).where(AnswerCorrection.conversation_id == ids[0]))
        await s.execute(delete(Message).where(Message.conversation_id == ids[0]))
        await s.execute(delete(Conversation).where(Conversation.id == ids[0]))
        await s.commit()


@pytest.fixture
async def cleanup_verified(maker):
    """把这次测试造出来的标准答案连同它的索引文档一起删掉。"""
    yield
    async with maker() as s:
        docs = list(
            (
                await s.execute(select(Document).where(Document.source_type == "verified"))
            ).scalars()
        )
        for d in docs:
            await s.execute(delete(Chunk).where(Chunk.document_id == d.id))
            await s.delete(d)
        await s.execute(delete(VerifiedAnswerRevision))
        await s.execute(delete(VerifiedAnswer))
        await s.commit()


async def _submit(api_client, msg_id, answer=GOOD_ANSWER, reason="第二步的菜单路径不对"):
    return await api_client.post(
        "/api/answer-corrections",
        json={"messageId": str(msg_id), "correctedAnswer": answer, "reason": reason},
    )


# ─────────── 1. 提交：快照是服务端取的，只能纠自己的 ───────────


async def test_submit_snapshots_the_original_from_the_server(api_client, logged_in, answered):
    """⭐ 原问题、原回答、原引用、原配图**都由服务端从会话里取**。

    让客户端把"原答案"一起传上来的话，它可以伪造一段从未存在过的回答，
    而审核界面上看不出真假——审的就是那段假的。
    """
    _, msg_id = answered

    r = await _submit(api_client, msg_id)

    assert r.status_code == 201, r.text
    got = r.json()
    assert got["status"] == flow.PENDING
    assert got["original_question"] == QUESTION, "原问题不是从上一条 user 消息取的"
    assert got["original_answer"] == BAD_ANSWER
    assert got["original_citations"] == [{"n": 1, "title": "电子面单设置"}]
    assert got["original_images"], "原配图快照丢了，审核时看不到当时配的什么图"
    assert got["knowledge_space_id"], "没带上知识版本，发布时会不知道发到哪一版"


async def test_cannot_correct_someone_elses_answer(api_client, maker, answered, flagship_id):
    """⭐ 别人会话里的消息一律当**不存在**（404，不是 403）。

    403 等于告诉对方「这个 id 是真的」，而 message_id 会随 SSE 发到浏览器里。
    """
    _, msg_id = answered
    email = f"mallory-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        other = User(email=email, password_hash="x", is_active=True)
        s.add(other)
        await s.commit()
        token = create_access_token(other.id)

    try:
        api_client.cookies.clear()
        r = await api_client.post(
            "/api/answer-corrections",
            json={"messageId": str(msg_id), "correctedAnswer": "我改的", "reason": "随便"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text
    finally:
        async with maker() as s:
            await s.execute(delete(User).where(User.email == email))
            await s.commit()


async def test_cannot_correct_a_user_message(api_client, maker, logged_in, answered):
    """只能纠 assistant 的回答。纠自己问的那句话没有意义，也不该建出一条纠错。"""
    conv_id, _ = answered
    async with maker() as s:
        user_msg = (
            await s.execute(
                select(Message.id).where(
                    Message.conversation_id == conv_id, Message.role == "user"
                )
            )
        ).scalar_one()

    r = await _submit(api_client, user_msg)
    assert r.status_code == 404


async def test_reason_is_required(api_client, logged_in, answered):
    """⚠️ 和「我自己改完自己用」不同：这是请人看一眼再对全站生效，
    审核的人需要知道原来错在哪。"""
    _, msg_id = answered
    r = await _submit(api_client, msg_id, reason="   ")
    assert r.status_code == 422


async def test_can_submit_by_trace_id(api_client, maker, logged_in, answered):
    """⭐ 前端手上只有 trace id（assistant 消息的库内 id 它拿不到）。

    为了这个接口专门往 SSE 协议里再塞一个 id，等于为后端方便去改一条
    所有人都在用的协议——所以这边认 traceId。
    """
    conv_id, msg_id = answered
    async with maker() as s:
        s.add(
            RequestTrace(
                user_id=logged_in,
                conversation_id=conv_id,
                message_id=msg_id,
                route="direct",
                question=QUESTION,
            )
        )
        await s.commit()
        trace_id = (
            await s.execute(select(RequestTrace.id).where(RequestTrace.message_id == msg_id))
        ).scalar_one()

    try:
        r = await api_client.post(
            "/api/answer-corrections",
            json={"traceId": str(trace_id), "correctedAnswer": GOOD_ANSWER, "reason": "路径不对"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["original_answer"] == BAD_ANSWER
        assert r.json()["trace_id"] == str(trace_id)
    finally:
        async with maker() as s:
            await s.execute(delete(RequestTrace).where(RequestTrace.id == trace_id))
            await s.commit()


async def test_a_correction_needs_a_target(api_client, logged_in):
    """traceId 和 messageId 一个都不给 → 422，而不是建出一条无主的纠错。"""
    r = await api_client.post(
        "/api/answer-corrections", json={"correctedAnswer": "改好了", "reason": "因为"}
    )
    assert r.status_code == 422


# ─────────── 2. 未审核不进 RAG ───────────


async def test_a_pending_correction_does_not_touch_retrieval(
    api_client, maker, logged_in, answered, flagship_id
):
    """⭐⭐ **提交只是排队。** 一条 pending 的纠错不许产生任何可检索的内容。

    这一条是 M16 存在的理由：在它之前，用户点一下就能让任意一段文字对全站
    立刻生效。检索里能搜到它 = 那个入口还开着。
    """
    _, msg_id = answered
    marker = f"只有纠错里才有的句子-{uuid.uuid4().hex[:8]}"

    r = await _submit(api_client, msg_id, answer=f"{GOOD_ANSWER} {marker}")
    assert r.status_code == 201, r.text

    async with maker() as s:
        assert await s.scalar(select(VerifiedAnswer).limit(1)) is None, "pending 就建出了标准答案"
        result = await search(
            s,
            marker,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=logged_in,
            space_id=flagship_id,
        )
    assert all(marker not in c.content for c in result.chunks), "未审核的纠错进了检索"


async def test_a_normal_user_cannot_write_a_verified_answer(api_client, logged_in):
    """⭐⭐ 堵掉「提交即公共生效」那个老入口：普通用户到 `/api/verified` 是 403。"""
    r = await api_client.post(
        "/api/verified", json={"question": QUESTION, "answer": "我说了算"}
    )
    assert r.status_code == 403, r.text


# ─────────── 3. 状态机 ───────────


async def test_review_and_publish_walks_the_state_machine(
    api_client, maker, admin_headers, logged_in, answered, fake_providers, cleanup_verified
):
    """pending → approved → published，每一步都留下审计字段。"""
    headers, admin_id = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    api_client.cookies.clear()
    r = await api_client.post(
        f"/api/admin/corrections/{cid}/review",
        json={"decision": "approve", "note": "路径确实错了"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == flow.APPROVED
    assert r.json()["review_note"] == "路径确实错了"
    assert r.json()["reviewed_at"]

    r = await api_client.post(
        f"/api/admin/corrections/{cid}/publish", json={}, headers=headers
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is True, "发布了却没进索引，等于没发布"
    assert out["knowledge_space"] == "flagship"

    async with maker() as s:
        row = await s.get(AnswerCorrection, uuid.UUID(cid))
        assert row.status == flow.PUBLISHED
        assert row.reviewed_by == admin_id
        v = await s.get(VerifiedAnswer, uuid.UUID(out["verified_id"]))
        assert v.answer == GOOD_ANSWER
        assert v.question == QUESTION
        assert v.source_correction_id == uuid.UUID(cid)
        assert v.status == "active"


async def test_publishing_twice_is_refused(
    api_client, maker, admin_headers, logged_in, answered, fake_providers, cleanup_verified
):
    """⭐ `published` 是终态。再发布一次必须 409，不能默默再来一遍。"""
    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    assert (
        await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)
    ).status_code == 200

    again = await api_client.post(
        f"/api/admin/corrections/{cid}/publish", json={}, headers=headers
    )
    assert again.status_code == 409, again.text


async def test_pending_cannot_be_published_without_review(
    api_client, admin_headers, logged_in, answered, fake_providers
):
    """⭐ **审核和发布是两步。** 跳过审核直接发布 = 无人审核，M16 就白做了。"""
    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    api_client.cookies.clear()
    r = await api_client.post(
        f"/api/admin/corrections/{cid}/publish", json={}, headers=headers
    )
    assert r.status_code == 409, r.text


async def test_a_normal_user_cannot_review(api_client, logged_in, answered):
    """审核接口对普通用户 403——否则「要过审」只是一句话。"""
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    assert (
        await api_client.post(
            f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}
        )
    ).status_code == 403
    assert (
        await api_client.post(f"/api/admin/corrections/{cid}/publish", json={})
    ).status_code == 403
    assert (await api_client.get("/api/admin/corrections")).status_code == 403


async def test_the_author_can_withdraw_while_pending(api_client, logged_in, answered):
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    r = await api_client.patch(f"/api/answer-corrections/{cid}", json={"action": "withdraw"})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == flow.WITHDRAWN
    # 撤回是终态：再撤一次没有意义，也不该悄悄成功
    assert (
        await api_client.patch(f"/api/answer-corrections/{cid}", json={"action": "withdraw"})
    ).status_code == 409


async def test_an_approved_correction_cannot_be_edited_by_its_author(
    api_client, admin_headers, logged_in, answered
):
    """⭐ 审核通过之后作者还能改内容的话，管理员看过的和最终发布的就不是同一段文字。"""
    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )

    r = await api_client.patch(
        f"/api/answer-corrections/{cid}",
        json={"correctedAnswer": "偷偷换掉的内容"},
        headers=headers,
    )
    assert r.status_code == 409, r.text


async def test_a_stale_version_is_refused(api_client, admin_headers, logged_in, answered):
    """⭐ 乐观锁：两个管理员同时点「通过」和「拒绝」，后到的必须失败。

    默默覆盖的话，审核结论被谁改掉了、什么时候改的，事后完全查不出来。
    """
    headers, _ = admin_headers
    _, msg_id = answered
    submitted = (await _submit(api_client, msg_id)).json()
    api_client.cookies.clear()

    ok = await api_client.post(
        f"/api/admin/corrections/{submitted['id']}/review",
        json={"decision": "approve", "version": submitted["version"]},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text

    stale = await api_client.post(
        f"/api/admin/corrections/{submitted['id']}/review",
        json={"decision": "reject", "version": submitted["version"]},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text


async def test_admin_can_edit_while_approving(
    api_client, maker, admin_headers, logged_in, answered
):
    """路线图 21.1：管理员能顺手改一版再通过。

    只给「通过 / 拒绝」两个按钮的话，为了改一个错别字只能拒绝再让人重提。
    """
    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    api_client.cookies.clear()
    r = await api_client.post(
        f"/api/admin/corrections/{cid}/review",
        json={
            "decision": "approve",
            "note": "顺手补了一步",
            "corrected_answer_markdown": f"{GOOD_ANSWER} 最后记得保存。",
        },
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["corrected_answer_markdown"].endswith("最后记得保存。")


# ─────────── 4. 发布同空间生效 ───────────


async def test_published_answer_is_scoped_to_its_space(
    api_client, maker, admin_headers, logged_in, answered, other_space, fake_providers,
    flagship_id, cleanup_verified,
):
    """⭐⭐ 旗舰版发布的标准答案，在企业版的会话里一条都搜不到。

    跨空间命中就是拿另一个产品的操作路径回答用户，而他照着点会点不到。
    """
    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    assert (
        await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)
    ).status_code == 200

    # ⚠️ 拿**块的原文**去搜：`FakeEmbedder` 下距离是 0，稳稳压过开发库里
    # 几千条真向量。用别的问法搜的话，测出来的是「这段文字像不像」，
    # 而这条测试要问的是「它在不在这个空间里」
    from copilot.verified import BODY_TEMPLATE

    body = BODY_TEMPLATE.format(question=QUESTION, answer=GOOD_ANSWER)

    async def hits(space_id):
        async with maker() as s:
            result = await search(
                s,
                body,
                FakeEmbedder(),
                PassThroughReranker(),
                space_id=space_id,
                top_k=20,
                rerank_k=20,
            )
        return [c for c in result.chunks if GOOD_ANSWER in c.content]

    assert await hits(flagship_id), "发布在本空间却搜不到，等于没发布"
    assert not await hits(other_space.id), "旗舰版的标准答案漏到了企业版"


# ─────────── 5. 不经 LLM 改写 ───────────


async def test_a_hit_returns_the_human_text_without_calling_the_model(
    api_client, maker, admin_headers, logged_in, answered, fake_providers, flagship_id,
    cleanup_verified,
):
    """⭐⭐ 命中标准答案时**一次模型调用都没有**，返回的就是人写的原文。

    走正常检索的话，这条答案只是上下文里的一块材料，模型会照自己的写法重述
    ——重述就有可能改掉事实，而这恰恰是已经有人确认过的那一段。
    """
    from copilot.qa import ask_stream

    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)

    async with maker() as s:
        streamed = await ask_stream(
            s,
            # 问法差一个问号、多两个空格——归一化之后是同一个问题
            f"  {QUESTION}？ ",
            FakeEmbedder(),
            PassThroughReranker(),
            BoomLLM(),  # 一被调用就炸
            user_id=logged_in,
            space_id=flagship_id,
        )
        text = "".join(piece for kind, piece in streamed.stream if kind == "content")

    assert text == GOOD_ANSWER, "命中之后返回的不是人写的原文"
    assert streamed.verified_id is not None
    assert streamed.citations == [], "标准答案不挂检索来源——它的来源就是人"


async def test_a_hit_never_crosses_spaces(
    api_client, maker, admin_headers, logged_in, answered, other_space, fake_providers,
    cleanup_verified,
):
    """⭐ 同一个问题在另一个知识版本里**不许**命中。"""
    from copilot.verified import lookup

    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)

    async with maker() as s:
        assert await lookup(s, QUESTION, other_space.id) is None
        # 没有空间上下文时也不许命中（fail closed，同检索）。
        # ⚠️ 这里只测 `lookup`，不走整条 `ask_stream`：缺空间时那条路会退回
        # 「检索不到 → 常识兜底」，也就是**照常调模型**，而 BoomLLM 一被调用就炸
        assert await lookup(s, QUESTION, None) is None


async def test_a_retired_answer_stops_hitting_and_leaves_the_index(
    api_client, maker, admin_headers, logged_in, answered, fake_providers, flagship_id,
    cleanup_verified,
):
    """⭐ 退役 = 立刻从检索和终结命中里消失，但行留着（可追溯）。"""
    from copilot.verified import lookup

    headers, _ = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    vid = (
        await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)
    ).json()["verified_id"]

    assert (await api_client.delete(f"/api/verified/{vid}", headers=headers)).status_code == 204

    async with maker() as s:
        row = await s.get(VerifiedAnswer, uuid.UUID(vid))
        assert row is not None, "退役不该把行抹掉——半年后要能查当初写了什么"
        assert row.status == "retired"
        assert await lookup(s, QUESTION, flagship_id) is None
        docs = list(
            (
                await s.execute(
                    select(Document).where(
                        Document.source_type == "verified", Document.source_url == vid
                    )
                )
            ).scalars()
        )
        assert docs == [], "退役了块还在索引里，答案不会变——用户会以为退役是假的"


# ─────────── 6. 修订可追溯 ───────────


async def test_every_publish_leaves_a_revision(
    api_client, maker, admin_headers, logged_in, answered, fake_providers, cleanup_verified
):
    """⭐ 发一次留一版。半年后要回答的是「每一次改了什么」，不是「最后是谁改的」。"""
    headers, admin_id = admin_headers
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]
    api_client.cookies.clear()
    await api_client.post(
        f"/api/admin/corrections/{cid}/review", json={"decision": "approve"}, headers=headers
    )
    vid = (
        await api_client.post(f"/api/admin/corrections/{cid}/publish", json={}, headers=headers)
    ).json()["verified_id"]

    r = await api_client.get(f"/api/verified/{vid}/revisions", headers=headers)

    assert r.status_code == 200, r.text
    versions = r.json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["answer"] == GOOD_ANSWER
    assert cid in (versions[0]["note"] or ""), "修订记录里没写这一版是从哪条纠错来的"

    async with maker() as s:
        rev = (
            await s.execute(
                select(VerifiedAnswerRevision).where(
                    VerifiedAnswerRevision.verified_answer_id == uuid.UUID(vid)
                )
            )
        ).scalars().first()
        assert rev.editor_id == admin_id


async def test_the_markdown_snapshot_has_everything_a_reviewer_needs(
    api_client, logged_in, answered
):
    """审核快照（路线图第 15 节）：原问、原答、修正、原因，一次看全。"""
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    md = (await api_client.get(f"/api/answer-corrections/{cid}/markdown")).json()["markdown"]

    assert md.startswith("---"), "没有 frontmatter，粘进 Git 就丢了上下文"
    assert QUESTION in md
    assert BAD_ANSWER in md
    assert GOOD_ANSWER in md
    assert "第二步的菜单路径不对" in md
    assert f'correction_id: "{cid}"' in md


async def test_mine_lists_my_own_corrections(api_client, logged_in, answered):
    _, msg_id = answered
    cid = (await _submit(api_client, msg_id)).json()["id"]

    got = (await api_client.get("/api/answer-corrections/mine")).json()

    assert [c["id"] for c in got] == [cid]
