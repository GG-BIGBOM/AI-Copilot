"""命令行入口。

M0 阶段各子命令为占位实现，逐个里程碑填充：
    ingest      M1  本地文件入库
    ask         M1  带引用问答
    sync-yuque  M2  语雀公开知识库同步
    invite      M3  生成邀请码
    serve       M3  启动 API 服务
    worker      M6  后台解析 worker
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="copilot",
    help="知识库 Agent —— 语雀 + 自传文档的带引用问答",
    no_args_is_help=True,
    add_completion=False,
)


def _todo(milestone: str) -> None:
    typer.secho(f"尚未实现，计划在 {milestone} 完成。见 plan.md", fg=typer.colors.YELLOW)
    raise typer.Exit(code=1)


@app.command()
def ingest(
    path: str = typer.Argument("", help="要入库的目录，默认 data/raw/yuque"),
    force: bool = typer.Option(False, "--force", help="忽略 content_hash，全部重新向量化"),
    limit: int = typer.Option(0, "--limit", help="只处理前 N 篇，用于小批量试跑"),
) -> None:
    """把本地 Markdown 切分、向量化、写入公共库。"""
    import asyncio

    asyncio.run(_ingest(path, force, limit))


async def _ingest(path: str, force: bool, limit: int) -> None:
    from pathlib import Path

    from copilot.config import get_settings
    from copilot.db.session import SessionLocal
    from copilot.ingest.pipeline import ingest_documents, load_yuque_dir
    from copilot.providers.siliconflow import SiliconFlowEmbedder

    root = Path(path) if path else get_settings().data_dir / "raw" / "yuque"
    if not root.exists():
        typer.secho(f"目录不存在：{root}", fg=typer.colors.RED)
        raise typer.Exit(1)

    docs = list(load_yuque_dir(root))
    if limit:
        docs = docs[:limit]
    typer.echo(f"待处理 {len(docs)} 篇，来自 {root}\n")

    embedder = SiliconFlowEmbedder()
    async with SessionLocal() as session:
        stats = await ingest_documents(
            session, docs, embedder, force=force, report=lambda m: typer.echo(m)
        )

    typer.echo("")
    typer.secho(
        f"完成：入库 {stats.ingested} 篇　跳过 {stats.skipped} 篇　"
        f"共 {stats.chunks} 块　失败 {stats.failed} 篇",
        fg=typer.colors.GREEN if stats.failed == 0 else typer.colors.YELLOW,
    )
    for err in stats.errors[:10]:
        typer.secho(f"  {err}", fg=typer.colors.RED)


@app.command()
def ask(
    question: str = typer.Argument(..., help="要问的问题"),
    show_chunks: bool = typer.Option(False, "--show-chunks", help="打印召回的原文，用于调参"),
) -> None:
    """检索知识库并生成带引用的答案。"""
    import asyncio

    asyncio.run(_ask(question, show_chunks))


async def _ask(question: str, show_chunks: bool) -> None:
    from copilot.db.session import SessionLocal
    from copilot.providers.llm import ChatLLM
    from copilot.providers.siliconflow import SiliconFlowEmbedder, SiliconFlowReranker
    from copilot.qa import ask_stream, is_no_answer

    embedder = SiliconFlowEmbedder()
    reranker = SiliconFlowReranker()

    # ChatLLM 是同步上下文管理器，不能混进 async with
    async with SessionLocal() as session:
        llm = ChatLLM()
        try:
            if show_chunks:
                from copilot.retrieve import search

                result = await search(session, question, embedder, reranker)
                typer.secho("── 召回的原文 ──", fg=typer.colors.BRIGHT_BLACK)
                for rc in result.chunks:
                    typer.secho(
                        f"[{rc.citation.n}] {rc.citation.label}　分数 {rc.citation.score:.4f}",
                        fg=typer.colors.BRIGHT_BLACK,
                    )
                    typer.secho(f"    {rc.content[:180]}…\n", fg=typer.colors.BRIGHT_BLACK)

            answer = await ask_stream(session, question, embedder, reranker, llm)
            buf: list[str] = []
            for piece in answer.stream:
                typer.echo(piece, nl=False)
                buf.append(piece)
            typer.echo("\n")

            # 答案是「暂无此内容」时不能挂来源——否则看着像有依据
            citations = [] if is_no_answer("".join(buf)) else answer.citations

            if answer.images and citations:
                typer.secho("配图：", fg=typer.colors.CYAN)
                for img in answer.images:
                    typer.secho(f"  [图{img['n']}] {img['url']}", fg=typer.colors.CYAN)

            if citations:
                typer.secho("来源：", fg=typer.colors.CYAN)
                for c in citations:
                    line = f"  [{c.n}] {c.label}"
                    if c.source_url:
                        line += f"\n      {c.source_url}"
                    typer.secho(line, fg=typer.colors.CYAN)
        finally:
            llm.close()


@app.command(name="sync-yuque")
def sync_yuque_cmd(
    url: str = typer.Argument(..., help="语雀空间 URL 或 login，如 https://www.yuque.com/wdterpqjb"),
    books: str = typer.Option("", "--books", help="只抓指定知识库，逗号分隔的 slug"),
    limit: int = typer.Option(0, "--limit", help="每个库最多抓几篇，0 表示不限；用于小批量试跑"),
    force: bool = typer.Option(False, "--force", help="忽略增量判定，全量重抓"),
) -> None:
    """抓取语雀公开知识库到本地 data/raw/yuque/。"""
    from copilot.sources.sync import sync_yuque

    only = [s.strip() for s in books.split(",") if s.strip()] or None
    stats = sync_yuque(
        url,
        only_books=only,
        limit=limit or None,
        force=force,
        report=lambda m: typer.echo(m),
    )

    typer.echo("")
    summary = (
        f"完成：知识库 {stats.books} 个　新增/更新 {stats.fetched} 篇　跳过 {stats.skipped} 篇"
    )
    if stats.restricted:
        summary += f"　私密 {stats.restricted} 篇"
    if stats.failed:
        summary += f"　失败 {stats.failed} 篇"
    typer.secho(summary, fg=typer.colors.GREEN if stats.failed == 0 else typer.colors.YELLOW)

    if stats.restricted:
        typer.secho(
            f"\n{stats.restricted} 篇是作者设了权限的私密文档，匿名访问不到，"
            "重试也没用——不影响其余内容。",
            fg=typer.colors.BRIGHT_BLACK,
        )
    if stats.errors:
        typer.echo("\n真正的失败（需要排查）：")
        for err in stats.errors[:10]:
            typer.secho(f"  {err}", fg=typer.colors.RED)


@app.command()
def invite(
    count: int = typer.Option(1, "--count", "-n", help="生成几个邀请码"),
    show: bool = typer.Option(False, "--list", help="不生成，只列出还没用掉的码"),
) -> None:
    """生成注册邀请码。"""
    import asyncio

    asyncio.run(_invite(count, show))


async def _invite(count: int, show: bool) -> None:
    from copilot.auth.invites import count_unused_codes, create_invite_codes, list_unused_codes
    from copilot.db.session import SessionLocal

    async with SessionLocal() as session:
        if show:
            codes = await list_unused_codes(session)
            if not codes:
                typer.secho("没有未使用的邀请码。", fg=typer.colors.YELLOW)
                return
            typer.secho(f"未使用的邀请码（{len(codes)} 个）：", fg=typer.colors.CYAN)
            for code in codes:
                typer.echo(f"  {code}")
            return

        if count < 1:
            typer.secho("--count 至少是 1", fg=typer.colors.RED)
            raise typer.Exit(1)

        codes = await create_invite_codes(session, count)
        remaining = await count_unused_codes(session)

    typer.secho(f"生成了 {len(codes)} 个邀请码：", fg=typer.colors.GREEN)
    for code in codes:
        typer.echo(f"  {code}")
    typer.secho(f"\n当前未使用共 {remaining} 个。每个码只能用一次。", fg=typer.colors.BRIGHT_BLACK)


@app.command(name="prune-junk")
def prune_junk(
    apply: bool = typer.Option(False, "--apply", help="真的删。默认只看不动"),
) -> None:
    """清掉索引里的二进制垃圾块（语雀内嵌表格的压缩载荷）。

    M8 清点索引时发现的：5268 块里有 692 块（13%）是这种东西，
    白占 embedding 额度、白占 top-k 名额（见 ingest/chunker.looks_like_junk）。
    切分器已经在入库时拦掉了，这个命令是清**存量**——不必重新向量化整个库。
    """
    import asyncio

    asyncio.run(_prune_junk(apply))


async def _prune_junk(apply: bool) -> None:
    from sqlalchemy import delete, func, select, update

    from copilot.db.models import Chunk, Document
    from copilot.db.session import SessionLocal
    from copilot.ingest.chunker import looks_like_junk

    async with SessionLocal() as session:
        stmt = select(Chunk.id, Chunk.document_id, Chunk.content)
        rows = list((await session.execute(stmt)).all())
        junk = [r for r in rows if looks_like_junk(r.content)]
        docs = {r.document_id for r in junk}
        share = len(junk) / max(len(rows), 1)
        typer.echo(
            f"总块 {len(rows)}　垃圾块 {len(junk)}（{share:.1%}），涉及 {len(docs)} 篇文档"
        )

        if not junk:
            typer.secho("索引很干净，不用清。", fg=typer.colors.GREEN)
            return
        if not apply:
            typer.secho("这是预演，什么都没删。确认无误后加 --apply。", fg=typer.colors.YELLOW)
            return

        await session.execute(delete(Chunk).where(Chunk.id.in_([r.id for r in junk])))
        # chunk_count 要跟着修，否则「文档管理」页和统计口径全是旧数
        for doc_id in docs:
            n = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.document_id == doc_id)
            )
            await session.execute(
                update(Document).where(Document.id == doc_id).values(chunk_count=n)
            )
        await session.commit()
        left = await session.scalar(select(func.count(Chunk.id)))
        typer.secho(f"已删 {len(junk)} 块，现存 {left} 块。", fg=typer.colors.GREEN)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址，线上用 127.0.0.1 由 nginx 反代"),
    port: int = typer.Option(8000, help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="开发模式自动重载"),
) -> None:
    """启动 API 服务。"""
    import uvicorn

    # 传字符串而不是 app 对象：--reload 需要能重新 import 这个模块。
    # 服务器只有 1.6GB 内存，workers 恒为 1（systemd 里也是），
    # 所以任何阻塞事件循环的调用都必须丢线程池——见 retrieve.py 与 routes/chat.py
    uvicorn.run("copilot.api.app:app", host=host, port=port, reload=reload, workers=1)


@app.command()
def worker(
    poll: float = typer.Option(3.0, "--poll", help="队列空时的轮询间隔（秒）"),
    once: bool = typer.Option(False, "--once", help="只清空当前队列，跑完就退出"),
) -> None:
    """启动后台任务 worker（解析上传的文档）。

    线上是 `copilot-worker.service` 常驻。`--once` 给手动补跑用：
    worker 停过一段时间、队列里积了任务时，跑一次清空它。
    """
    import asyncio

    asyncio.run(_worker(poll, once))


async def _worker(poll: float, once: bool) -> None:
    import logging

    from copilot.api.providers import close_all, get_embedder
    from copilot.jobs.worker import run_once, run_worker, startup_reclaim

    # worker 的进展全在 logger 里（systemd 收进 journal）。不配置的话，
    # 前台跑 `copilot worker` 就是一片死寂——看不出它到底在干活还是卡住了
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        if once:
            if n := await startup_reclaim():
                typer.echo(f"回收了 {n} 条上次没跑完的任务")
            done = failed = 0
            # 失败的任务若可重试会回到 pending，这里会再取到它——MAX_ATTEMPTS
            # 之内就地试完，不必等下一次手动补跑
            while (outcome := await run_once(get_embedder())) != "idle":
                done += outcome == "done"
                failed += outcome == "failed"
            typer.secho(
                f"处理了 {done} 条任务" + (f"，{failed} 条失败" if failed else "") + "，队列已空。",
                fg=typer.colors.GREEN if not failed else typer.colors.YELLOW,
            )
            return

        await run_worker(poll_interval=poll, report=lambda m: typer.echo(m))
    except KeyboardInterrupt:
        # Windows 上装不了信号处理器（见 run_worker），Ctrl-C 走的是这条路
        typer.echo("\n已中断。")
    finally:
        close_all()


if __name__ == "__main__":
    app()
