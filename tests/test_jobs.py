"""任务队列与 worker 的测试（打真实 Postgres，用假 embedder）。

这里守四件事：

1. **`FOR UPDATE SKIP LOCKED` 真的在生效。** 没有它，两个 worker 会同时拿到
   同一条任务：同一份文档解析两遍、重复烧 embedding 额度，还互删对方刚写的块。
2. **可重试与不可重试分得开。** 文件坏了别重试（白烧 CPU），限流要重试
   （否则一次网络抖动就让用户看到「解析失败」）。
3. **文档状态跟着任务状态走。** 出现「任务 done、文档还在解析中」这种中间态时，
   页面上就是一个永远转圈的圈。
4. **排队期间文档被删掉，worker 不能崩。**
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from samples import make_docx
from sqlalchemy import delete, select

from copilot.config import get_settings
from copilot.db.models import Chunk, Document, Job, User
from copilot.jobs import queue
from copilot.jobs import worker as worker_mod
from copilot.jobs.worker import (
    handle_parse_upload,
    run_job,
    run_once,
    run_worker,
    startup_reclaim,
)

DIM = 1024


class FakeEmbedder:
    dim = DIM

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * DIM
        for i, ch in enumerate(text[:64]):
            v[(ord(ch) * 7 + i) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


# ---------- 夹具 ----------


@pytest.fixture
async def owner(maker):
    """一个用户 + 收尾时把它的文档、块、任务、落盘文件全清掉。"""
    async with maker() as s:
        u = User(email=f"jobs-{uuid.uuid4().hex[:10]}@test.local", password_hash="x")
        s.add(u)
        await s.commit()
        user_id = u.id

    yield user_id

    async with maker() as s:
        doc_ids = list(
            (await s.execute(select(Document.id).where(Document.owner_id == user_id))).scalars()
        )
        if doc_ids:
            await s.execute(
                delete(Job).where(
                    Job.payload["document_id"].astext.in_([str(d) for d in doc_ids])
                )
            )
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await s.execute(delete(Document).where(Document.id.in_(doc_ids)))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()

    updir = get_settings().upload_dir / str(user_id)
    if updir.exists():
        for f in updir.iterdir():
            f.unlink(missing_ok=True)
        updir.rmdir()


async def _make_upload(
    maker,
    user_id: uuid.UUID,
    *,
    filename: str = "手册.md",
    # 默认正文要够长到切得出块：太短会走进「切不出可检索片段」那条分支，
    # 把本来想测的东西盖掉
    body: str = "退货入库的操作步骤：先在收货单里录入运单号，再确认入库。",
    job_status: str = "pending",
    job_age_hours: float = 0.0,
) -> uuid.UUID:
    """造一份「已上传、待解析」的文档：落盘文件 + documents 行 + jobs 行。

    落盘走的是真实的 `Settings.upload_path`——相对路径拼接和越界检查
    也顺带一起验了。

    `job_status="running"` + `job_age_hours` 直接造一条**僵尸**任务：
    `claim_next` 只看 pending，所以这样造出来的任务只有回收能动它。
    验「worker 起来之后才变成僵尸」那一类场景要用它——`claim_next` 造不出来，
    那样造出来的僵尸在 worker 启动前就存在了。
    """
    s = get_settings()
    rel = f"{user_id}/{uuid.uuid4().hex}{filename[filename.rfind('.'):]}"
    path = s.upload_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    if filename.endswith(".docx"):
        make_docx(path)
    else:
        path.write_text(body, encoding="utf-8")

    async with maker() as session:
        doc = Document(
            owner_id=user_id,
            source_type="upload",
            title=filename.rsplit(".", 1)[0],
            original_filename=filename,
            stored_path=rel,
            size_bytes=path.stat().st_size,
            content_hash=uuid.uuid4().hex,
            status="pending",
        )
        session.add(doc)
        await session.flush()
        job = await queue.enqueue(session, queue.PARSE_UPLOAD, queue.document_payload(doc.id))
        if job_status != "pending":
            job.status = job_status
            job.attempts = 1
            job.started_at = datetime.now(UTC) - timedelta(hours=job_age_hours)
            doc.status = "running"
        await session.commit()
        return doc.id


async def _stored_path(maker, doc_id: uuid.UUID) -> str:
    async with maker() as s:
        return (await s.get(Document, doc_id)).stored_path


# ---------- 队列语义 ----------


async def test_claim_marks_running_and_counts_attempt(maker, owner):
    await _make_upload(maker, owner)
    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        assert job is not None
        assert job.status == "running"
        assert job.attempts == 1
        assert job.started_at is not None


async def test_claimed_job_is_not_handed_out_twice(maker, owner):
    await _make_upload(maker, owner)
    async with maker() as s:
        first = await queue.claim_next(s, [queue.PARSE_UPLOAD])
    async with maker() as s2:
        second = await queue.claim_next(s2, [queue.PARSE_UPLOAD])
    assert first is not None
    assert second is None, "同一条任务被取了两次"


async def test_locked_row_is_skipped_not_waited_on(maker, owner):
    """⭐ `skip_locked=True` 的核心用例。

    第一个会话**持着行锁不提交**，第二个会话来取任务：
      有 skip_locked → 立刻返回 None
      没有           → 一直阻塞到第一个事务结束

    所以这里包了 `wait_for`：真的退化成阻塞时，测试会超时失败，
    而不是把整个测试套件挂死。
    """
    await _make_upload(maker, owner)

    async with maker() as holder:
        held = (
            await holder.execute(
                select(Job)
                .where(Job.status == "pending")
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        assert held is not None, "没造出待办任务，测试前提不成立"

        async def try_claim():
            async with maker() as other:
                return await queue.claim_next(other, [queue.PARSE_UPLOAD])

        got = await asyncio.wait_for(try_claim(), timeout=5)
        assert got is None, "拿到了别人锁着的任务"

        await holder.rollback()  # 放锁，别把锁留给后面的测试


async def test_transient_failure_goes_back_to_pending(maker, owner):
    """网络/限流类失败要放回队列，别让用户看到「失败」。"""
    await _make_upload(maker, owner)
    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        state = await queue.finish(s, job, "429 Too Many Requests", retryable=True)
        await s.commit()
    assert state == "pending"


async def test_permanent_failure_fails_immediately(maker, owner):
    """文件坏了重试一万次也是坏的，第一次就该认。"""
    await _make_upload(maker, owner)
    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        state = await queue.finish(s, job, "这个 PDF 有密码保护", retryable=False)
        await s.commit()
    assert state == "failed"


async def test_retry_gives_up_after_max_attempts(maker, owner):
    """可重试也不能无限重试，否则一条坏任务会一直占着 worker。"""
    await _make_upload(maker, owner)
    state = "pending"
    for _ in range(queue.MAX_ATTEMPTS):
        async with maker() as s:
            job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
            assert job is not None, "还没到上限就取不到任务了"
            state = await queue.finish(s, job, "连不上", retryable=True)
            await s.commit()
    assert state == "failed"


async def test_stale_running_job_is_reclaimed(maker, owner):
    """worker 被 OOM killer 收走时，那条 running 没人会去改。

    不回收 = 用户对着「解析中」等一辈子。这是这套没有心跳的简易队列
    唯一的自愈手段。
    """
    doc_id = await _make_upload(maker, owner)
    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        job.started_at = datetime.now(UTC) - timedelta(hours=2)
        doc = await s.get(Document, doc_id)
        doc.status = "running"
        await s.commit()

    assert await startup_reclaim(maker) == 1

    async with maker() as s:
        assert (await s.get(Job, job.id)).status == "pending"
        # 文档状态必须一起回退，否则任务重排了、页面还显示「解析中」
        assert (await s.get(Document, doc_id)).status == "pending"


async def test_fresh_running_job_is_left_alone(maker, owner):
    """刚开始跑的任务不能被当成僵尸抢走——那会导致同一份文档解析两遍。"""
    await _make_upload(maker, owner)
    async with maker() as s:
        await queue.claim_next(s, [queue.PARSE_UPLOAD])
    assert await startup_reclaim(maker) == 0


async def test_a_job_that_keeps_killing_the_worker_eventually_fails(maker, owner):
    """⭐⭐ **毒任务不能无限重试。**

    一份每次都把 worker 吃到 OOM 的文件，**永远走不到 `queue.finish()`**——
    而 `attempts` 的上限判定原来只长在那里。回收一律放回 pending 的话：

        claim(attempts=1) → 进程被收走 → 回收成 pending
        claim(attempts=2) → 进程被收走 → 回收成 pending → …

    次数一直涨，而队列是 `ORDER BY created_at`，这条永远排第一——
    它不只是自己重试不完，**还挡住后面所有人的上传**。

    ⚠️ 文档也要跟着判 failed。只收任务不收文档的话，队列里那行结案了，
    页面上那篇还在「解析中」转圈。
    """
    doc_id = await _make_upload(maker, owner)
    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        # 模拟「已经被弄死过 MAX_ATTEMPTS 次」：每次都是 claim 成功、进程消失
        job.attempts = queue.MAX_ATTEMPTS
        job.started_at = datetime.now(UTC) - timedelta(hours=2)
        (await s.get(Document, doc_id)).status = "running"
        await s.commit()
        job_id = job.id

    assert await startup_reclaim(maker) == 1

    async with maker() as s:
        assert (await s.get(Job, job_id)).status == "failed"
        doc = await s.get(Document, doc_id)
        assert doc.status == "failed"
        # 给用户的话必须是人话，它会原样显示在知识库页面上
        assert "停止重试" in (doc.error or "")


async def test_one_crash_counts_as_exactly_one_attempt(maker, owner):
    """⭐ **一次崩溃只记一次 attempt。**

    `attempts` 的定义是「被 `claim_next` 领走并开始执行了几次」。
    回收那一步**只改状态、不动计数**——加了的话一次崩溃记两次，
    `MAX_ATTEMPTS=3` 实际只允许一次半重试，而"最多重试三次"这句话
    在文档和给用户的错误话术里到处都是。

    ⚠️ 这道题验的是**不变量，不是新行为**：今天的代码本来就是对的
    （全项目只有 `claim_next` 一处 `+= 1`）。钉下来是因为回收那一段
    2026-09-03 刚改过，而"顺手把 attempts 也加上"看起来非常自然。
    """
    await _make_upload(maker, owner)

    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        assert job.attempts == 1
        # 模拟崩溃：进程消失，这条 running 没人改
        job.started_at = datetime.now(UTC) - timedelta(hours=2)
        await s.commit()
        job_id = job.id

    await startup_reclaim(maker)
    async with maker() as s:
        assert (await s.get(Job, job_id)).attempts == 1, "回收把 attempts 也加了一次"

    async with maker() as s:
        again = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        assert again.id == job_id
        assert again.attempts == 2, "第二次真正执行才该是第 2 次"


async def test_the_loop_reclaims_without_waiting_for_a_restart(maker, owner):
    """⭐⭐ **回收必须在循环里也跑，不能只在启动时跑一次。**

    这是这一条真正的缺口，而且它恰好**绕开**了 `STALE_AFTER`：
    `copilot-worker.service` 是 `Restart=always` + `RestartSec=5`，所以
    最常见的那种崩溃（解析吃爆 MemoryMax=400M）自愈不了——

        t=0    claim，started_at 记下
        t=10s  被 systemd 收走
        t=15s  worker 起来跑一次回收 → 这条只有 15 秒大，够不上 30 分钟 → 跳过
        此后   worker 再也不重启，回收再也不跑
               → 任务永远 running，文档永远「解析中」

    ⚠️ **这道题必须让任务在 worker 起来之后才变成僵尸**，否则验的是启动那一次
    回收（`test_stale_running_job_is_reclaimed` 已经在验它了）。所以：
    先起 worker、等它空转，再直接插一行 `running` 的任务。
    """
    monkeypatched = worker_mod.RECLAIM_INTERVAL
    worker_mod.RECLAIM_INTERVAL = 0.0  # 别让这道题真的等 60 秒
    lines: list[str] = []
    task = asyncio.create_task(
        run_worker(poll_interval=0.01, embedder=FakeEmbedder(), report=lines.append)
    )
    try:
        # 等 worker 起来并且把启动那一次回收跑完
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any("队列已空" in m for m in lines):
                break
        else:
            pytest.fail("worker 没起来")

        # 现在才造僵尸：直接插一行 running，`claim_next` 碰不到它，
        # 只有回收能把它挪走
        doc_id = await _make_upload(maker, owner, job_status="running", job_age_hours=2)

        for _ in range(400):  # 最多等 4 秒
            await asyncio.sleep(0.01)
            async with maker() as s:
                if (await s.get(Document, doc_id)).status == "done":
                    break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        worker_mod.RECLAIM_INTERVAL = monkeypatched

    async with maker() as s:
        doc = await s.get(Document, doc_id)
        assert doc.status == "done", (
            "循环里没有回收：worker 不重启的话这条任务永远停在 running，"
            f"文档永远停在「解析中」（现在是 {doc.status}）"
        )


# ---------- worker 端到端 ----------


async def test_worker_parses_and_ingests(maker, owner):
    doc_id = await _make_upload(
        maker, owner, filename="面单手册.md", body="# 面单设置\n\n先绑定物流账号再打印。"
    )
    assert await run_once(FakeEmbedder(), maker) == "done"

    async with maker() as s:
        doc = await s.get(Document, doc_id)
        assert doc.status == "done"
        assert doc.error is None
        assert doc.chunk_count > 0

        chunks = list(
            (await s.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars()
        )
        assert chunks
        # ⚠️ 隔离红线：块的 owner 必须跟着文档
        assert all(c.owner_id == owner for c in chunks)
        assert any("绑定物流账号" in c.content for c in chunks)


async def test_worker_handles_docx(maker, owner):
    doc_id = await _make_upload(maker, owner, filename="操作手册.docx")
    assert await run_once(FakeEmbedder(), maker) == "done"
    async with maker() as s:
        doc = await s.get(Document, doc_id)
        assert doc.status == "done", doc.error
        assert doc.chunk_count > 0


async def test_worker_empties_the_queue(maker, owner):
    for i in range(3):
        await _make_upload(maker, owner, filename=f"文档{i}.md", body=f"第 {i} 份文档的正文内容")
    emb = FakeEmbedder()
    handled = 0
    while await run_once(emb, maker) != "idle":
        handled += 1
    assert handled == 3


async def test_deleted_document_does_not_crash_worker(maker, owner):
    """排队期间用户把文档删了。这不是错误——任务作废即可。

    当成失败的话，队列里会攒下一堆红色的「失败」，而其实什么都没坏。
    """
    doc_id = await _make_upload(maker, owner)
    async with maker() as s:
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()

    assert await run_once(FakeEmbedder(), maker) == "done"
    async with maker() as s:
        job = (
            await s.execute(
                select(Job).where(Job.payload["document_id"].astext == str(doc_id))
            )
        ).scalar_one()
        assert job.status == "done"
        assert job.error is None


async def test_missing_file_is_permanent_failure(maker, owner):
    doc_id = await _make_upload(maker, owner)
    get_settings().upload_path(await _stored_path(maker, doc_id)).unlink()

    assert await run_once(FakeEmbedder(), maker) == "failed"
    async with maker() as s:
        doc = await s.get(Document, doc_id)
        assert doc.status == "failed"
        assert "找不到" in doc.error


async def test_broken_file_is_permanent_failure(maker, owner):
    """坏文件只试一次。重试的话是白烧 CPU，而且用户一直等不到结论。"""
    doc_id = await _make_upload(maker, owner, filename="坏的.docx", body="not a zip")
    path = get_settings().upload_path(await _stored_path(maker, doc_id))
    path.write_bytes(b"definitely not a docx")

    assert await run_once(FakeEmbedder(), maker) == "failed"
    async with maker() as s:
        doc = await s.get(Document, doc_id)
        job = (
            await s.execute(
                select(Job).where(Job.payload["document_id"].astext == str(doc_id))
            )
        ).scalar_one()
        assert doc.status == "failed"
        assert job.status == "failed"
        assert job.attempts == 1, "不可重试的失败不该再试第二次"


async def test_embedding_failure_is_retried(maker, owner):
    """SiliconFlow 限流是过一会儿就好的，不该让用户看到「解析失败」。"""
    doc_id = await _make_upload(maker, owner)
    emb = FakeEmbedder(fail_with=RuntimeError("429 rate limited"))
    assert await run_once(emb, maker) == "failed"

    async with maker() as s:
        doc = await s.get(Document, doc_id)
        job = (
            await s.execute(
                select(Job).where(Job.payload["document_id"].astext == str(doc_id))
            )
        ).scalar_one()
        assert job.status == "pending", "限流类失败应该放回队列"
        # 还没到最终判决，别给用户看红色的「失败」
        assert doc.status == "pending"
        assert "重试" in doc.error

    # 下一轮换个正常的 embedder，应该顺利跑完
    assert await run_once(FakeEmbedder(), maker) == "done"
    async with maker() as s:
        assert (await s.get(Document, doc_id)).status == "done"


async def test_unknown_job_type_fails_without_retry(maker):
    """⚠️ **这条会自己把队列清干净，两头都清。**

    `claim_next(s, None)` 取的是**最老的** pending，不一定是刚入队那条。
    库里但凡躺着一条别的 `sync_mars` pending，这条用例就会去判决**那一条**，
    然后断言自己这条还是 pending —— 而它失败时又留下一条 pending，
    **于是一旦失败一次，这个库上就永远失败**。

    2026-08-25 在开发库上真的发生了：`jobs` 表里躺着 1 条 pending、
    4 条 failed 的 `sync_mars`，全是这条用例历次失败留下的。
    CI 的空库跑得过，本机跑不过 —— 和「靠历史数据撑着」正好是镜像的一对。

    `sync_mars` 是只有这条用例用的假类型，所以整类删掉是安全的。
    """
    async with maker() as s:
        await s.execute(delete(Job).where(Job.type == "sync_mars"))
        job = await queue.enqueue(s, "sync_mars", {})
        await s.commit()
        job_id = job.id

    try:
        async with maker() as s:
            job = await queue.claim_next(s, None)
            assert job is not None and job.id == job_id, (
                "claim_next 取到的不是刚入队那条——队列里还躺着别的 pending"
            )
            # run_job 认不出这个类型就该直接判死
            assert await run_job(s, job, FakeEmbedder()) is False  # run_job 仍是布尔

        async with maker() as s:
            stored = await s.get(Job, job_id)
            assert stored.status == "failed"
            assert "未知任务类型" in stored.error
    finally:
        # ⭐ 放 finally：断言炸了也不能把 pending 留在库里，否则下一次必炸
        async with maker() as s:
            await s.execute(delete(Job).where(Job.type == "sync_mars"))
            await s.commit()


async def test_running_state_is_committed_before_the_long_work(maker, owner):
    """解析 + 向量化要几十秒，这期间页面上必须显示「解析中」。

    所以 `handle_parse_upload` 里那句 `doc.status = "running"` 后面跟着一次
    单独的 commit。这里在**另一个连接**上确认它真的落了库——同一个会话里查
    是看不出区别的（未提交的改动在本会话内可见）。

    直接调 handler，让 embedder 在被调用时抛错停在半路：此时 running 已提交，
    而后面的块和 done 都还没写。
    """
    doc_id = await _make_upload(maker, owner)

    async with maker() as s:
        job = await queue.claim_next(s, [queue.PARSE_UPLOAD])
        with pytest.raises(RuntimeError):
            await handle_parse_upload(s, job, FakeEmbedder(fail_with=RuntimeError("停在这")))

        async with maker() as other:  # 另一条连接，只看得到已提交的数据
            assert (await other.get(Document, doc_id)).status == "running"
        await s.rollback()
