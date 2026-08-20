"""聊天接口：输出 AI SDK **UI Message Stream Protocol**（SSE）。

前端 `useChat({ transport: new DefaultChatTransport({ api: '/api/chat' }) })`
直接对接，流式、中断、重试都由 AI SDK 那边包掉。

三个不显眼但会咬人的设计决定：

1. **引用在正文之后才发。**
   M1 的坑 #2：`ask_stream` 为了让前端早点渲染来源，在生成前就把引用给出来了。
   可模型完全可能接着回一句「知识库暂无此内容」——那时页面上就是一句"不知道"
   底下挂着五条来源，用户会以为答案有依据。这比不做防幻觉更糟。
   把 `data-citations` 挪到正文流完之后，先过 `is_no_answer()` 再决定发不发，
   这个坑就从"靠自觉"变成"结构上不可能"。

2. **流里自己开数据库会话，不用 `Depends(get_session)`。**
   StreamingResponse 的响应体是在接口函数 return 之后才被消费的，
   那时请求作用域的依赖可能已经退出、会话已经关闭。
   自己 `async with SessionLocal()` 才不会踩到这个时序。

3. **LLM 的流是同步生成器**（httpx.Client），直接在协程里 for 会卡死事件循环。
   丢进 `iterate_in_threadpool` 转成异步迭代。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import FileResponse, StreamingResponse

from copilot import usage
from copilot.api import providers, stream
from copilot.api.schemas import (
    BulkDeleteRequest,
    BulkDeleteResult,
    ChatRequest,
    ConversationOut,
    MessageOut,
)
from copilot.api.trace import TraceDraft
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import Conversation, Message, RequestTrace
from copilot.db.session import SessionLocal
from copilot.qa import DEFAULT_MODE, HISTORY_TURNS, ask_stream, is_no_answer, small_talk_reply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])

TITLE_MAX = 40
# 给用户看的错误话术。真正的异常连堆栈进服务端日志——错误信息会原样渲染在
# 聊天框里，不该把内部细节（更别说密钥相关的报错正文）送到浏览器。
GENERIC_ERROR = "生成回答时出错了，请稍后重试。"


async def _resolve_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    client_id: str | None,
    question: str,
) -> Conversation:
    """定位或新建会话。

    前端传的 `id` 是 useChat 的会话 id。它是**客户端生成**的，所以：
      - 是合法 UUID 且属于当前用户  → 续用，多轮对话落在同一条会话里
      - 是合法 UUID 且不存在        → 就用它建，前端刷新后 id 还能对上
      - 已被别人占了 / 不是 UUID    → 服务端另发一个，绝不返回别人的会话

    第三条是关键：拿别人的 conversation id 来问，最坏结果只是自己多一条新会话，
    看不到也写不进对方的历史。

    ⚠️ 新建的会话必须 flush 后再用它的 id：`Conversation.id` 的 `default=uuid.uuid4`
    是**列默认值**，INSERT 时才求值。不 flush 的话 `conv.id` 还是 None，
    紧接着那条 Message 就会带着 conversation_id=NULL 插进去。
    """
    cid: uuid.UUID | None = None
    if client_id:
        try:
            cid = uuid.UUID(client_id)
        except ValueError:
            cid = None

    if cid is not None:
        existing = await session.get(Conversation, cid)
        if existing is not None:
            if existing.user_id == user_id:
                return existing
            cid = None  # 被别人占了，另发一个

    conv = Conversation(id=cid or uuid.uuid4(), user_id=user_id, title=_title_from(question))
    session.add(conv)
    await session.flush()
    return conv


def _title_from(question: str) -> str:
    q = " ".join(question.split())
    return q[:TITLE_MAX] if len(q) <= TITLE_MAX else q[: TITLE_MAX - 1] + "…"


INTERRUPTED_MARK = "\n\n（生成已中断，内容可能不完整）"
# 边流边落库的间隔。用户点停止最多丢这么多秒的内容
FLUSH_SECONDS = 2.0


async def _recent_turns(session: AsyncSession, conv_id: uuid.UUID) -> list[tuple[str, str]]:
    """这条会话最近几轮对话，从旧到新。

    ⭐ **必须在插入本轮提问之前调用**，否则历史里会混进用户刚问的那句，
    改写时等于让模型拿问题去补全问题本身。

    只取 user / assistant：tool 那类是 Agent 的内部记录，对直路没有意义。
    排序和 `list_messages` 保持一致（created_at, id）——同一毫秒落库的两条
    要有个稳定的先后，否则历史里的问答会偶发地颠倒。
    """
    stmt = (
        select(Message.role, Message.content)
        .where(Message.conversation_id == conv_id, Message.role.in_(("user", "assistant")))
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(HISTORY_TURNS)
    )
    rows = (await session.execute(stmt)).all()
    return [(role, content) for role, content in reversed(rows)]


class _AnswerWriter:
    """把正在流的答案边流边写进库。**两条路共用一份**（M10 P2）。

    ⭐ **不能等流结束再写。** 用户点「停止生成」时任务被取消，而 Python
    **不会立刻**关掉那个异步生成器——它要等 GC 去 finalize，可能很久以后，
    也可能进程重启就没了。所以写在循环之后的落库根本不保证执行：
    线上表现是刷新页面只剩一个孤零零的提问。

    没写完的内容**必须标出来**：一段写到一半的 ERP 操作步骤看上去和写完的
    一模一样，用户照着做到第 3 步才发现没有第 4 步——那时候他已经在生产
    环境里点下去了。

    > 这套逻辑原来只有直路有（M9 加的），Agent 路径点停止就只剩一个提问。
    > 「每加一个能力都只加在一条路上」正是 M10 要消灭的双路税，
    > 所以这里先抽成一份，再给两边用。
    """

    def __init__(self, session: AsyncSession, conversation_id: uuid.UUID) -> None:
        self.session = session
        self.conversation_id = conversation_id
        self.row: Message | None = None  # 第一次落盘时才建
        self._last = time.monotonic()

    @property
    def due(self) -> bool:
        """离上次落库够久了吗。用户点停止最多丢 `FLUSH_SECONDS` 秒的内容。"""
        return time.monotonic() - self._last >= FLUSH_SECONDS

    async def write(
        self,
        text: str,
        *,
        final: bool,
        citations: list | None = None,
        images: list | None = None,
    ) -> None:
        if not text.strip():
            return  # 一个字都没吐出来，不留空消息
        content = text if final else text + INTERRUPTED_MARK
        if self.row is None:
            self.row = Message(
                conversation_id=self.conversation_id, role="assistant", content=content
            )
            self.session.add(self.row)
        else:
            self.row.content = content
        if final:
            self.row.citations = citations or None
            # 图片跟着答案一起存，否则重新载入历史时 [图1] 会变成裸标记。
            # 没有引用就没有图——那种情况下正文里根本不会出现 [图N]
            self.row.images = (images or None) if citations else None
        await self.session.commit()
        self._last = time.monotonic()

    async def interrupted(self, text: str) -> None:
        """被取消时补最后一次。

        `shield=True` 不能省：任务已经被取消，不挡一下的话里面的 await
        会立刻再抛一次 CancelledError，等于没写。
        """
        with anyio.CancelScope(shield=True):
            await self.write(text, final=False)


async def _chat_stream(
    user_id: uuid.UUID,
    question: str,
    client_id: str | None,
    mode: str = DEFAULT_MODE,
    draft: TraceDraft | None = None,
) -> AsyncIterator[str]:
    draft = draft or TraceDraft(user_id=user_id, question=question, route="direct", mode=mode)
    message_id = stream.new_id("msg")
    text_id = stream.new_id("txt")
    text_open = False
    reason_id = stream.new_id("rsn")
    reason_open = False

    yield stream.start(message_id)
    yield stream.start_step()

    try:
        async with SessionLocal() as session:
            conv = await _resolve_conversation(session, user_id, client_id, question)
            # 先读历史再落本轮提问，顺序不能反（见 _recent_turns）
            history = await _recent_turns(session, conv.id)
            session.add(Message(conversation_id=conv.id, role="user", content=question))
            await session.commit()

            # 前端据此知道这轮落在哪条会话上（服务端可能没沿用它传来的 id）
            yield stream.data_part("conversation", {"id": str(conv.id), "title": conv.title})
            draft.conversation_id = conv.id
            # ⭐ trace id **在正文之前**发。前端点 👎 是在读到烂答案的第一秒，
            # 那时候流可能还没结束——等结束再发，那一秒就没有按钮可点
            yield stream.data_part("trace", {"id": str(draft.id)})

            streamed = await ask_stream(
                session,
                question,
                providers.get_embedder(),
                providers.get_reranker(),
                providers.get_llm_for(mode),
                user_id=user_id,
                history=history,
                mode=mode,
            )

            # 配图**在正文之前**发，和引用相反。因为前端要边流边把 [图1] 换成
            # 真图，拿不到对照表就只能干等。这不违反第 1 条：图片不构成"答案有
            # 依据"的暗示——模型说「暂无此内容」时正文里根本没有 [图N]，
            # 什么都不会渲染。
            if streamed.images:
                yield stream.data_part("images", {"images": streamed.images})

            text_stream = streamed.stream
            citations = streamed.citations
            draft.retrieval(citations)
            draft.private_hits = streamed.private_hits
            draft.model = getattr(providers.get_llm_for(mode), "model", None)

            buf: list[str] = []
            writer = _AnswerWriter(session, conv.id)

            try:
                async for kind, piece in iterate_in_threadpool(text_stream):
                    # ⭐ **推理草稿边出边发。**
                    # 详解档走的 kimi-k2.6 是推理模型，实测第一个草稿字 1 秒就到，
                    # 而**第一个正文字要 8~60 秒**。不发草稿的话，那几十秒前端
                    # 一个字都没有——用户看到的就是「选了详解，它不回答」。
                    # 草稿走 reasoning part，和正文分开：里面尽是「材料里没提到…」
                    # 这种自我推翻的话，混进正文就成了一条会骗人的答案。
                    if kind == "reasoning":
                        if not reason_open:
                            yield stream.reasoning_start(reason_id)
                            reason_open = True
                        yield stream.reasoning_delta(reason_id, piece)
                        continue

                    # ⭐ **第一个正文字到了才发 `text-start`。**
                    # 原来是在调模型之前就发，于是 AI SDK 立刻从 `submitted`
                    # 切到 `streaming`——前端那句「正在理解问题」消失，换成一条
                    # 空答案加一个闪烁光标。用户看到的就是「没有回答内容」。
                    if reason_open:
                        yield stream.reasoning_end(reason_id)
                        reason_open = False
                    if not text_open:
                        yield stream.text_start(text_id)
                        text_open = True
                    draft.first_token()  # 只认第一次，见 TraceDraft.first_token
                    buf.append(piece)
                    yield stream.text_delta(text_id, piece)
                    if writer.due:
                        await writer.write("".join(buf), final=False)
            except (asyncio.CancelledError, GeneratorExit) as exc:
                # 取消**有时候**能立刻送到这里，那就顺手补一次，把丢失降到 0
                await writer.interrupted("".join(buf))
                # ⭐ 被打断的这一轮**也要记账**。用户点停止本身就是一种反馈
                # （多半是答得不对或者太慢），而这恰恰是最该留下记录的一轮
                draft.failed(exc)
                draft.answer_chars = len("".join(buf))
                await draft.save()
                raise
            if reason_open:
                yield stream.reasoning_end(reason_id)
                reason_open = False
            if text_open:
                yield stream.text_end(text_id)
                text_open = False

            answer = "".join(buf)
            # ⚠️ 见文件头第 1 条：说了"不知道"就不能挂来源
            shown = [] if is_no_answer(answer) else [c.to_dict() for c in citations]
            if shown:
                yield stream.data_part("citations", {"citations": shown})

            await writer.write(
                answer, final=True, citations=shown, images=streamed.images
            )

            # 记账。算的是「送进去的 + 吐出来的」——**上下文才是大头**
            # （5 块材料约 2500 字，答案往往只有它的三成）
            tokens = usage.estimate_tokens(streamed.context_text, question, answer)
            await usage.record(session, user_id, tokens)

            draft.tokens = tokens
            draft.answer_chars = len(answer)
            draft.no_answer = not shown
            draft.message_id = writer.row.id if writer.row is not None else None

    except Exception as exc:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("聊天流出错：user=%s question=%r", user_id, question[:80])
        draft.failed(exc)
        if reason_open:
            yield stream.reasoning_end(reason_id)
        if text_open:
            yield stream.text_end(text_id)
        yield stream.error(GENERIC_ERROR)

    # ⚠️ 在 `finish` 之前落库，但**不挡着流**：这是一条 INSERT，
    # 失败了也只是记一行日志（见 trace.py 第 1 条）。放在 try 之外是因为
    # 出错那一支也要写——只记成功的请求，等于把最该看的那一半扔了
    await draft.save()

    yield stream.finish_step()
    yield stream.finish()
    yield stream.DONE


async def _agent_stream(
    user_id: uuid.UUID,
    question: str,
    client_id: str | None,
    mode: str = DEFAULT_MODE,
    draft: TraceDraft | None = None,
) -> AsyncIterator[str]:
    """M7 的 Agent 路径。默认不走这条（`agent_enabled`）。

    和直路的差别只在中间那段：这里由 Agent 决定检索几次、要不要追问、
    要不要出方案。前后的规矩完全一样——会话落库、说了不知道就不挂来源、
    记 token 账。

    ⚠️ **多轮状态必须进出数据库。** `profile` / `checklist` 存在 conversations 上，
    进来读、出去写。放内存里的话，用户答完第二个问题，第一个答案就没了。

    `mode` 收下但**暂时不用**：Agent 走的是 pydantic-ai 自己那套模型配置
    （见 agent/model.py），换档要连工具调用一起验，不能顺手加。
    参数留着是为了和 `_chat_stream` 同签名——路由那边是按同一个名字调的。
    """
    from copilot.agent.checklist import Checklist, Requirement
    from copilot.agent.deps import AgentDeps
    from copilot.agent.runner import run_agent_stream, to_message_history

    draft = draft or TraceDraft(user_id=user_id, question=question, route="agent", mode=mode)
    message_id = stream.new_id("msg")
    yield stream.start(message_id)
    yield stream.start_step()

    answer = ""
    try:
        async with SessionLocal() as session:
            conv = await _resolve_conversation(session, user_id, client_id, question)
            # 和直路用同一个函数取历史。**这里原来是 `order_by(created_at).limit(20)`，
            # 取的是最老的 20 条**——会话一长，带进上下文的就永远是开头那几轮，
            # 而「接着聊」要的恰恰是最近几轮。顺带也统一了截断口径（HISTORY_TURNS）。
            history = await _recent_turns(session, conv.id)
            session.add(Message(conversation_id=conv.id, role="user", content=question))
            await session.commit()

            yield stream.data_part("conversation", {"id": str(conv.id), "title": conv.title})
            draft.conversation_id = conv.id
            yield stream.data_part("trace", {"id": str(draft.id)})

            deps = AgentDeps(
                session=session,
                user_id=user_id,
                conversation_id=conv.id,
                embedder=providers.get_embedder(),
                reranker=providers.get_reranker(),
                # ⭐ M10：这三样是 `answer_kb` 跑直路要用的。缺了 llm 它直接
                # 不可用；缺了 history / mode 它就退化成「单轮 + 简答档」，
                # 而消灭这种双路差异正是 M10 的目的
                llm=providers.get_llm_for(mode),
                history=history,
                mode=mode,
                profile=Requirement(**(conv.profile or {})),
                checklist=Checklist(**conv.checklist) if conv.checklist else None,
            )

            writer = _AnswerWriter(session, conv.id)
            try:
                async for part, so_far in run_agent_stream(
                    question, deps, to_message_history(history)
                ):
                    if so_far and not answer:
                        draft.first_token()
                    answer = so_far
                    yield part
                    # 和直路同一套边流边落库（M10 P2）。Agent 路径原来没有，
                    # 点停止刷新页面就只剩一个提问
                    if writer.due:
                        await writer.write(answer, final=False)
            except (asyncio.CancelledError, GeneratorExit) as exc:
                await writer.interrupted(answer)
                draft.failed(exc)
                draft.tools = sorted(deps.used_tools)
                draft.answer_chars = len(answer)
                await draft.save()
                raise

            # 配图兜底。`answer_kb` 走的是终结工具，它在正文**之前**就把图发了
            # （和直路一致，前端要边流边把 [图1] 换成真图）；这里只兜住普通工具
            # 那条路——它边跑边检索，开始流的时候还不知道会用到哪些图
            if deps.images and not deps.images_sent:
                yield stream.data_part("images", {"images": deps.images})

            # ⚠️ 同直路：说了"不知道"就不能挂来源
            shown = [] if is_no_answer(answer) else deps.citations
            if shown:
                yield stream.data_part("citations", {"citations": shown})
            if deps.download_url:
                yield stream.data_part(
                    "download", {"url": deps.download_url, "name": "实施配置方案.xlsx"}
                )

            # ⚠️ 空字典也要写进去，**不能 `or None`**。
            # `profile is not None` 是「这条会话在走 Agent」的标记，而第一轮
            # Agent 通常只是提个问题、一个字段都没记——写成 None 的话，
            # 下一轮就被路由回直路，对话直接散掉（线上实测踩到过）。
            conv.profile = deps.profile.model_dump(exclude_none=True)
            if deps.checklist is not None:
                conv.checklist = deps.checklist.model_dump()
            if deps.download_url:
                conv.export_path = f"{user_id}/{conv.id}.xlsx"
            await writer.write(answer, final=True, citations=shown, images=deps.images)
            # ⚠️ 再提交一次。`writer.write` 在正文为空时会直接返回**不提交**，
            # 而上面那几行对 `conv` 的改动一个都不能丢——尤其是 `profile`：
            # 它是「这条会话在走 Agent」的标记，丢了下一轮就掉回直路，对话散掉。
            await session.commit()

            # ⚠️ 要把检索到的材料算进去。只算问题和答案会漏掉八成——
            # Agent 一轮可能检索好几次，那些材料每一份都进了模型的上下文
            tokens = usage.estimate_tokens(deps.context_text, question, answer)
            await usage.record(session, user_id, tokens)

            # ⭐ `tools` 是这条路**独有**的那一列，也是灰度期间最该盯的一列：
            # 空数组 + 一段像模像样的 ERP 答案 = 越过工具直答，
            # 那正是 `ca6fc82` 那道硬防线要拦的东西。验收标准第 8 条查的就是它
            draft.tools = sorted(deps.used_tools)
            draft.retrieval(deps.citations)
            draft.private_hits = deps.private_hits
            draft.tokens = tokens
            draft.answer_chars = len(answer)
            draft.no_answer = not shown
            draft.message_id = writer.row.id if writer.row is not None else None

    except Exception as exc:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("Agent 流出错：user=%s question=%r", user_id, question[:80])
        draft.failed(exc)
        yield stream.error(GENERIC_ERROR)

    await draft.save()

    yield stream.finish_step()
    yield stream.finish()
    yield stream.DONE


async def _canned_stream(
    user_id: uuid.UUID,
    question: str,
    client_id: str | None,
    mode: str = DEFAULT_MODE,
    draft: TraceDraft | None = None,
) -> AsyncIterator[str]:
    """招呼 / 道谢 / 告别 / 问能力——固定回复，**一次模型调用都不花**（M10 P2）。

    ⭐ **它是 Agent 之前的一层短路，不是第三条路由分叉。** 定位相当于缓存：
    命中就 0 成本、0 幻觉地返回；不命中就当它不存在。全 Agent 化之后
    （P3）这一层仍然留着——「你好」值不值得花一次模型调用，答案是不值得，
    而且让模型自由回招呼语，就是在防幻觉的墙上开一个洞：它会开始"友好地"
    补全 ERP 知识。这里回的每一句都是写死的常量。

    `mode` 收下不用：固定回复没有简答/详解之分。
    """
    draft = draft or TraceDraft(user_id=user_id, question=question, route="canned", mode=mode)
    reply = small_talk_reply(question) or ""
    message_id = stream.new_id("msg")
    text_id = stream.new_id("txt")
    yield stream.start(message_id)
    yield stream.start_step()

    try:
        async with SessionLocal() as session:
            conv = await _resolve_conversation(session, user_id, client_id, question)
            session.add(Message(conversation_id=conv.id, role="user", content=question))
            await session.commit()

            yield stream.data_part("conversation", {"id": str(conv.id), "title": conv.title})
            draft.conversation_id = conv.id
            yield stream.data_part("trace", {"id": str(draft.id)})
            yield stream.text_start(text_id)
            draft.first_token()
            yield stream.text_delta(text_id, reply)
            yield stream.text_end(text_id)

            # 不挂引用：固定回复没有出处，挂了就是假的
            writer = _AnswerWriter(session, conv.id)
            await writer.write(reply, final=True)
            # tokens 记 0——一个字都没送进模型。但请求数要记，否则运维看到的
            # 请求量会凭空少掉一截
            await usage.record(session, user_id, 0)

            # ⭐ 寒暄也记一行。**它是这张表里最便宜也最有用的一类样本**：
            # 「有多少提问其实只是打招呼」这件事，除了这里没有别处能看出来；
            # 而且它一次模型调用都不花——如果哪天这一类的占比很高，
            # 说明该做的是引导用户怎么提问，不是继续调模型
            draft.answer_chars = len(reply)
            draft.model = None  # 一个字都没送进模型
            draft.message_id = writer.row.id if writer.row is not None else None

    except Exception as exc:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("寒暄流出错：user=%s question=%r", user_id, question[:80])
        draft.failed(exc)
        yield stream.error(GENERIC_ERROR)

    await draft.save()

    yield stream.finish_step()
    yield stream.finish()
    yield stream.DONE


# 走 Agent 的意图词。**只认「要一份方案/清单」这一件事**——
# 那是 Agent 独有的能力（多轮追问 + 结构化输出 + 导出）。
# 别往这里加「怎么设置」「在哪里配」之类的词：那些是普通问答，
# 走直路更准（见 _use_agent 的数字）。
AGENT_TRIGGERS = (
    "实施方案",
    "配置方案",
    "实施清单",
    "配置清单",
    "上线清单",
    "上线方案",
    "实施配置",
    "配置检查表",
)


def in_agent_allowlist(email: str) -> bool:
    """这个人在 Agent 白名单里吗（M11 P4）。

    ⭐ **白名单先于百分比灰度，因为 n=3 上的百分比是自欺欺人。**
    线上只有 3 个真实账号，而分桶按 user_id 稳定哈希：`AGENT_ROLLOUT=0.2`
    最可能的结果是一个人都没进桶，0.5 也不过是掷三次硬币。
    观察不到样本的灰度不叫灰度，叫等。

    所以先在 `.env` 里点名两个人（自己 + 1 个熟人），真实用一周，
    看 `request_trace` 里这两个人的行：有没有「一个工具都没调却答了 ERP 问题」、
    有没有报错、首字时间掉了多少。**这一周的观察对象是那张表，不是感觉。**
    """
    return bool(email) and email.lower() in get_settings().agent_allow_email_set


def in_agent_bucket(user_id: uuid.UUID) -> bool:
    """这个用户在不在 Agent 灰度桶里（M10 P3）。

    ⭐ **按 user_id 稳定分桶，不是按请求随机。** 同一个人的同一条会话在两条路
    之间跳，多轮状态当场断掉；线上出问题时也归不了因——你不知道他刚才那一句
    走的是哪条。哈希取前 4 字节映射到 [0,1)，同一个人每次都落在同一侧。

    M10 P2 的评测（55 题，各跑两轮）：

        路径    准确率            幻觉  检索命中  引用正确
        直路    96.4 / 98.2%       0%     100%     100%
        Agent   94.5 / 100.0%      0%     100%     100%

    两条路的区间**完全重叠**——差异是模型在 temperature=0 下的抖动，不是架构。
    （逐题比对过：52/55 题上下文完全一致，失败题的引用来源、顺序、上下文
    与直路一模一样。）硬指标两轮全满，所以敢灰度；但正因为单轮判不出 2 个点
    的差距，**只能灰度，不能一把切**。
    """
    s = get_settings()
    if s.agent_enabled:
        return True  # 总开关：评测和本机验证用
    if s.agent_rollout <= 0:
        return False
    if s.agent_rollout >= 1:
        return True
    digest = hashlib.sha256(str(user_id).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < s.agent_rollout


async def _use_agent(session: AsyncSession, user, question: str, client_id: str | None) -> bool:
    """这一轮走 Agent 还是走直路。

    四个入口，前两个分别是 M11 和 M10 加的：

      -1. **这个用户在白名单里**（`AGENT_ALLOW_EMAILS`）—— M11 P4。
          n=3 的线上不做百分比灰度，改成点名，理由见 `in_agent_allowlist`。
      0. **这个用户在灰度桶里** —— 那就一律走 Agent，普通问答也走。
         这是 M10 的目标形态：路由交给模型，而不是关键词表。
         （用户到 20+ 之前这条实际上不会命中，`AGENT_ROLLOUT` 默认是 0。）
      1. 问题里出现意图词（「帮我出个实施方案」）
      2. **这条会话已经在走 Agent 了**（`profile is not None`）。这条不能少——
         少了它，用户答完第一个追问，第二轮就被路由回直路，
         Agent 那边的状态就断了。

    ⚠️ 1 和 2 是**灰度桶之外**那半边人的路由，M7 定的规矩原样留着。
    等灰度到 100% 且线上稳定，这两条连同 `AGENT_TRIGGERS`、`_chat_stream`
    一起删——**删掉那一堆才是 M10 的收益**，只加不删的话双路的税照收。

    ⚠️ 判据是 `profile is not None`，**不是「profile 有内容」**。
    第一轮 Agent 往往只是提个问题、一个字段都没记，此时 profile 是 `{}`。
    按「有内容」判的话第二轮就掉回直路——线上实测踩到过：
    用户答「淘宝和抖音两个平台」，回来的是一句「根据参考材料，无法回答」。

    > `AGENT_TRIGGERS` 是子串匹配，会误伤：「实施方案模板在哪里下载」问的是
    > 知识库里有没有这份文档，却被送进需求收集流程（路由评测 `routing-before`
    > 里那 2 道错题）。灰度桶里的用户不受这个影响——模型分得清。
    """
    # M11 P4：白名单先判。它排在哈希分桶**前面**是因为白名单是「点名」，
    # 而分桶是「碰运气」——被点到名的人不该再被一次哈希掷回直路
    if in_agent_allowlist(user.email):
        return True
    if in_agent_bucket(user.id):
        return True
    if any(kw in question for kw in AGENT_TRIGGERS):
        return True
    if not client_id:
        return False
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        return False
    conv = await session.get(Conversation, cid)
    return bool(conv and conv.user_id == user.id and conv.profile is not None)


@router.post("/chat")
async def chat(
    body: ChatRequest, request: Request, user: CurrentUser, session: SessionDep
) -> StreamingResponse:
    """提问，流式返回答案与引用。未登录 401，超出当日配额 429。

    ⭐ 配额检查必须在 `StreamingResponse` **之前**。流一旦开始，
    HTTP 状态码就已经发出去了，再想返回 429 也来不及——只能在流里塞一个
    error 片段，那对客户端（尤其是脚本）来说完全是另一回事。
    """
    question = body.last_user_text()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有找到用户消息")

    exceeded, used, quota = await usage.over_quota(session, user)
    if exceeded:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"今天的用量已达上限（{used}/{quota} tokens），明天再来或联系管理员调整。",
        )

    use_agent = await _use_agent(session, user, question, body.id)
    # ⭐ 寒暄短路（M10 P2）。**顺序不能反**：已经在多轮流程里的会话不走这条。
    # Agent 问完「要对接哪些平台？」，用户回一句「好的」——那个「好的」在
    # 寒暄表里，短路掉就变成「不客气，还有别的问题随时问」，流程当场断掉。
    if not use_agent and small_talk_reply(question) is not None:
        producer, route = _canned_stream, "canned"
    elif use_agent:
        producer, route = _agent_stream, "agent"
    else:
        producer, route = _chat_stream, "direct"

    # ⭐ 台账在这里就建好，因为**路由是在这里决定的**——
    # 「这一轮走的哪条路」是灰度期间最该看的一列，而只有这个函数知道答案。
    # 生产者不该自己猜自己是谁。
    #
    # `request_id` 从中间件挂上来的 `request.state` 取。用户截图报错时，
    # 凭这一列能把台账里的这一行和 journal 里的完整堆栈缝起来
    draft = TraceDraft(
        user_id=user.id,
        question=question,
        route=route,
        mode=body.mode,
        request_id=getattr(request.state, "request_id", None),
    )
    return StreamingResponse(
        producer(user.id, question, body.id, body.mode, draft),
        media_type="text/event-stream",
        headers=stream.SSE_HEADERS,
    )


@router.get("/conversations/{conversation_id}/export")
async def download_export(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> FileResponse:
    """下载 Agent 生成的《实施配置方案.xlsx》。

    别人的会话一律当**不存在**（404 而非 403），和 `list_messages` 同一个理由：
    403 等于告诉对方「这个 id 是有效的」。
    """
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or not conv.export_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有可下载的方案")

    try:
        path = get_settings().export_path(conv.export_path)
    except ValueError as e:
        logger.error("导出路径越界 conv=%s：%s", conversation_id, e)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有可下载的方案") from e
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件已不存在，请让助手重新导出")

    from copilot.agent.tools import conversation_export_name

    return FileResponse(
        path,
        filename=conversation_export_name(conversation_id),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------- 历史记录 ----------


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user: CurrentUser, session: SessionDep, limit: int = 50
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user.id)  # 只看自己的
        .order_by(desc(Conversation.created_at))
        .limit(min(limit, 200))
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MessageOut]:
    conv = await session.get(Conversation, conversation_id)
    # 别人的会话一律当成不存在，不用 403——403 等于告诉对方"这个 id 是有效的"
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    # ⭐ LEFT JOIN 上 `request_trace`，把 trace id 和已有的赞/踩带出来（M11 P2）。
    # 不带的话，👍👎 只在「答案刚生成出来的那一次」可用——trace id 是随 SSE
    # 发给前端的，刷新一次就没了。而用户很常见的行为恰恰是回头翻历史、
    # 看到一条当时没细看的烂答案，那时候才想点踩。
    #
    # 用 outerjoin 不用子查询：一次扫描，且 `message_id` 上没有唯一约束时
    # 也不会因为多行而报错（重复的极少，取到哪一条都能复现同一轮）。
    stmt = (
        select(Message, RequestTrace.id, RequestTrace.feedback)
        .outerjoin(RequestTrace, RequestTrace.message_id == Message.id)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    out: list[MessageOut] = []
    for msg, trace_id, feedback in (await session.execute(stmt)).all():
        row = MessageOut.model_validate(msg)
        row.trace_id = trace_id
        row.feedback = feedback
        out.append(row)
    return out


@router.post("/conversations/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete_conversations(
    body: BulkDeleteRequest, user: CurrentUser, session: SessionDep
) -> BulkDeleteResult:
    """一次删掉多段会话。

    ⭐ **路由必须写在 `/conversations/{conversation_id}` 前面。** 否则
    `bulk-delete` 会先被那条路径匹配上，然后卡在 uuid 解析上返回 422——
    而错误信息完全指不到这里。

    别人的 id 混进来时**静默跳过**，不报错也不告诉你它存不存在：
    报错等于给了一个「拿 uuid 探别人有没有这段会话」的探针，
    和单条删除用 404 而不是 403 是同一个理由。所以返回的是
    「真的删掉了几条」，而不是「你传的每一条分别怎么样」。
    """
    if not body.ids:
        return BulkDeleteResult(deleted=0)

    stmt = select(Conversation).where(
        Conversation.id.in_(body.ids), Conversation.user_id == user.id
    )
    convs = list((await session.execute(stmt)).scalars())

    # 先把导出文件的路径记下来：会话删掉之后就查不到了
    export_paths = [c.export_path for c in convs if c.export_path]
    for conv in convs:
        await session.delete(conv)
    await session.commit()

    # 文件在提交之后才删，理由同单条删除
    for rel in export_paths:
        try:
            get_settings().export_path(rel).unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            logger.warning("批量删除时清理导出文件失败 %s：%s", rel, e)

    return BulkDeleteResult(deleted=len(convs))


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> None:
    """删除一整段会话，连消息和 Agent 导出的 xlsx 一起。

    别人的会话仍然一律当**不存在**（404 而非 403），和上面两个读接口同一个理由。
    这条尤其重要：删除接口如果用 403 区分「存在但不是你的」，就等于给了一个
    「拿 uuid 探别人有没有这段会话」的探针。

    消息不用手动删——`Message.conversation_id` 是 `ondelete="CASCADE"`。
    但**导出的文件必须自己删**：它在 `data/exports/<user_id>/` 下，
    数据库里没有任何东西再指向它，留着就是永远不会被回收的孤儿文件。
    """
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    export_path = conv.export_path
    await session.delete(conv)
    await session.commit()

    # 文件在**提交之后**才删，和 docs.py 删文档同一个顺序：
    # 反过来的话一旦提交失败，库里的会话还在、xlsx 已经没了，
    # 用户点下载会得到一个「文件已不存在」而不知道为什么
    if export_path:
        try:
            get_settings().export_path(export_path).unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            logger.warning("删除导出文件失败 conv=%s %s：%s", conversation_id, export_path, e)
