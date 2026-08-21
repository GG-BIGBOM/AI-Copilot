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
import re
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
from copilot.agent.guard import looks_like_kb_answer
from copilot.api import stream
from copilot.config import get_settings
from copilot.qa import NO_ANSWER, asks_about_subject

logger = logging.getLogger(__name__)

# 工具名 → 给用户看的说法。前端拿 toolName 直接显示的话，
# 用户看到的是 `answer_kb` 这种内部标识
TOOL_LABELS = {
    "answer_kb": "查知识库",
    "current_time": "查当前时间",
    "whoami": "自我介绍",
    "my_documents": "查我的文档",
    "search_kb": "检索知识库",
    "save_requirement": "记录需求",
    "generate_plan": "生成配置方案",
    "export_excel": "导出 Excel",
}

# 事件与工具正文汇进同一个 channel 的缓冲大小。满了之后写入方（工具）会被
# 挡住等消费者——那正是我们要的背压：前端读得慢时不该在内存里堆无限多的字
_CHANNEL_SIZE = 256

# 历史里单条助手回答带进上下文的长度上限。和直路的 `_HISTORY_CHAR_LIMIT`
# 是同一个数量级、同一个理由：够听懂上下文就行，不该够"照着答"
HISTORY_ANSWER_LIMIT = 400
# 历史里要抹掉的引用与配图标记
_MARK_RE = re.compile(r"\[(?:\d{1,2}|图\d+)\]")
_EARLIEST_HISTORY_RE = re.compile(r"(第一个|最开始|一开始).{0,8}(问题|问的|说的)")


def to_message_history(rows: list[tuple[str, str]]) -> list[ModelRequest | ModelResponse]:
    """把库里的 (role, content) 历史转成 Pydantic AI 的消息。

    ⚠️ **多轮追问全靠它。** 每个 HTTP 请求都是一次全新的 Agent run，
    不把历史带上，Agent 就会把刚问过的问题再问一遍——用户体验上就是
    「这机器人失忆了」。

    只带 user / assistant 的**正文**，不带工具调用记录：工具结果往往很长
    （一次检索几千字），全带上会让上下文迅速撑爆，而对「接着聊」没有帮助。

    ⚠️ **助手那半边要截断、还要抹掉 `[n]` 和 `[图n]`。** 上一轮那段几百字的
    答案原样带进来，模型看着它就够"答"下一句了，于是跳过检索——实测撞到过
    （见 guard.py 文件头）。而那些编号在新一轮里根本无效：`citations` 是
    每轮重建的，抄过去的 `[3]` 指向的是上一轮的来源。
    历史的用处是**听懂这一句在问什么**，不是当材料用。"""
    out: list[ModelRequest | ModelResponse] = []
    for role, content in rows:
        text = (content or "").strip()
        if not text:
            continue
        if role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        elif role == "assistant":
            trimmed = _MARK_RE.sub("", text)[:HISTORY_ANSWER_LIMIT]
            out.append(ModelResponse(parts=[TextPart(content=trimmed)]))
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
    # 本轮调过哪些工具。硬防线要用——见下面那段注释。
    # ⚠️ **挂在 deps 上，不是局部变量**：路由层要拿它写 request_trace
    # （M11 P1）。两边读同一份，才不会出现「防线看到调了、台账写着没调」
    used_tools: set[str] = deps.used_tools
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
                for part in _translate(payload, drafted, used_tools):
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
        if deps.history_truncated and _EARLIEST_HISTORY_RE.search(deps.question):
            # 当前窗口的第一条不等于整段会话第一条。让模型猜会制造一段看似确定的
            # 假记忆；这里用结构化状态直接说明边界。
            text = "当前上下文只保留最近几轮，我无法确认你最开始问的是什么。"

        # ⭐ 模型漏调 `answer_kb` 时不要只会拒答。线上 20 组多轮验收里，
        # 「那个要先审核吗」「再说详细点」等追问都在这里变成了无工具拒答。
        # 对非方案流，把这类结果结构化地送回**现有的同一条直路**；不是让
        # Agent 自己补写，也不是再造一套检索。
        should_retrieve = not used_tools and not deps.profile.filled() and (
            text == NO_ANSWER
            or asks_about_subject(deps.question)
            or looks_like_kb_answer(
                text, operational_only=get_settings().allow_general_knowledge
            )
        )
        if should_retrieve:
            from copilot.agent.tools import answer_kb_for_deps

            used_tools.add("answer_kb")
            # 工具正常是边生成边 emit；这里是模型已经走完之后的安全回退，
            # 先完整生成，再按「图片 → 正文」顺序补进同一条 UI 流。
            deps.emit = None
            await answer_kb_for_deps(deps)
            if deps.images:
                deps.images_sent = True
                yield stream.data_part("images", {"images": deps.images}), so_far()
            replacement = deps.final_answer or NO_ANSWER
            tid = stream.new_id("txt")
            yield stream.text_start(tid), so_far()
            answer.append(replacement)
            yield stream.text_delta(tid, replacement), so_far()
            yield stream.text_end(tid), so_far()
            return

        # ⭐ **硬防线**：这一轮**一个工具都没调**，却写出了一段像知识库答案的
        # 东西——那只可能是编的、或者是从上一轮的历史里抄的
        # （见 guard.py 文件头的实测）。换成兜底话术：宁可什么都不说，
        # 也不给一段无据可查的 ERP 操作步骤。
        #
        # ⚠️ `used_tools` 这个前提不能省。`generate_plan` 之后 Agent 会写一段
        # 带界面路径的方案摘要，长得和越线的一模一样，但它有据——据在刚跑完的
        # 那个工具里。少了这个判断，整条出方案流程会变成「知识库暂无此内容」。
        # ⚠️ **常识兜底打开时，这道防线只拦「像操作步骤」的那一半**（M12）。
        #
        # 2026-08-20 线上实测：用户追问「品牌方又是什么」，模型没调工具、
        # 写了一段正确的行业概念解释，被这里整段换成了「知识库暂无此内容」。
        # 事后查证：知识库里**确实没有**这个概念的定义，上一轮召回的 5 块材料
        # 也一个「品牌方」都没有——换句话说怎么修检索都救不回来，
        # 而防线把一个正当的问题变成了一句拒答。
        #
        # 所以判据从「像不像知识库答案」收窄成「像不像**操作步骤**」：
        # 带界面路径、菜单层级、[n] 引用标记的，仍然一律拦下（那是真会伤人的
        # 那一种）；纯概念解释放行。开关关掉时行为和以前一模一样。
        if not used_tools and looks_like_kb_answer(
            text, operational_only=get_settings().allow_general_knowledge
        ):
            logger.warning(
                "越过工具直答，已拦下：question=%r answer=%r", deps.question[:60], text[:120]
            )
            text = NO_ANSWER
        tid = stream.new_id("txt")
        yield stream.text_start(tid), so_far()
        answer.append(text)
        yield stream.text_delta(tid, text), so_far()
        yield stream.text_end(tid), so_far()


def _translate(event: object, drafted: list[str], used_tools: set[str] | None = None) -> list[str]:
    """一个 Pydantic AI 事件 → 零到多个 SSE 片段。

    Agent 自己的正文不在返回值里——它进 `drafted`，由调用方决定要不要发；
    调过的工具名记进 `used_tools`，硬防线要用。
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
            if used_tools is not None:
                used_tools.add(part.tool_name)
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
