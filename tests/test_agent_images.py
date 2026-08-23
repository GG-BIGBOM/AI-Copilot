"""M13.1：Agent 路由的配图回归。

直路的配图链路 M9 就打通了，Agent 是 M10 才接上去的，两条路各有一段自己的
转发代码：直路在 `_chat_stream` 里直接发 `streamed.images`，Agent 要经过
`answer_kb → deps.emit_images() → runner 的事件泵 → data-images`。
**只要有一段漏接线，用户看到的就是一个裸 `[图1]`**——不报错、不红测试，
只有截图没了。所以这里守的是「两条路产出同一份对照表，并且都落库」：

1. Agent 流里必须有 `data-images`，且排在正文之前（前端要边流边换图）。
2. 同一个问题，Agent 和直路给出的图片对照表必须完全一致。
3. 对照表里的编号必须来自本轮上下文，不能凭空多出一条。
4. 图片要跟着答案落进 `messages.images`，否则刷新页面就剩裸标记。
5. 模型引用了不存在的图号时，后端不能为它编一条记录出来。
   （正文里那个坏标记由前端 `inlineImages` 删掉，见 image-rendering.test.ts。）

不联网：embedder / reranker / LLM 全是假的，模型行为用 `FunctionModel` 写死。
"""

from __future__ import annotations

import json
import uuid

import pytest
from chat_helpers import FakeEmbedder, FakeLLM, TopOneReranker, ask, parts
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete, select

from copilot.db.models import Chunk, Conversation, Document, Message, RequestTrace

IMAGE_URL = "/images/a3/agent-regression.png"
MARKER_ID = "a3f9"


def scripted(*turns) -> FunctionModel:
    """按顺序把这些回复喂给 Agent。多要一次就报错——那说明它多转了一圈。"""
    it = iter(turns)

    async def f(messages, info):  # noqa: ARG001 - FunctionModel 的签名要求
        try:
            turn = next(it)
        except StopIteration:  # pragma: no cover - 只有测试写错了才会到这
            raise AssertionError("Agent 比预期多请求了一次模型") from None
        if isinstance(turn, str):
            for ch in turn:
                yield ch
        else:
            yield turn

    return FunctionModel(stream_function=f)


def call(name: str, **args) -> dict[int, DeltaToolCall]:
    return {
        0: DeltaToolCall(
            name=name,
            json_args=json.dumps(args, ensure_ascii=False),
            tool_call_id=f"call_{name}",
        )
    }


@pytest.fixture
async def image_chunk(maker):
    """一篇带配图的公共文档。正文里是 `[图:a3f9]`，检索时才被编号成 `[图1]`。"""
    tag = uuid.uuid4().hex[:8]
    title = f"电子面单打印设置-{tag}"
    body = f"进入打印设置页 [图:{MARKER_ID}]，勾选自动打印-{tag}"
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
                heading="打印设置",
                source_url="https://www.yuque.com/wdterpqjb/test",
                images=[{"id": MARKER_ID, "url": IMAGE_URL}],
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
def wire(monkeypatch, maker):
    """把两条路都接到假 provider 上，并按需把路由钉死在 Agent 或直路。

    路由本身在 `test_agent.py` 里单独测；这里要的是「进了 Agent 分支之后
    配图还在不在」，所以直接钉死，免得受灰度桶和触发词影响。
    """

    def _wire(reply: str, *, agent: bool) -> FakeLLM:
        from copilot.agent import runner as runner_module
        from copilot.api import providers
        from copilot.api import trace as trace_module
        from copilot.api.routes import chat as chat_module

        llm = FakeLLM(reply)
        monkeypatch.setattr(providers, "get_embedder", FakeEmbedder)
        monkeypatch.setattr(providers, "get_reranker", TopOneReranker)
        monkeypatch.setattr(providers, "get_llm", lambda: llm)
        monkeypatch.setattr(chat_module, "SessionLocal", maker)
        monkeypatch.setattr(trace_module, "SessionLocal", maker)

        async def _forced(*_a, **_kw) -> bool:
            return agent

        monkeypatch.setattr(chat_module, "_use_agent", _forced)

        if agent:
            real_build = runner_module.build_agent
            # `answer_kb` 是终结工具：它的返回就是最终答案，配图在正文之前发。
            monkeypatch.setattr(
                runner_module,
                "build_agent",
                lambda model=None: real_build(scripted(call("answer_kb"), "完毕")),
            )
        return llm

    return _wire


def images_of(body: str) -> list[dict]:
    """流里那份 `data-images` 的对照表。一条都没有就返回空表。"""
    for part in parts(body):
        if part["type"] == "data-images":
            return part["data"]["images"]
    return []


async def last_route(maker, user_id) -> str | None:
    """这一轮真的走了 Agent 吗。钉死路由之后，这是唯一能证明它的东西。"""
    async with maker() as s:
        return await s.scalar(
            select(RequestTrace.route)
            .where(RequestTrace.user_id == user_id)
            .order_by(RequestTrace.created_at.desc())
            .limit(1)
        )


async def stored_images(maker, user_id) -> list[dict] | None:
    async with maker() as s:
        return await s.scalar(
            select(Message.images)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )


# ---------- 1：Agent 流里必须有配图，且排在正文之前 ----------


async def test_agent_preserves_image_events(api_client, logged_in, image_chunk, wire, maker):
    _, body = image_chunk
    wire("按 [图1] 打开打印设置页[1]。", agent=True)

    r = await ask(api_client, body)

    assert r.status_code == 200
    types = [p["type"] for p in parts(r.text)]
    assert "data-images" in types, "Agent 路径把配图弄丢了，用户看到的是裸 [图1]"
    assert types.index("data-images") < types.index("text-delta"), (
        "对照表来晚了：前端已经把 [图1] 当普通文字渲染出去了"
    )
    assert images_of(r.text) == [{"n": 1, "url": IMAGE_URL}]
    assert await last_route(maker, logged_in) == "agent", "路由没进 Agent，这条回归等于没跑"


# ---------- 2：Agent 和直路必须给出同一份对照表 ----------


async def test_agent_and_direct_use_same_images(api_client, logged_in, image_chunk, wire, maker):
    _, body = image_chunk
    reply = "按 [图1] 打开打印设置页[1]。"

    wire(reply, agent=False)
    direct = images_of((await ask(api_client, body)).text)
    assert await last_route(maker, logged_in) == "direct"

    wire(reply, agent=True)
    agent = images_of((await ask(api_client, body)).text)
    assert await last_route(maker, logged_in) == "agent"

    assert direct == [{"n": 1, "url": IMAGE_URL}]
    assert agent == direct, "两条路的配图对照表不一致，同一个问题会看到不同的图"


# ---------- 3：编号只能来自本轮上下文 ----------


async def test_agent_image_ids_match_context(api_client, logged_in, image_chunk, wire):
    _, body = image_chunk
    wire("按 [图1] 打开打印设置页[1]。", agent=True)

    images = images_of((await ask(api_client, body)).text)

    assert [img["n"] for img in images] == [1], "编号必须从 1 连续排，前端按编号取图"
    assert {img["url"] for img in images} == {IMAGE_URL}, "对照表里出现了本轮上下文之外的图"


# ---------- 4：图片要跟着答案落库 ----------


async def test_agent_images_persist_to_message(api_client, logged_in, image_chunk, wire, maker):
    """刷新页面读的是 `messages.images`。这一列空了，历史里就只剩裸 `[图1]`。"""
    _, body = image_chunk
    wire("按 [图1] 打开打印设置页[1]。", agent=True)

    r = await ask(api_client, body)

    assert images_of(r.text) == [{"n": 1, "url": IMAGE_URL}]
    assert await stored_images(maker, logged_in) == [{"n": 1, "url": IMAGE_URL}]


# ---------- 5：不存在的图号，后端不能替它编一条出来 ----------


async def test_invalid_image_reference_is_dropped(api_client, logged_in, image_chunk, wire):
    """模型写了 `[图99]`。后端只发真实存在的那一张，正文里的坏标记由前端删。"""
    _, body = image_chunk
    wire("按 [图99] 打开打印设置页[1]。", agent=True)

    images = images_of((await ask(api_client, body)).text)

    assert 99 not in {img["n"] for img in images}, "后端为一个不存在的图号编了记录"
    assert images == [{"n": 1, "url": IMAGE_URL}]
