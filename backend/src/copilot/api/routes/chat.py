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
import logging
import time
import uuid
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import FileResponse, StreamingResponse

from copilot import usage
from copilot.api import providers, stream
from copilot.api.schemas import ChatRequest, ConversationOut, MessageOut
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import Conversation, Message
from copilot.db.session import SessionLocal
from copilot.qa import HISTORY_TURNS, ask_stream, is_no_answer

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


async def _chat_stream(
    user_id: uuid.UUID, question: str, client_id: str | None
) -> AsyncIterator[str]:
    message_id = stream.new_id("msg")
    text_id = stream.new_id("txt")
    text_open = False

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

            streamed = await ask_stream(
                session,
                question,
                providers.get_embedder(),
                providers.get_reranker(),
                providers.get_llm(),
                user_id=user_id,
                history=history,
            )

            # 配图**在正文之前**发，和引用相反。因为前端要边流边把 [图1] 换成
            # 真图，拿不到对照表就只能干等。这不违反第 1 条：图片不构成"答案有
            # 依据"的暗示——模型说「暂无此内容」时正文里根本没有 [图N]，
            # 什么都不会渲染。
            if streamed.images:
                yield stream.data_part("images", {"images": streamed.images})

            text_stream = streamed.stream
            citations = streamed.citations

            yield stream.text_start(text_id)
            text_open = True
            buf: list[str] = []
            row: Message | None = None  # 助手那条消息，第一次落盘时才建

            async def flush(*, final: bool, shown: list | None = None) -> None:
                """把已经吐出来的部分写进库。

                ⭐ **不能等流结束再写。** 用户点「停止生成」时任务被取消，而
                Python **不会立刻**关掉这个异步生成器——它要等 GC 去 finalize，
                可能很久以后，也可能进程重启就没了。所以原来写在循环之后的落库
                根本不保证执行：线上表现是刷新页面只剩一个孤零零的提问。

                没写完的内容**必须标出来**：一段写到一半的 ERP 操作步骤看上去和
                写完的一模一样，用户照着做到第 3 步才发现没有第 4 步——那时候
                他已经在生产环境里点下去了。
                """
                nonlocal row
                text = "".join(buf)
                if not text.strip():
                    return  # 一个字都没吐出来，不留空消息
                content = text if final else text + INTERRUPTED_MARK
                if row is None:
                    row = Message(conversation_id=conv.id, role="assistant", content=content)
                    session.add(row)
                else:
                    row.content = content
                if final:
                    row.citations = shown or None
                    # 图片跟着答案一起存，否则重新载入历史时 [图1] 会变成裸标记
                    row.images = (streamed.images or None) if shown else None
                await session.commit()

            last_flush = time.monotonic()
            try:
                async for piece in iterate_in_threadpool(text_stream):
                    buf.append(piece)
                    yield stream.text_delta(text_id, piece)
                    if time.monotonic() - last_flush >= FLUSH_SECONDS:
                        await flush(final=False)
                        last_flush = time.monotonic()
            except (asyncio.CancelledError, GeneratorExit):
                # 取消**有时候**能立刻送到这里，那就顺手补一次，把丢失降到 0。
                # `shield=True` 不能省：任务已经被取消，不挡一下的话下面的
                # await 会立刻再抛一次 CancelledError，等于没写
                with anyio.CancelScope(shield=True):
                    await flush(final=False)
                raise
            yield stream.text_end(text_id)
            text_open = False

            answer = "".join(buf)
            # ⚠️ 见文件头第 1 条：说了"不知道"就不能挂来源
            shown = [] if is_no_answer(answer) else [c.to_dict() for c in citations]
            if shown:
                yield stream.data_part("citations", {"citations": shown})

            await flush(final=True, shown=shown)

            # 记账。算的是「送进去的 + 吐出来的」——**上下文才是大头**
            # （5 块材料约 2500 字，答案往往只有它的三成）
            await usage.record(
                session,
                user_id,
                usage.estimate_tokens(streamed.context_text, question, answer),
            )

    except Exception:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("聊天流出错：user=%s question=%r", user_id, question[:80])
        if text_open:
            yield stream.text_end(text_id)
        yield stream.error(GENERIC_ERROR)

    yield stream.finish_step()
    yield stream.finish()
    yield stream.DONE


async def _agent_stream(
    user_id: uuid.UUID, question: str, client_id: str | None
) -> AsyncIterator[str]:
    """M7 的 Agent 路径。默认不走这条（`agent_enabled`）。

    和直路的差别只在中间那段：这里由 Agent 决定检索几次、要不要追问、
    要不要出方案。前后的规矩完全一样——会话落库、说了不知道就不挂来源、
    记 token 账。

    ⚠️ **多轮状态必须进出数据库。** `profile` / `checklist` 存在 conversations 上，
    进来读、出去写。放内存里的话，用户答完第二个问题，第一个答案就没了。
    """
    from copilot.agent.checklist import Checklist, Requirement
    from copilot.agent.deps import AgentDeps
    from copilot.agent.runner import run_agent_stream, to_message_history

    message_id = stream.new_id("msg")
    yield stream.start(message_id)
    yield stream.start_step()

    answer = ""
    try:
        async with SessionLocal() as session:
            conv = await _resolve_conversation(session, user_id, client_id, question)
            history_rows = list(
                (
                    await session.execute(
                        select(Message.role, Message.content)
                        .where(Message.conversation_id == conv.id)
                        .order_by(Message.created_at, Message.id)
                        .limit(20)  # 只带最近的，上下文撑爆了对「接着聊」也没帮助
                    )
                ).all()
            )
            session.add(Message(conversation_id=conv.id, role="user", content=question))
            await session.commit()

            yield stream.data_part("conversation", {"id": str(conv.id), "title": conv.title})

            deps = AgentDeps(
                session=session,
                user_id=user_id,
                conversation_id=conv.id,
                embedder=providers.get_embedder(),
                reranker=providers.get_reranker(),
                profile=Requirement(**(conv.profile or {})),
                checklist=Checklist(**conv.checklist) if conv.checklist else None,
            )

            async for part, so_far in run_agent_stream(
                question, deps, to_message_history([(r, c) for r, c in history_rows])
            ):
                answer = so_far
                yield part

            # 配图在正文之后发（和直路相反）：Agent 边跑边检索，
            # 开始流的时候还不知道会用到哪些图
            if deps.images:
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
            session.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=answer,
                    citations=shown or None,
                    images=(deps.images or None) if shown else None,
                )
            )
            await session.commit()

            # ⚠️ 要把检索到的材料算进去。只算问题和答案会漏掉八成——
            # Agent 一轮可能检索好几次，那些材料每一份都进了模型的上下文
            await usage.record(
                session,
                user_id,
                usage.estimate_tokens(deps.context_text, question, answer),
            )

    except Exception:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("Agent 流出错：user=%s question=%r", user_id, question[:80])
        yield stream.error(GENERIC_ERROR)

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


async def _use_agent(session: AsyncSession, user, question: str, client_id: str | None) -> bool:
    """这一轮走 Agent 还是走直路。

    ⭐ **依据是数字，不是偏好。** M8 的 41 题评测上（`eval/results/`）：

        指标        直路      Agent
        准确率      100%      87.8%
        幻觉率        0%      12.5%
        检索命中率   100%      93.9%

    Agent 自己决定检索词，命中率反而更低，还会把相邻主题的材料凑进答案
    （典型：拿「得物」的面单步骤回答「京东」的问题）。所以**普通问答一律走直路**，
    Agent 只接它独有的那件事：多轮收集需求 + 出方案 + 导出 xlsx。

    两个入口：
      1. 问题里出现意图词（「帮我出个实施方案」）
      2. **这条会话已经在走 Agent 了**（`profile is not None`）。这条不能少——
         少了它，用户答完第一个追问，第二轮就被路由回直路，
         Agent 那边的状态就断了。

    ⚠️ 判据是 `profile is not None`，**不是「profile 有内容」**。
    第一轮 Agent 往往只是提个问题、一个字段都没记，此时 profile 是 `{}`。
    按「有内容」判的话第二轮就掉回直路——线上实测踩到过：
    用户答「淘宝和抖音两个平台」，回来的是一句「根据参考材料，无法回答」。
    """
    if get_settings().agent_enabled:
        return True  # 总开关：留给评测和将来验证用
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
async def chat(body: ChatRequest, user: CurrentUser, session: SessionDep) -> StreamingResponse:
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

    producer = _agent_stream if await _use_agent(session, user, question, body.id) else _chat_stream
    return StreamingResponse(
        producer(user.id, question, body.id),
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
) -> list[Message]:
    conv = await session.get(Conversation, conversation_id)
    # 别人的会话一律当成不存在，不用 403——403 等于告诉对方"这个 id 是有效的"
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list((await session.execute(stmt)).scalars())


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
