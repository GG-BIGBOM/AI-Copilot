"""/api/chat 的端到端测试：协议序列、引用、会话落库、跨用户隔离。

不打外部 API——embedder / reranker / LLM 全换成假的。这些测试要能随时跑，
不能依赖 SiliconFlow 的额度，也不该每跑一次就烧一次 DeepSeek 的钱。

最重要的一条是 `test_no_answer_carries_no_citations`：
M1 踩过的坑——「知识库暂无此内容」下面挂着五条来源，用户会以为答案有依据。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.db.models import Chunk, Conversation, Document, InviteCode, Message, User
from copilot.providers.base import RerankResult
from copilot.qa import NO_ANSWER

PASSWORD = "test-password-2026"
DIM = 1024


# ---------- 假 provider ----------


class FakeEmbedder:
    """确定性向量。同样的文本永远得到同样的向量，所以拿原文去搜，
    余弦距离是 0，稳稳压过库里那 5000 个真实 bge-m3 向量。"""

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


class TopOneReranker:
    """只保留向量召回的第一名。测试里就是那条精确命中的 chunk。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=0, score=0.9)] if documents else []


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        self.calls.append(messages)
        # 一个字一个字地吐，逼出多个 text-delta 片段
        return iter(list(self.reply))

    def close(self) -> None:
        pass


# ---------- 夹具 ----------


@pytest.fixture
async def public_chunk(maker):
    """往公共库塞一篇文档，让检索有东西可召回。"""
    tag = uuid.uuid4().hex[:8]
    title = f"电子面单设置指南-{tag}"
    body = f"打印电子面单前先在设置里绑定物流账号-{tag}"

    async with maker() as s:
        doc = Document(
            owner_id=None,
            source_type="yuque",
            title=title,
            source_url="https://www.yuque.com/wdterpqjb/test",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=None,
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=title,
                heading="绑定物流账号",
                source_url="https://www.yuque.com/wdterpqjb/test",
            )
        )
        await s.commit()
        doc_id = doc.id

    yield title, body

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


@pytest.fixture
def fake_providers(monkeypatch, maker):
    """把 provider 和会话工厂都换成测试用的。

    chat 路由在流里自己开会话（StreamingResponse 的响应体在依赖退出之后才被消费），
    所以这里得连 `SessionLocal` 一起换掉，否则流里那部分会打到真实连接池上。
    """
    from copilot.api import providers
    from copilot.api.routes import chat as chat_module

    llm = FakeLLM("先绑定物流账号[1]，再打印面单。")
    monkeypatch.setattr(providers, "get_embedder", FakeEmbedder)
    monkeypatch.setattr(providers, "get_reranker", TopOneReranker)
    monkeypatch.setattr(providers, "get_llm", lambda: llm)
    monkeypatch.setattr(chat_module, "SessionLocal", maker)
    return llm


@pytest.fixture
async def logged_in(api_client, maker):
    """注册一个用户并保持登录态；结束时把它连同会话记录一起删干净。"""
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)

    email = f"chat-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": code},
    )
    assert r.status_code == 201, r.text
    user_id = uuid.UUID(r.json()["id"])

    yield user_id

    async with maker() as s:
        convs = list(
            (
                await s.execute(select(Conversation.id).where(Conversation.user_id == user_id))
            ).scalars()
        )
        if convs:
            await s.execute(delete(Message).where(Message.conversation_id.in_(convs)))
            await s.execute(delete(Conversation).where(Conversation.id.in_(convs)))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


def parts(body: str) -> list[dict]:
    """把 SSE 正文解成协议片段列表。`[DONE]` 不是 JSON，单独处理。"""
    out = []
    for line in body.split("\n\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


async def ask(api_client, question: str, conv_id: str | None = None):
    payload = {"messages": [{"role": "user", "parts": [{"type": "text", "text": question}]}]}
    if conv_id:
        payload["id"] = conv_id
    return await api_client.post("/api/chat", json=payload)


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
