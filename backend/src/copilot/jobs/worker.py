"""后台 worker：解析上传的文档并入库。

单独一个进程（`copilot worker` / `copilot-worker.service`），不塞进 API 进程里。
两个理由，都是 1.6GB 逼出来的：

1. 解析一份 20MB 的 pptx 是**同步的 CPU 活**，塞进 API 进程就会卡住事件循环，
   而那个进程只有一个 worker——别人正在流的答案会一起停住。
2. 内存能分开限。API 是 `MemoryMax=600M`，worker 单独给 400M；
   真有人传了个把内存吃爆的文件，被 systemd 收走的是 worker，
   网站本身还在。

**文档状态与任务状态必须在同一个事务里改。** 拆开的话会出现「任务 done、
文档还停在解析中」这种没人能解释的中间态——页面上就是一个永远转圈的圈。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copilot.config import get_settings
from copilot.db.models import Document, Job
from copilot.db.session import SessionLocal
from copilot.ingest.parsers import ParseError, parse_upload
from copilot.ingest.pipeline import write_chunks
from copilot.jobs import queue
from copilot.providers.base import Embedder

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3.0

# 一轮的结果：队列空了 / 干成一件 / 干砸一件
Outcome = Literal["idle", "done", "failed"]


class PermanentError(Exception):
    """再试也不会好的失败：文件坏了、类型不支持、payload 里的文档没了。"""


async def _load_document(session: AsyncSession, payload: dict) -> Document | None:
    raw = (payload or {}).get("document_id")
    if not raw:
        raise PermanentError("任务 payload 里没有 document_id")
    try:
        doc_id = uuid.UUID(str(raw))
    except ValueError as e:
        raise PermanentError(f"document_id 不是合法 UUID：{raw!r}") from e
    return await session.get(Document, doc_id)


async def _load_document_safe(session: AsyncSession, payload: dict) -> Document | None:
    """善后路径专用：payload 本身有问题时返回 None，不再抛。

    在「处理失败」的分支里再抛一次异常，会把真正的失败原因盖掉——
    用户看到的错误信息就变成了 payload 格式，而实际错的是别的事。
    """
    try:
        return await _load_document(session, payload)
    except PermanentError:
        return None


async def handle_parse_upload(session: AsyncSession, job: Job, embedder: Embedder) -> str:
    """解析一份上传的文档并写入它自己那一行 documents。返回一句结果说明。

    抛 `PermanentError` = 别重试；抛别的 = 值得重试（限流、网络、数据库抖动）。
    """
    doc = await _load_document(session, job.payload)
    if doc is None:
        # 用户在排队期间把文档删了。这不是错误，任务作废即可——
        # 当成失败的话，队列里会攒下一堆红色的「失败」，而其实什么都没坏
        return "文档已被删除，任务作废"

    if not doc.stored_path:
        raise PermanentError("这条文档没有落盘路径，无法解析")
    try:
        path = get_settings().upload_path(doc.stored_path)
    except ValueError as e:
        raise PermanentError(str(e)) from e
    if not path.exists():
        raise PermanentError("上传的文件在服务器上找不到了，请重新上传")

    # 先让用户看到「解析中」。这一步单独提交，不能等到最后——
    # 解析 + 向量化可能要几十秒，中间页面上得有个准确的状态
    doc.status = "running"
    doc.error = None
    await session.commit()

    # 解析是纯 CPU 的同步活，向量化是同步网络调用。这里**故意不丢线程池**：
    # worker 进程除了这条任务没有别的事要做，阻塞事件循环没有代价，
    # 而多一层线程池只会多一份内存和一份排查难度。（API 进程里是另一回事，
    # 见 retrieve.py。）
    try:
        parsed = parse_upload(path, suffix=Path(doc.original_filename or path.name).suffix)
    except ParseError as e:
        raise PermanentError(str(e)) from e

    n = await write_chunks(session, doc, parsed.markdown, embedder)
    if n == 0:
        raise PermanentError("解析出来的内容太少，切不出可检索的片段")

    doc.status = "done"
    doc.error = None
    return f"{doc.chunk_count} 块" + (f"（{parsed.note}）" if parsed.note else "")


HANDLERS = {queue.PARSE_UPLOAD: handle_parse_upload}


async def run_job(session: AsyncSession, job: Job, embedder: Embedder) -> bool:
    """执行一条任务，落定它和它那篇文档的状态。返回是否成功。

    ⚠️ `job.id` / `job.type` 要**在跑之前**取出来存成普通变量。
    失败分支里要 `rollback()`，而 rollback 会让会话里所有实例的属性过期——
    过期之后读 `job.id` 会触发一次隐式的 IO，在 async 会话里直接抛 MissingGreenlet。
    表现是「任务失败」被一个更难懂的错误盖掉，真正的原因看不见了。
    """
    job_id, job_type = job.id, job.type

    handler = HANDLERS.get(job_type)
    if handler is None:
        await queue.finish(session, job, f"未知任务类型：{job_type}")
        await session.commit()
        logger.error("未知任务类型 %s（job=%s）", job_type, job_id)
        return False

    try:
        note = await handler(session, job, embedder)
    except Exception as e:  # noqa: BLE001 - worker 不能因为一条任务崩掉
        permanent = isinstance(e, PermanentError)
        msg = str(e) if permanent else f"{type(e).__name__}: {e}"
        # 回滚掉这条任务写坏的一切（半截的块、改了一半的字段），
        # 再在干净的事务里写失败状态——否则连失败都提交不上去
        await session.rollback()
        state = await _mark_failed(session, job_id, msg, retryable=not permanent)
        await session.commit()
        logger.warning("任务失败 job=%s type=%s → %s：%s", job_id, job_type, state, msg)
        return False

    await queue.finish(session, job)
    await session.commit()
    logger.info("任务完成 job=%s type=%s %s", job_id, job_type, note)
    return True


async def _mark_failed(
    session: AsyncSession, job_id: uuid.UUID, msg: str, *, retryable: bool
) -> str:
    """把失败写进 job，并让那篇文档的状态跟上。"""
    job = await session.get(Job, job_id)
    if job is None:  # 理论上不可能，但别让善后代码自己再抛一次
        return "failed"
    state = await queue.finish(session, job, msg, retryable=retryable)

    doc = (
        await _load_document_safe(session, job.payload)
        if job.type == queue.PARSE_UPLOAD
        else None
    )
    if doc is not None:
        if state == "pending":
            # 还要再试，别让用户看到红色的「失败」——那是最终判决，现在还没到
            doc.status = "pending"
            doc.error = f"第 {job.attempts} 次尝试未成功，稍后自动重试：{msg}"
        else:
            doc.status = "failed"
            doc.error = msg
    return state


async def run_once(
    embedder: Embedder,
    maker: async_sessionmaker[AsyncSession] | None = None,
    job_types: list[str] | None = None,
) -> Outcome:
    """取一条任务跑掉。测试直接用这个，不必起循环。

    返回三态而不是布尔值。「有没有活干」和「干成了没有」必须分开，
    因为主循环要靠它决定**下一步是接着取还是先歇一下**：
    干成了就立刻接着取（队列里可能还堆着）；失败了得歇一下再来——
    失败最常见的原因就是 embedding 限流，不歇就是原地连撞三次，
    把重试次数在一秒内烧光，重试也就白设了。
    """
    maker = maker or SessionLocal
    async with maker() as session:
        job = await queue.claim_next(session, job_types or list(HANDLERS))
        if job is None:
            return "idle"
        ok = await run_job(session, job, embedder)
        return "done" if ok else "failed"


async def startup_reclaim(maker: async_sessionmaker[AsyncSession] | None = None) -> int:
    """启动自愈：把上次没跑完的任务和文档一起放回排队状态。"""
    maker = maker or SessionLocal
    async with maker() as session:
        stale = await queue.reclaim_stale(session)
        for job in stale:
            if job.type != queue.PARSE_UPLOAD:
                continue
            doc = await _load_document_safe(session, job.payload)
            if doc is not None and doc.status == "running":
                doc.status = "pending"
        if stale:
            await session.commit()
        return len(stale)


async def run_worker(
    poll_interval: float = POLL_INTERVAL,
    embedder: Embedder | None = None,
    report=None,
) -> None:
    """主循环。收到 SIGTERM / Ctrl-C 时**跑完手上这条**再退出。

    半路硬停会把一条 running 留在库里，得等 30 分钟的 stale 回收才恢复——
    对用户就是「解析中」卡了半小时。systemd 重启服务时走的正是这条路，
    所以这里必须优雅。
    """
    say = report or (lambda m: logger.info(m))
    if embedder is None:
        from copilot.api.providers import get_embedder

        embedder = get_embedder()

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:
            # Windows 的 ProactorEventLoop 不支持 add_signal_handler。
            # 本机开发只会 Ctrl-C（走 KeyboardInterrupt），线上是 Linux，
            # 所以这里静默退让即可
            signal.signal(sig, lambda *_: stopping.set())

    if n := await startup_reclaim():
        say(f"回收了 {n} 条上次没跑完的任务")
    say(f"worker 已启动，轮询间隔 {poll_interval}s，Ctrl-C 退出")

    announced_idle = False
    while not stopping.is_set():
        try:
            outcome: Outcome = await run_once(embedder)
        except Exception:  # noqa: BLE001 - 连库失败之类，睡一下继续，别让服务反复重启
            logger.exception("worker 循环出错，%.0fs 后重试", poll_interval)
            outcome = "failed"

        if outcome == "done":
            announced_idle = False
            continue  # 队列里可能还堆着，立刻接着取

        if outcome == "idle" and not announced_idle:
            announced_idle = True
            say("队列已空，等待新任务…")

        # 失败也要歇这一下：失败最常见的原因是 embedding 限流，
        # 不歇就等于原地连撞三次，把重试次数在一秒内烧光（见 run_once）
        # 用 wait_for 而不是 sleep：收到停止信号能立刻醒，不必等满一个间隔
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=poll_interval)

    say("worker 已停止")
