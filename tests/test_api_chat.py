"""/api/chat 的端到端测试：协议序列、引用、会话落库、跨用户隔离。

不打外部 API——embedder / reranker / LLM 全换成假的。这些测试要能随时跑，
不能依赖 SiliconFlow 的额度，也不该每跑一次就烧一次 DeepSeek 的钱。

最重要的一条是 `test_no_answer_carries_no_citations`：
M1 踩过的坑——「知识库暂无此内容」下面挂着五条来源，用户会以为答案有依据。
"""

from __future__ import annotations

import uuid

from chat_helpers import DIM, PASSWORD, FakeEmbedder, FakeLLM, TopOneReranker, ask, parts
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.db.models import Conversation, InviteCode, Message, User
from copilot.qa import NO_ANSWER

# ---------- 协议 ----------


async def test_stream_shape(api_client, logged_in, public_chunk, fake_providers):
    title, body = public_chunk
    r = await ask(api_client, body)

    assert r.status_code == 200
    assert r.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.endswith("data: [DONE]\n\n"), "流必须以 [DONE] 收尾"

    types = [p["type"] for p in parts(r.text)]
    assert types[0] == "start"
    assert types[1] == "start-step"
    assert types[-2:] == ["finish-step", "finish"]
    assert "text-start" in types and "text-end" in types
    assert types.count("text-delta") > 1, "没有逐字流式，前端看不到打字机效果"
    # text-start 必须在所有 delta 之前，text-end 在之后
    assert types.index("text-start") < types.index("text-delta") < types.index("text-end")


async def test_text_deltas_reassemble_into_the_answer(
    api_client, logged_in, public_chunk, fake_providers
):
    _, body = public_chunk
    r = await ask(api_client, body)
    deltas = [p for p in parts(r.text) if p["type"] == "text-delta"]

    assert {p["id"] for p in deltas} == {deltas[0]["id"]}, "delta 的 id 不一致，前端拼不起来"
    assert "".join(p["delta"] for p in deltas) == fake_providers.reply


# ---------- 引用 ----------


async def test_citations_arrive_after_the_text(
    api_client, logged_in, public_chunk, fake_providers
):
    title, body = public_chunk
    r = await ask(api_client, body)
    ps = parts(r.text)
    types = [p["type"] for p in ps]

    assert "data-citations" in types
    # 先正文后引用：引用早发的话，模型万一回"不知道"就来不及撤了
    assert types.index("text-end") < types.index("data-citations")

    cites = ps[types.index("data-citations")]["data"]["citations"]
    assert cites[0]["title"] == title
    assert cites[0]["n"] == 1
    assert cites[0]["url"] == "https://www.yuque.com/wdterpqjb/test"


async def test_no_answer_carries_no_citations(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """⭐ M1 的坑 #2：模型说「知识库暂无此内容」时，一条来源都不能挂。

    挂了的话，用户看到的是一句"不知道"配五条参考资料——
    会以为系统查过了、答案有依据。这比不做防幻觉更糟。
    """
    from copilot.api import providers

    monkeypatch.setattr(providers, "get_llm", lambda: FakeLLM(NO_ANSWER))

    _, body = public_chunk
    r = await ask(api_client, body)
    ps = parts(r.text)

    assert NO_ANSWER in "".join(p["delta"] for p in ps if p["type"] == "text-delta")
    assert not [p for p in ps if p["type"] == "data-citations"], "说了不知道还挂着来源"


async def test_no_answer_is_not_stored_with_citations(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """落库的那份也不能带来源——否则刷新页面后引用又冒出来了。"""
    from copilot.api import providers

    monkeypatch.setattr(providers, "get_llm", lambda: FakeLLM(NO_ANSWER))

    _, body = public_chunk
    await ask(api_client, body)

    async with maker() as s:
        rows = list(
            (
                await s.execute(
                    select(Message.citations)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.user_id == logged_in, Message.role == "assistant")
                )
            ).scalars()
        )
    assert rows and all(c is None for c in rows), f"落库的引用没清干净：{rows}"


async def test_only_the_sources_the_answer_cites_are_shown(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """来源清单只列**正文引用过**的那几条。

    ⭐ 线上台账里的样子：一条走方案流程的会话，连「你好」「好的谢谢」都挂着
    **21 条来源**——出方案那条路大范围检索，而方案正文一个 `[n]` 都不写。
    用户看到「来源 · 21」，点开全是和这句话无关的文档。
    来源清单是给人溯源用的，不是"这一轮检索到了什么"的日志。
    """
    from copilot.api import providers

    # 一段没有任何 [n] 的答案（方案 / 常识 / 寒暄都是这个形状）
    monkeypatch.setattr(providers, "get_llm", lambda: FakeLLM("按你的情况，建议这样配置：先接入店铺。"))

    _, body = public_chunk
    r = await ask(api_client, body)

    assert not [p for p in parts(r.text) if p["type"] == "data-citations"], (
        "正文一个 [n] 都没写，却挂了来源"
    )
    async with maker() as s:
        rows = list(
            (
                await s.execute(
                    select(Message.citations)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.user_id == logged_in, Message.role == "assistant")
                )
            ).scalars()
        )
    assert rows and all(c is None for c in rows), f"落库的来源没清干净：{rows}"


def test_cited_only_keeps_the_original_numbers():
    """只留引用过的，**但不重新编号**。

    重编就得连正文一起改，而正文已经流给用户了——两边对不上的表现是
    用户点 `[3]` 跳到另一篇，比编号不连续糟得多。
    """
    from copilot.qa import cited_only

    cites = [{"n": 1, "title": "甲"}, {"n": 2, "title": "乙"}, {"n": 3, "title": "丙"}]
    assert [c["n"] for c in cited_only("只引了第三条 [3]。", cites)] == [3]
    assert [c["n"] for c in cited_only("引了 [1] 和 [3]。", cites)] == [1, 3]
    assert cited_only("一个引用都没有的方案正文。", cites) == []
    # 编号超出清单时不该炸，也不该凭空造一条
    assert cited_only("引了 [9]。", cites) == []


# ---------- 会话历史 ----------


async def test_conversation_and_messages_persisted(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_part = next(p for p in parts(r.text) if p["type"] == "data-conversation")
    conv_id = uuid.UUID(conv_part["data"]["id"])

    async with maker() as s:
        msgs = list(
            (
                await s.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.created_at, Message.id)
                )
            ).scalars()
        )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == body
    assert msgs[1].content == fake_providers.reply
    assert msgs[1].citations, "助手消息应该带上引用"


async def test_same_client_id_continues_one_conversation(
    api_client, logged_in, public_chunk, fake_providers
):
    """前端传同一个 UUID，多轮就落在同一条会话里。"""
    _, body = public_chunk
    cid = str(uuid.uuid4())
    for _ in range(2):
        r = await ask(api_client, body, conv_id=cid)
        got = next(p for p in parts(r.text) if p["type"] == "data-conversation")
        assert got["data"]["id"] == cid

    listing = await api_client.get("/api/conversations")
    assert [c["id"] for c in listing.json()] == [cid]


async def test_non_uuid_client_id_gets_a_server_side_id(
    api_client, logged_in, public_chunk, fake_providers
):
    """useChat 默认的 id 是 nanoid，不是 UUID。不能因此报错。"""
    _, body = public_chunk
    r = await ask(api_client, body, conv_id="nanoid-XyZ123")
    conv = next(p for p in parts(r.text) if p["type"] == "data-conversation")
    uuid.UUID(conv["data"]["id"])  # 能解析就说明服务端另发了一个


async def test_conversation_title_from_first_question(
    api_client, logged_in, public_chunk, fake_providers
):
    _, body = public_chunk
    await ask(api_client, body)
    conv = (await api_client.get("/api/conversations")).json()[0]
    assert conv["title"].startswith(body[:10])


async def test_messages_endpoint_returns_the_thread(
    api_client, logged_in, public_chunk, fake_providers
):
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    msgs = (await api_client.get(f"/api/conversations/{conv_id}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]


async def test_cannot_read_another_users_conversation(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """别人的会话一律 404。用 403 等于确认"这个 id 是有效的"。"""
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    # 换一个账号
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    other = f"other-{uuid.uuid4().hex[:10]}@test.local"
    await api_client.post("/api/auth/logout")
    reg = await api_client.post(
        "/api/auth/register",
        json={"email": other, "password": PASSWORD, "inviteCode": code},
    )
    other_id = uuid.UUID(reg.json()["id"])
    try:
        assert (
            await api_client.get(f"/api/conversations/{conv_id}/messages")
        ).status_code == 404
        assert (await api_client.get("/api/conversations")).json() == []
    finally:
        async with maker() as s:
            await s.execute(delete(InviteCode).where(InviteCode.code == code))
            await s.execute(delete(User).where(User.id == other_id))
            await s.commit()


async def test_squatted_conversation_id_does_not_leak(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """拿别人的 conversation id 来提问，只会给自己另开一条，写不进对方的历史。"""
    _, body = public_chunk
    r = await ask(api_client, body)
    victim_conv = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    await api_client.post("/api/auth/logout")
    reg = await api_client.post(
        "/api/auth/register",
        json={
            "email": f"squat-{uuid.uuid4().hex[:8]}@test.local",
            "password": PASSWORD,
            "inviteCode": code,
        },
    )
    attacker_id = uuid.UUID(reg.json()["id"])
    try:
        r2 = await ask(api_client, body, conv_id=victim_conv)
        got = next(p for p in parts(r2.text) if p["type"] == "data-conversation")["data"]["id"]
        assert got != victim_conv, "写进了别人的会话"

        async with maker() as s:
            owner = (
                await s.execute(
                    select(Conversation.user_id).where(Conversation.id == uuid.UUID(victim_conv))
                )
            ).scalar_one()
        assert owner == logged_in, "受害者的会话被改了归属"
    finally:
        async with maker() as s:
            convs = list(
                (
                    await s.execute(
                        select(Conversation.id).where(Conversation.user_id == attacker_id)
                    )
                ).scalars()
            )
            if convs:
                await s.execute(delete(Message).where(Message.conversation_id.in_(convs)))
                await s.execute(delete(Conversation).where(Conversation.id.in_(convs)))
            await s.execute(delete(InviteCode).where(InviteCode.code == code))
            await s.execute(delete(User).where(User.id == attacker_id))
            await s.commit()


# ---------- 出错与边界 ----------


async def test_empty_messages_rejected(api_client, logged_in, fake_providers):
    assert (await api_client.post("/api/chat", json={"messages": []})).status_code == 400


async def test_plain_content_field_also_works(
    api_client, logged_in, public_chunk, fake_providers
):
    """老版本 AI SDK 和手写 curl 用的是 content 而不是 parts，也得收。"""
    _, body = public_chunk
    r = await api_client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": body}]}
    )
    assert r.status_code == 200
    assert "text-delta" in [p["type"] for p in parts(r.text)]


async def test_provider_failure_becomes_an_error_part(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """LLM 挂了不能让流卡死——发一个 error 片段，照常收尾。

    响应头早就发出去了，这时候再抛异常前端只会看到连接断开。
    """
    from copilot.api import providers

    class BrokenLLM(FakeLLM):
        def stream(self, messages, temperature=0.1):
            raise RuntimeError("DeepSeek 返回 500，密钥是 sk-secret-should-not-leak")

    monkeypatch.setattr(providers, "get_llm", lambda: BrokenLLM(""))

    _, body = public_chunk
    r = await ask(api_client, body)
    ps = parts(r.text)
    types = [p["type"] for p in ps]

    assert r.status_code == 200
    assert "error" in types
    assert types[-2:] == ["finish-step", "finish"], "出错了也得正常收尾"
    assert r.text.endswith("data: [DONE]\n\n")
    # 错误详情只进服务端日志。errorText 会原样渲染在聊天框里
    assert "sk-secret-should-not-leak" not in r.text


# ---------- is_no_answer 的边界（M7 评测撞出来的）----------


def test_no_answer_detected_when_phrase_comes_last():
    """⭐ M7 的 Agent 会先解释「我查到的是 X」再补一句「暂无此内容」。

    只认开头的话，这种答案会被判成"有答案"，于是页面上出现
    「知识库暂无此内容」下面挂着五条来源——正是 M1 最不想要的那个样子。
    """
    from copilot.qa import is_no_answer

    assert is_no_answer("知识库中检索到的内容均与员工工资条无关。\n\n知识库暂无此内容。")


def test_partial_answer_with_citations_is_not_no_answer():
    """⭐ 反例，比上面那条更要紧：答出了一部分、并说明另一部分没有，
    引用必须照常显示。带 [n] 就说明有据可依，不能因为末尾提了一句
    「某部分材料里没有」就把整条来源清单丢掉。"""
    from copilot.qa import is_no_answer

    assert not is_no_answer(
        "短信模板在【设置–策略设置–短信策略–短信发送模板】里新建 [1]。\n"
        "至于短信费用怎么收，知识库暂无此内容。"
    )


def test_plain_answer_is_not_no_answer():
    from copilot.qa import is_no_answer

    assert not is_no_answer("批量换货一次最多 500 单 [1]。")


def test_general_knowledge_answer_with_a_closing_caveat_is_not_no_answer():
    """⭐ 常识兜底打开后的第三种形态（2026-08-23 评测撞出来的）。

    M12 的铁律 1 规定常识回答**不许标来源编号**，于是「全文没有 [n]」这个
    条件对它恒成立；末尾再补一句「产品里具体怎么算，知识库暂无此内容」，
    整段正确的解释就被判成拒答。实测原句（`gk-inventory-turnover`）：
    189 字的回答，那句话在第 180 字，评测报「常识题不该拒答」，
    线上会把 `answer_source` 记成 `no_answer`——答得好好的问题记成没答上。
    """
    from copilot.qa import is_no_answer

    assert not is_no_answer(
        "库存周转率是衡量库存周转速度的指标，反映一定时期内库存商品被销售并补充的频率。"
        "知识库里没有专门针对“库存周转率”的说明，按通用理解：它通常用"
        "“销售成本 ÷ 平均库存”来计算，数值越高说明周转越快、资金占用越少，"
        "但过高也可能意味着备货不足。\n"
        "关于旺店通旗舰版 ERP 中库存周转率的具体计算口径或配置路径，知识库暂无此内容。"
    )


def test_a_short_preface_before_the_phrase_is_still_no_answer():
    """位置条件不能把 M7 那种形态放过去——它的前缀只有一句话。"""
    from copilot.qa import is_no_answer

    assert is_no_answer("我查到的是发货单打印，不是你问的退款单。知识库暂无此内容。")


# ---------- 删除会话 ----------


async def test_delete_conversation_removes_messages(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """删会话要把消息一起带走（靠 messages.conversation_id 的 ON DELETE CASCADE）。"""
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 204

    assert (await api_client.get(f"/api/conversations/{conv_id}/messages")).status_code == 404
    assert (await api_client.get("/api/conversations")).json() == []
    async with maker() as s:
        left = (
            await s.execute(
                select(Message).where(Message.conversation_id == uuid.UUID(conv_id))
            )
        ).scalars().all()
    assert left == []


async def test_delete_conversation_is_404_the_second_time(
    api_client, logged_in, public_chunk, fake_providers
):
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 204
    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 404


async def test_cannot_delete_another_users_conversation(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    """⭐ 最要紧的一条：别人的会话删不掉，而且回 404 不回 403。

    删除接口如果用 403 区分「存在但不是你的」，就等于给了一个拿 uuid
    探别人有没有这段会话的探针——比读接口那条更值钱，因为它顺带确认了
    这个 id 当前是活的。
    """
    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    await api_client.post("/api/auth/logout")
    reg = await api_client.post(
        "/api/auth/register",
        json={
            "email": f"del-{uuid.uuid4().hex[:8]}@test.local",
            "password": PASSWORD,
            "inviteCode": code,
        },
    )
    other_id = uuid.UUID(reg.json()["id"])
    try:
        assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 404
        # 原主人的会话还在
        async with maker() as s:
            assert await s.get(Conversation, uuid.UUID(conv_id)) is not None
    finally:
        async with maker() as s:
            await s.execute(delete(InviteCode).where(InviteCode.code == code))
            await s.execute(delete(User).where(User.id == other_id))
            await s.commit()


async def test_delete_conversation_removes_the_export_file(
    api_client, logged_in, public_chunk, fake_providers, maker, tmp_path, monkeypatch
):
    """导出的 xlsx 必须跟着删。

    库里删掉会话之后，没有任何一行再指向那个文件——留着它就是一个
    谁都不会去回收的孤儿，而它落在用户目录下、体积不小。
    """
    from copilot.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(type(settings), "export_dir", property(lambda self: tmp_path))

    _, body = public_chunk
    r = await ask(api_client, body)
    conv_id = next(p for p in parts(r.text) if p["type"] == "data-conversation")["data"]["id"]

    rel = f"{uuid.uuid4().hex}.xlsx"
    (tmp_path / rel).write_bytes(b"fake xlsx")
    async with maker() as s:
        conv = await s.get(Conversation, uuid.UUID(conv_id))
        conv.export_path = rel
        await s.commit()

    assert (await api_client.delete(f"/api/conversations/{conv_id}")).status_code == 204
    assert not (tmp_path / rel).exists()
