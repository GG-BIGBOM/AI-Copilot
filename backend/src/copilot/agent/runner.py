"""把 Pydantic AI 的事件流翻成 AI SDK 的 UI Message Stream。

两套协议之间只有一处对得上（文本增量），其余都要手工映射：

    PartStartEvent(TextPart)      → text-start
    PartDeltaEvent(TextPartDelta) → text-delta
    PartEndEvent(TextPart)        → text-end
    FunctionToolCallEvent         → tool-input-start + tool-input-available
    FunctionToolResultEvent       → tool-output-available / tool-output-error

三个坑，都会表现成「页面上安静地少了东西」而不是报错：

1. **一轮里可能有多个 TextPart。** Agent 先说一句「我查一下」、调工具、再接着答，
   那是两个独立的 TextPart，各自要有配套的 text-start / text-end，
   `id` 还得对得上。用同一个 id 复用的话，前端会把后一段拼到前一段里去；
   不发 text-end 就一直显示成「正在输入」。
2. **工具失败要发 `tool-output-error`，不要发 `error`。** 后者会让整轮变成错误态，
   而 Agent 明明还能继续（工具本身也把失败包成了正常返回值，见 tools.py）。
   这里处理的是 `RetryPromptPart`——参数填错、模型被要求重填的情形。
3. **`ThinkingPart` 要丢掉。** 推理模型会吐思维链，把它当正文发给前端等于
   把内部推理展示给用户。这里只认 TextPart。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    UserPromptPart,
)

from copilot.agent.agent import build_agent, usage_limits
from copilot.agent.deps import AgentDeps
from copilot.api import stream

logger = logging.getLogger(__name__)

# 工具名 → 给用户看的说法。前端拿 toolName 直接显示的话，
# 用户看到的是 `search_kb` 这种内部标识
TOOL_LABELS = {
    "search_kb": "检索知识库",
    "save_requirement": "记录需求",
    "generate_plan": "生成配置方案",
    "export_excel": "导出 Excel",
}


def to_message_history(rows: list[tuple[str, str]]) -> list[ModelRequest | ModelResponse]:
    """把库里的 (role, content) 历史转成 Pydantic AI 的消息。

    ⚠️ **多轮追问全靠它。** 每个 HTTP 请求都是一次全新的 Agent run，
    不把历史带上，Agent 就会把刚问过的问题再问一遍——用户体验上就是
    「这机器人失忆了」。

    只带 user / assistant 的**正文**，不带工具调用记录：工具结果往往很长
    （一次检索几千字），全带上会让上下文迅速撑爆，而对「接着聊」没有帮助。
    """
    out: list[ModelRequest | ModelResponse] = []
    for role, content in rows:
        text = (content or "").strip()
        if not text:
            continue
        if role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        elif role == "assistant":
            out.append(ModelResponse(parts=[TextPart(content=text)]))
    return out


async def run_agent_stream(
    question: str,
    deps: AgentDeps,
    history: list[ModelRequest | ModelResponse] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """跑一轮 Agent，产出 `(SSE 片段, 本轮已累积的正文)`。

    为什么第二个元素要一路带出来：调用方要在流结束后判断 `is_no_answer(全文)`
    来决定发不发引用（M1 的坑 #2），而正文是分成好几段吐出来的，
    只有这里知道完整的样子。
    """
    agent = build_agent()
    answer: list[str] = []
    open_text_ids: dict[int, str] = {}  # PartStartEvent 的 index → 我们发出去的 text id

    async with agent.run_stream_events(
        question,
        deps=deps,
        message_history=history or None,
        usage_limits=usage_limits(),
    ) as events:
        async for event in events:
            if isinstance(event, PartStartEvent):
                part = event.part
                if isinstance(part, TextPart):
                    tid = stream.new_id("txt")
                    open_text_ids[event.index] = tid
                    yield stream.text_start(tid), "".join(answer)
                    if part.content:  # 首块可能自带内容
                        answer.append(part.content)
                        yield stream.text_delta(tid, part.content), "".join(answer)
                # ToolCallPart / ThinkingPart 在这里不处理：
                # 工具走 FunctionToolCallEvent（那时参数才齐），思维链直接丢

            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta) and delta.content_delta:
                    tid = open_text_ids.get(event.index)
                    if tid is None:  # 理论上不会，兜一下别把内容丢了
                        tid = stream.new_id("txt")
                        open_text_ids[event.index] = tid
                        yield stream.text_start(tid), "".join(answer)
                    answer.append(delta.content_delta)
                    yield stream.text_delta(tid, delta.content_delta), "".join(answer)

            elif isinstance(event, PartEndEvent):
                if (tid := open_text_ids.pop(event.index, None)) is not None:
                    yield stream.text_end(tid), "".join(answer)

            elif isinstance(event, FunctionToolCallEvent):
                part = event.part
                if isinstance(part, ToolCallPart):
                    name = TOOL_LABELS.get(part.tool_name, part.tool_name)
                    cid = part.tool_call_id
                    yield stream.tool_input_start(cid, name), "".join(answer)
                    try:
                        args = part.args_as_dict()
                    except Exception:  # noqa: BLE001 - 参数没填成合法 JSON
                        args = {"_raw": str(part.args)[:200]}
                    yield stream.tool_input_available(cid, name, args), "".join(answer)

            elif isinstance(event, FunctionToolResultEvent):
                part = event.part
                cid = getattr(part, "tool_call_id", "") or ""
                if isinstance(part, RetryPromptPart):
                    # 模型被要求重填参数。发 tool-output-error 而不是 error：
                    # 整轮还能继续，前端只需把这一次调用标成失败
                    yield stream.tool_output_error(cid, "参数不对，正在重试"), "".join(answer)
                else:
                    # 工具返回可能是几千字的检索结果，全推给前端没意义——
                    # 它只需要知道「这步做完了、大概拿到了什么」
                    text = str(getattr(part, "content", "") or "")
                    brief = text if len(text) <= 300 else text[:300] + "…"
                    yield stream.tool_output_available(cid, {"summary": brief}), "".join(answer)

    # 收尾：正常情况下 PartEndEvent 会关掉所有 text，异常中断时补上，
    # 否则前端那条消息会永远停在「正在输入」
    for tid in list(open_text_ids.values()):
        yield stream.text_end(tid), "".join(answer)
