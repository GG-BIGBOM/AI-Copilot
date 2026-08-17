"""任务队列的取放。状态机只有这一处实现。

    pending ──claim_next()──> running ──finish()──> done
                                 │
                                 └──finish(error)──> failed
                                        或 ──> pending（可重试且没超次数）

**为什么 `FOR UPDATE SKIP LOCKED` 是这里的关键**：两个 worker（或者一次
误开了两份 systemd 服务）同时来取任务时，普通的 `SELECT ... LIMIT 1` 会让
两边都拿到同一行，于是同一份文档被解析两遍——重复扣 embedding 额度，
而且两边都在删对方刚写进去的块。`SKIP LOCKED` 让第二个 worker 直接跳过
已被锁住的行去看下一条，不阻塞、不重复。

**可重试与不可重试要分开。** 文件本身坏了（ParseError）重试一万次也是坏的，
只会白烧 CPU；而 embedding 撞上限流是过一会儿就好的。分不开的话，
要么坏文件卡在队列里反复重试，要么一次网络抖动就让用户看到「解析失败」。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.db.models import Job

PARSE_UPLOAD = "parse_upload"

# 超过这个次数就认命，标 failed 让用户看到。免费额度撞限流一般一两次就过去了
MAX_ATTEMPTS = 3
# running 超过这么久还没结果，视为 worker 半路挂了（被 OOM killer 收走、
# 机器重启）。不回收的话那条任务永远停在「解析中」，用户等一辈子。
STALE_AFTER = timedelta(minutes=30)


def _now() -> datetime:
    """带时区的当前时间。

    列是 `TIMESTAMP WITH TIME ZONE`，塞 naive datetime 进去会按服务器本地时区
    解释——本机是 +08、服务器是 UTC，同一份代码算出来的「30 分钟前」能差 8 小时，
    stale 回收要么永不触发要么见谁收谁。
    """
    return datetime.now(UTC)


async def enqueue(session: AsyncSession, job_type: str, payload: dict) -> Job:
    """入队。不提交——调用方通常要和别的写操作放同一个事务里。

    上传接口就依赖这一点：`documents` 行和 `jobs` 行必须一起成功。
    分开提交的话，「文档建好了但任务没入队」就是一篇永远停在「排队中」的文档。
    """
    job = Job(type=job_type, payload=payload, status="pending")
    session.add(job)
    await session.flush()
    return job


async def claim_next(session: AsyncSession, job_types: list[str] | None = None) -> Job | None:
    """取一条待办并占住它。没有就返回 None。

    `with_for_update(skip_locked=True)` 是这段的全部要点，见模块 docstring。
    取到后立刻提交，锁随之释放——此后靠 `status='running'` 挡住别的 worker，
    而不是靠一直持有行锁（那会把事务拖成任务本身那么长）。
    """
    stmt = (
        select(Job)
        .where(Job.status == "pending")
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types:
        stmt = stmt.where(Job.type.in_(job_types))

    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.attempts += 1
    job.started_at = _now()
    job.finished_at = None
    await session.commit()
    return job


async def finish(
    session: AsyncSession,
    job: Job,
    error: str | None = None,
    *,
    retryable: bool = False,
) -> str:
    """结束一条任务，返回落到的最终状态。

    Args:
        error: None 表示成功
        retryable: 失败是否值得再试（网络/限流值得，文件本身坏了不值得）

    不提交——`parse_upload` 要把任务状态和文档状态放同一个事务里改，
    否则会出现「任务已 done、文档还停在 running」这种谁也解释不了的中间态。
    """
    if error is None:
        job.status = "done"
        job.error = None
        job.finished_at = _now()
        return job.status

    job.error = error[:2000]  # 堆栈可能很长，DB 里存个够排查的量就行
    if retryable and job.attempts < MAX_ATTEMPTS:
        # 放回 pending 等下一轮。不设退避时间——轮询本身有间隔，
        # 而 MAX_ATTEMPTS 很小，不会转成死循环
        job.status = "pending"
        job.finished_at = None
    else:
        job.status = "failed"
        job.finished_at = _now()
    return job.status


async def reclaim_stale(session: AsyncSession, older_than: timedelta = STALE_AFTER) -> list[Job]:
    """把卡在 running 的僵尸任务放回 pending，返回被回收的那些。

    worker 被 OOM killer 收走、或机器直接重启时，那条 running 没人会去改。
    worker 每次启动都跑一遍这个，是这套「没有心跳」的简易队列唯一的自愈手段。

    不提交：调用方还要把对应文档的状态一起改回去（见 worker.py），
    两者必须同一个事务，否则任务回了 pending、文档还停在「解析中」。
    """
    cutoff = _now() - older_than
    stmt = select(Job).where(
        Job.status == "running",
        Job.started_at.is_not(None),
        Job.started_at < cutoff,
    )
    stale = list((await session.execute(stmt)).scalars())
    for job in stale:
        job.status = "pending"
        job.error = "上一次执行没有正常结束（worker 中断），已重新排队"
    return stale


def document_payload(document_id: uuid.UUID) -> dict:
    """`parse_upload` 的 payload。JSONB 里存字符串，UUID 不是 JSON 类型。"""
    return {"document_id": str(document_id)}
