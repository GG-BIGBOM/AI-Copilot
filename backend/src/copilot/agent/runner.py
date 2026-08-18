"""把 Pydantic AI 的事件流翻成 AI SDK 的 UI Message Stream。

两套协议之间只有一处对得上（文本增量），其余都要手工映射：

    PartStartEvent / PartDeltaEvent(Text) → 攒进 drafted，见下
    FunctionToolCallEvent                 → tool-input-start + tool-input-available
    FunctionToolResultEvent               → tool-output-available / tool-output-error
    终结工具吐的正文                        → text-start / text-delta / text-end

M10 之后这里还多了一件事：**终结工具的正文要直通前端**。`answer_kb` 在工具
内部就把答案生成完了，那些字不经过 Agent 的笔，直接变成 text-delta。
两个由此而来的机制，都不是可选的：

- **事件泵放在独立 task 里。** 工具是在 Agent 的循环内部跑的；它往一个没人
  接收的 channel 写，就会和「正在等 Agent 出下一个事件」的消费者互相死等。
  所以让 Agent 在自己的 task 里跑，事件和工具正文汇进同一个 channel，
  这里只负责读。用 `asyncio.create_task` 而不是 anyio 的 task group：
  task group 跨 `yield` 会撞上「cancel scope in a different task」，
  而这个生成器**确实会被取消**（用户点停止生成，见 routes/chat.py 的 flush）。
- **Agent 自己写的正文要先攒着。** 它可能先说一句「我查一下」再调工具，
  发出去就收不回来了——用户会看到一句废话顶在答案前面。所以攒到本轮结束：
  有终结答案就整个丢掉，没有才吐出来（追问、时间、闲聊都属于后者，都很短）。

三个老坑，都会表现成「页面上安静地少了东西」而不是报错：

1. **每一段正文都要有配套的 text-start / text-end，`id` 还得对得上。**
   用同一个 id 复用的话，前端会把后一段拼到前一段里去；
   不发 text-end 就一直显示成「正在输入」。
2. **工具失败要发 `tool-output-error`，不要发 `error`。** 后者会让整轮变成错误态，
   而 Agent 明明还能继续（工具本身也把失败包成了正常返回值，见 tools.py）。
   这里处理的是 `RetryPromptPart`——参数填错、模型被要求重填的情形。
3. **`ThinkingPart` 要丢掉。** 推理模型会吐思维链，把它当正文发给前端等于
   把内部推理展示给用户。这里只认 TextPart。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import anyio
from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
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
# 用户看到的是 `answer_kb` 这种内部标识
TOOL_LABELS = {
    "answer_kb": "查知识库",
    "current_time": "查当前时间",
    "search_kb": "检索知识库",
    "save_requirement": "记录需求",
    "generate_plan": "生成配置方案",
    "export_excel": "导出 Excel",
}

# 事件与工具正文汇进同一个 channel 的缓冲大小。满了之后写入方（工具）会被
# 挡住等消费者——那正是我们要的背压：前端读得慢时不该在内存里堆无限多的字
_CHANNEL_SIZE = 256


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
    *,
    model=None,
) -> AsyncIterator[tuple[str, str]]:
    """跑一轮 Agent，产出 `(SSE 片段, 本轮已累积的正文)`。

    为什么第二个元素要一路带出来：调用方要在流结束后判断 `is_no_answer(全文)`
    来决定发不发引用（M1 的坑 #2），而正文是分成好几段吐出来的，
    只有这里知道完整的样子。

    Args:
        model: 只给测试用，见 `build_agent`。
    """
    agent = build_agent(model)
    deps.question = question

    send, recv = anyio.create_memory_object_stream[tuple[str, object]](_CHANNEL_SIZE)

    async def emit(kind: str, payload: object) -> None:
        await send.send((kind, payload))

    deps.emit = emit

    async def pump() -> None:
        """在自己的 task 里把 Agent 跑完，事件全部塞进 channel。"""
        try:
            async with agent.run_stream_events(
                question,
                deps=deps,
                message_history=history or None,
                usage_limits=usage_limits(plan_flow=bool(deps.profile.filled())),
            ) as events:
                async for event in events:
                    await send.send(("event", event))
        except Exception as e:  # noqa: BLE001 - 交给消费者决定怎么呈现
            await send.send(("error", e))
        finally:
            send.close()

    task = asyncio.create_task(pump())

    # 终结工具的正文用一个独立的 text id，和 Agent 自己写的那段分开
    final_id: str | None = None
    # Agent 自己写的正文，先攒着（见文件头）
    drafted: list[str] = []
    answer: list[str] = []
    failure: Exception | None = None

    def so_far() -> str:
        return "".join(answer)

    try:
        async for kind, payload in recv:
            if kind == "text":
                if final_id is None:
                    final_id = stream.new_id("txt")
                    yield stream.text_start(final_id), so_far()
                chunk = str(payload)
                answer.append(chunk)
                yield stream.text_delta(final_id, chunk), so_far()

            elif kind == "images":
                deps.images_sent = True
                yield stream.data_part("images", {"images": deps.images}), so_far()

            elif kind == "error":
                failure = payload if isinstance(payload, Exception) else RuntimeError(payload)
                break

            else:
                for part in _translate(payload, drafted):
                    yield part, so_far()

    finally:
        # 消费者可能提前离场（用户点了停止），别把 Agent 的 task 留在后台跑
        task.cancel()

    if final_id is not None:
        yield stream.text_end(final_id), so_far()

    if failure is not None:
        raise failure

    # 没有终结答案时，才把 Agent 自己写的那段吐出来：追问、时间、闲聊。
    # 有终结答案的话这段一定是复述或「希望对你有帮助」，丢掉正好。
    if deps.final_answer is None and (text := "".join(drafted).strip()):
        tid = stream.new_id("txt")
        yield stream.text_start(tid), so_far()
        answer.append(text)
        yield stream.text_delta(tid, text), so_far()
        yield stream.text_end(tid), so_far()


def _translate(event: object, drafted: list[str]) -> list[str]:
    """一个 Pydantic AI 事件 → 零到多个 SSE 片段。

    Agent 自己的正文不在返回值里——它进 `drafted`，由调用方决定要不要发。
    """
    out: list[str] = []

    if isinstance(event, PartStartEvent):
        part = event.part
        if isinstance(part, TextPart) and part.content:  # 首块可能自带内容
            drafted.append(part.content)
        # ToolCallPart / ThinkingPart 在这里不处理：
        # 工具走 FunctionToolCallEvent（那时参数才齐），思维链直接丢

    elif isinstance(event, PartDeltaEvent):
        delta = event.delta
        if isinstance(delta, TextPartDelta) and delta.content_delta:
            drafted.append(delta.content_delta)

    elif isinstance(event, FunctionToolCallEvent):
        part = event.part
        if isinstance(part, ToolCallPart):
            name = TOOL_LABELS.get(part.tool_name, part.tool_name)
            cid = part.tool_call_id
            out.append(stream.tool_input_start(cid, name))
            try:
                args = part.args_as_dict()
            except Exception:  # noqa: BLE001 - 参数没填成合法 JSON
                args = {"_raw": str(part.args)[:200]}
            out.append(stream.tool_input_available(cid, name, args))

    elif isinstance(event, FunctionToolResultEvent):
        part = event.part
        cid = getattr(part, "tool_call_id", "") or ""
        if isinstance(part, RetryPromptPart):
            # 模型被要求重填参数。发 tool-output-error 而不是 error：
            # 整轮还能继续，前端只需把这一次调用标成失败
            out.append(stream.tool_output_error(cid, "参数不对，正在重试"))
        else:
            # 工具返回可能是几千字的检索结果，全推给前端没意义——
            # 它只需要知道「这步做完了、大概拿到了什么」
            text = str(getattr(part, "content", "") or "")
            brief = text if len(text) <= 300 else text[:300] + "…"
            out.append(stream.tool_output_available(cid, {"summary": brief}))

    return out
