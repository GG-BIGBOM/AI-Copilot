"""M10 P1：终结工具的机制。不联网、不花钱——模型行为用 `FunctionModel` 写死。

「终结工具」= 它的返回**就是**给用户的最终答案，Agent 不复述、不加工。
这里守的是四件事，每一件坏掉的表现都是「页面上安静地不对」而不是报错：

1. **终结工具的正文要原样直通前端**，一个字都不能少。
2. **Agent 自己写的正文，在有终结答案时要整个丢掉。** 模型几乎一定会
   在工具之后再补一句「以上就是全部内容，希望对你有帮助」——那句话对用户
   毫无价值，还会让答案看起来像是它写的。
3. **调工具之前的开场白也要丢掉。** 「我查一下」顶在答案前面很难看，
   而且它是在工具跑完**之前**就产生的，边流边发就收不回来了。
4. **没有终结答案时，Agent 自己的话必须照常吐出来。** 追问、时间、闲聊
   全靠这条；丢错了的表现是「问它几点，它一个字都不回」。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import FakeEmbedder, FakeLLM, TopOneReranker, parts
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete

from copilot.agent.deps import AgentDeps
from copilot.agent.runner import run_agent_stream
from copilot.agent.tools import answer_kb
from copilot.db.models import Chunk, Document

KB_REPLY = "先绑定物流账号[1]，再打印面单。"


def scripted(*turns) -> FunctionModel:
    """按顺序把这些回复喂给 Agent。多要一次就报错——那说明它多转了一圈。

    每一轮要么是一段文本（`str`），要么是一次工具调用（`call("...")`）。
    ⚠️ 流式模式下**一轮里不能既有文本又有工具调用**，那是 `FunctionModel` 的
    限制、不是真模型的（真模型会两样一起给）。所以「调工具前先说一句开场白」
    那条不在这里测，见 `test_preamble_never_reaches_the_stream`。
    """
    it = iter(turns)

    async def f(messages, info):  # noqa: ARG001 - FunctionModel 的签名要求
        try:
            turn = next(it)
        except StopIteration:  # pragma: no cover - 只有测试写错了才会到这
            raise AssertionError("Agent 比预期多请求了一次模型") from None
        if isinstance(turn, str):
            for ch in turn:  # 一个字一个字地吐，逼出多个 text-delta
                yield ch
        else:
            yield turn

    return FunctionModel(stream_function=f)


def call(name: str) -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args="{}", tool_call_id=f"call_{name}")}


def _deps(session, **kw) -> AgentDeps:
    return AgentDeps(
        session=session,
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        embedder=FakeEmbedder(),
        reranker=TopOneReranker(),
        llm=kw.pop("llm", None) or FakeLLM(KB_REPLY),
        **kw,
    )


async def drain(question: str, deps: AgentDeps, model: FunctionModel) -> tuple[list[dict], str]:
    """跑一轮，返回 (协议片段列表, 最后一次报告的正文)。"""
    body: list[str] = []
    answer = ""
    async for part, so_far in run_agent_stream(question, deps, model=model):
        body.append(part)
        answer = so_far
    return parts("".join(body)), answer


def text_of(chunks: list[dict]) -> str:
    return "".join(c["delta"] for c in chunks if c["type"] == "text-delta")


# ---------- 1 / 2：终结答案直通，Agent 的复述丢掉 ----------


async def test_terminal_answer_reaches_the_user_verbatim(maker, public_chunk):
    async with maker() as s:
        deps = _deps(s)
        chunks, answer = await drain(
            "电子面单怎么设置",
            deps,
            scripted(call("answer_kb"), "以上，希望对你有帮助。"),
        )

    assert text_of(chunks) == KB_REPLY
    assert answer == KB_REPLY  # 调用方拿它去判 is_no_answer，必须是完整的
    assert deps.final_answer == KB_REPLY


async def test_agent_does_not_get_to_add_a_closing_remark(maker, public_chunk):
    async with maker() as s:
        chunks, _ = await drain(
            "电子面单怎么设置",
            _deps(s),
            scripted(call("answer_kb"), "以上，希望对你有帮助。"),
        )

    assert "希望对你有帮助" not in text_of(chunks)


def test_preamble_never_reaches_the_stream():
    """「我查一下」是在工具跑完**之前**产生的。边流边发就收不回来了。

    真模型会在同一次回复里既说话又调工具（`FunctionModel` 的流式模式做不到，
    所以这里直接验翻译层）：Agent 写的字只进 `drafted`，一个 SSE 片段都不发。
    """
    from pydantic_ai import PartDeltaEvent, PartStartEvent
    from pydantic_ai.messages import TextPart, TextPartDelta

    from copilot.agent.runner import _translate

    drafted: list[str] = []
    delta = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="我查一下。"))
    assert _translate(PartStartEvent(index=0, part=TextPart("好的，")), drafted) == []
    assert _translate(delta, drafted) == []
    assert "".join(drafted) == "好的，我查一下。"


# ---------- 3：没有终结答案时，Agent 自己的话要照常吐 ----------


async def test_agent_own_text_is_emitted_when_no_terminal_tool(maker):
    """追问 / 时间 / 闲聊全靠这条。丢错了的表现是「问它几点，它一个字都不回」。"""
    async with maker() as s:
        chunks, answer = await drain(
            "你好",
            _deps(s),
            scripted("你好，我是旺店通 ERP 的知识库助手。"),
        )

    assert text_of(chunks) == "你好，我是旺店通 ERP 的知识库助手。"
    assert answer == text_of(chunks)


async def test_text_start_and_end_wrap_every_segment(maker):
    """少一个 text-end，前端那条消息就永远停在「正在输入」。"""
    async with maker() as s:
        chunks, _ = await drain(
            "你好", _deps(s), scripted("你好。")
        )

    starts = [c for c in chunks if c["type"] == "text-start"]
    ends = [c for c in chunks if c["type"] == "text-end"]
    assert len(starts) == len(ends) == 1
    assert starts[0]["id"] == ends[0]["id"]
    assert all(c["id"] == starts[0]["id"] for c in chunks if c["type"] == "text-delta")


# ---------- 4：工具调用要出现在流里 ----------


async def test_tool_call_is_reported_with_a_human_label(maker, public_chunk):
    """前端拿 toolName 直接显示。发 `answer_kb` 的话用户看到的是内部标识。"""
    async with maker() as s:
        chunks, _ = await drain(
            "电子面单怎么设置",
            _deps(s),
            scripted(call("answer_kb"), "完毕"),
        )

    names = [c["toolName"] for c in chunks if c["type"] == "tool-input-start"]
    assert names == ["查知识库"]
    assert any(c["type"] == "tool-output-available" for c in chunks)


# ---------- 5：配图必须在正文之前 ----------


@pytest.fixture
async def chunk_with_image(maker):
    """一篇带配图的公共文档。正文里是 `[图:a3f9]` 这种标记，检索时才被编号。"""
    tag = uuid.uuid4().hex[:8]
    title = f"打印设置-{tag}"
    body = f"进入打印设置页 [图:a3f9]，勾选自动打印-{tag}"
    async with maker() as s:
        doc = Document(
            owner_id=None,
            source_type="yuque",
            title=title,
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
                images=[{"id": "a3f9", "url": "https://cdn.test/pic.png"}],
            )
        )
        await s.commit()
        doc_id = doc.id

    yield body

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


async def test_images_are_sent_before_the_text(maker, chunk_with_image):
    """前端要边流边把 [图1] 换成真图。对照表来晚了，用户看到的是一个裸标记。"""
    async with maker() as s:
        deps = _deps(s, llm=FakeLLM("按 [图1] 操作即可。"))
        chunks, _ = await drain(
            "打印设置在哪",
            deps,
            scripted(call("answer_kb"), "完毕"),
        )

    kinds = [c["type"] for c in chunks]
    assert "data-images" in kinds
    assert kinds.index("data-images") < kinds.index("text-delta")
    assert deps.images_sent is True  # 路由层据此不再发第二遍


# ---------- 6：一轮只允许一次终结答案 ----------


async def test_second_answer_kb_call_is_refused(maker, public_chunk):
    """两次调用会毁掉第一次的引用编号——那批 [1][2] 已经连着正文发出去了。"""
    from types import SimpleNamespace

    async with maker() as s:
        deps = _deps(s)
        first = await answer_kb(SimpleNamespace(deps=deps))
        assert "已经把答案直接给用户了" in first

        before = deps.final_answer
        second = await answer_kb(SimpleNamespace(deps=deps))

    assert "已经回答过" in second
    assert deps.final_answer == before  # 没有被第二次调用覆盖


# ---------- 7：限额按路径分 ----------


def test_usage_limits_are_tighter_for_plain_questions():
    """普通问答的正常形态是「决策 → answer_kb → 结束」。给 8 次等于允许它
    多烧 6 次才被拦住，而用户全程只看到一个转圈。"""
    from copilot.agent.agent import usage_limits

    qa = usage_limits()
    plan = usage_limits(plan_flow=True)
    assert qa.request_limit < plan.request_limit
    assert qa.tool_calls_limit < plan.tool_calls_limit
