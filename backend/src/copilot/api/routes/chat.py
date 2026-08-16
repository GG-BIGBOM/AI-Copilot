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

import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import StreamingResponse

from copilot.api import providers, stream
from copilot.api.schemas import ChatRequest, ConversationOut, MessageOut
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.db.models import Conversation, Message
from copilot.db.session import SessionLocal
from copilot.qa import ask_stream, is_no_answer

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
            session.add(Message(conversation_id=conv.id, role="user", content=question))
            await session.commit()

            # 前端据此知道这轮落在哪条会话上（服务端可能没沿用它传来的 id）
            yield stream.data_part("conversation", {"id": str(conv.id), "title": conv.title})

            text_stream, citations = await ask_stream(
                session,
                question,
                providers.get_embedder(),
                providers.get_reranker(),
                providers.get_llm(),
                user_id=user_id,
            )

            yield stream.text_start(text_id)
            text_open = True
            buf: list[str] = []
            async for piece in iterate_in_threadpool(text_stream):
                buf.append(piece)
                yield stream.text_delta(text_id, piece)
            yield stream.text_end(text_id)
            text_open = False

            answer = "".join(buf)
            # ⚠️ 见文件头第 1 条：说了"不知道"就不能挂来源
            shown = [] if is_no_answer(answer) else [c.to_dict() for c in citations]
            if shown:
                yield stream.data_part("citations", {"citations": shown})

            session.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=answer,
                    citations=shown or None,
                )
            )
            await session.commit()

    except Exception:  # noqa: BLE001 —— 流已经开始了，异常不能再变成 HTTP 状态码
        logger.exception("聊天流出错：user=%s question=%r", user_id, question[:80])
        if text_open:
            yield stream.text_end(text_id)
        yield stream.error(GENERIC_ERROR)

    yield stream.finish_step()
    yield stream.finish()
    yield stream.DONE


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser) -> StreamingResponse:
    """提问，流式返回答案与引用。未登录 401。"""
    question = body.last_user_text()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有找到用户消息")

    return StreamingResponse(
        _chat_stream(user.id, question, body.id),
        media_type="text/event-stream",
        headers=stream.SSE_HEADERS,
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
