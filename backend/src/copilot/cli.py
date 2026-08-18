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
    owner: str = typer.Option(
        "", "--owner", help="写进这个用户的私有库（邮箱）。不给则写公共库"
    ),
) -> None:
    """把本地 Markdown 切分、向量化、写入公共库（或某个用户的私有库）。"""
    import asyncio

    asyncio.run(_ingest(path, force, limit, owner))


async def _ingest(path: str, force: bool, limit: int, owner: str = "") -> None:
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

    # ⭐ 勘误层盖在语雀原文之上。放在 limit 之前：限量试跑也要走同一条路径，
    # 否则「试跑通过、全量才炸」那类问题就要等到全量才发现
    from copilot.ingest.corrections import (
        apply_corrections,
        load_corrections,
        load_db_corrections,
        merge_corrections,
    )

    try:
        corrections = load_corrections(get_settings().corrections_dir)
    except Exception as e:  # noqa: BLE001 - 勘误文件写坏了要说清楚，不能带着错继续入库
        typer.secho(f"勘误文件有问题：{e}", fg=typer.colors.RED)
        raise typer.Exit(1) from e

    # 网页上写的勘误也是勘误。少了这一路，全量 ingest 会把网页改过的内容
    # **悄悄改回语雀原文**——用户看到的是「我明明改过，怎么又变回去了」
    async with SessionLocal() as s_corr:
        from_db = await load_db_corrections(s_corr)
    if from_db:
        typer.secho(f"网页勘误 {len(from_db)} 条", fg=typer.colors.CYAN)
    corrections = merge_corrections(corrections, from_db)

    # 勘误层只覆盖语雀公共库。灌私有库时跳过——那是别人自己的文档，
    # 拿一条针对语雀文档的勘误去盖它毫无道理（`target_url` 也对不上）
    if owner:
        corrections = {}
    docs, applied, missed = apply_corrections(docs, corrections)
    retired = [c for c in applied if c.retired]
    if applied:
        typer.secho(
            f"勘误生效 {len(applied)} 条（其中作废 {len(retired)} 篇）", fg=typer.colors.CYAN
        )
    if missed:
        # 对不上号 = 一个字都没生效，而输出里完全看不出来。必须吵。
        typer.secho(
            f"⚠️ {len(missed)} 条勘误没有对上任何一篇语雀文档，它们不会生效：",
            fg=typer.colors.YELLOW,
        )
        for c in missed:
            typer.secho(f"    {c.name} → {c.target_url}", fg=typer.colors.YELLOW)
        typer.secho(
            "    多半是 target_url 抄错，或那篇语雀文档换了地址。", fg=typer.colors.YELLOW
        )

    if limit:
        docs = docs[:limit]
    typer.echo(f"待处理 {len(docs)} 篇，来自 {root}\n")

    # ⭐ owner_id 决定这批文档进公共库还是某人的私有库。**这是隔离的入口**，
    # 传错了就是把一个人的资料塞进所有人都能搜到的地方——而且不会报错
    owner_id = None
    if owner:
        from sqlalchemy import select

        from copilot.db.models import User

        async with SessionLocal() as s0:
            user = (
                await s0.execute(select(User).where(User.email == owner))
            ).scalar_one_or_none()
        if user is None:
            typer.secho(f"库里没有这个用户：{owner}", fg=typer.colors.RED)
            raise typer.Exit(1)
        owner_id = user.id
        typer.secho(f"写入 {owner} 的私有库（只有他自己搜得到）", fg=typer.colors.CYAN)

    embedder = SiliconFlowEmbedder()
    async with SessionLocal() as session:
        if retired:
            n = await _retire(session, [c.target_url for c in retired])
            typer.secho(f"已从索引移除作废文档 {n} 篇", fg=typer.colors.CYAN)
        stats = await ingest_documents(
            session,
            docs,
            embedder,
            owner_id=owner_id,
            force=force,
            report=lambda m: typer.echo(m),
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


async def _retire(session, urls: list[str]) -> int:
    """把标了 `retired` 的语雀文档从索引里彻底删掉（含块）。

    只删公共库那一行（`owner_id IS NULL`）。用户上传的私有文档和语雀无关，
    就算 source_url 撞上了也不能碰——那是别人的东西。
    """
    from sqlalchemy import delete, select

    from copilot.db.models import Chunk, Document

    stmt = select(Document).where(
        Document.source_url.in_(urls), Document.owner_id.is_(None)
    )
    docs = list((await session.execute(stmt)).scalars())
    for doc in docs:
        await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        await session.delete(doc)
    await session.commit()
    return len(docs)


def _load_manifest() -> dict:
    """读 sync 落下的增量台账。没有就返回空——勘误本身不依赖它，
    只有「判断有没有过期」这一件事需要。"""
    import json

    from copilot.config import get_settings

    path = get_settings().data_dir / "raw" / "yuque" / "_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@app.command()
def correct(
    keyword: str = typer.Argument(..., help="按标题搜要勘误的文档，如「京东面单」"),
    retire: bool = typer.Option(False, "--retire", help="整篇作废（语雀删了或彻底过时）"),
) -> None:
    """修正语雀文档里写错的内容。

    改动落在 `corrections/<slug>.md`，进 Git、可 diff、可回滚，
    下次 `copilot ingest` 时覆盖语雀原文，`deploy.sh` 会一起推到服务器。

    **不会碰 `data/raw/yuque/`**——那是 sync 的产物，永远保持和语雀一致。
    """
    from copilot.config import get_settings
    from copilot.ingest.chunker import parse_frontmatter
    from copilot.ingest.corrections import (
        load_corrections,
        render_correction,
        slugify,
    )

    settings = get_settings()
    manifest = _load_manifest()
    if not manifest:
        typer.secho(
            "找不到 data/raw/yuque/_manifest.json，先跑一次 `copilot sync-yuque`。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    kw = keyword.strip().lower()
    hits = [
        e
        for e in manifest.values()
        if kw in (e.get("title") or "").lower() or kw in (e.get("book_name") or "").lower()
    ]
    if not hits:
        typer.secho(f"没有标题或知识库名含「{keyword}」的文档。", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    hits.sort(key=lambda e: (e.get("book_name") or "", e.get("title") or ""))
    if len(hits) > 30:
        typer.secho(f"命中 {len(hits)} 篇，太多了，换个更具体的关键词。", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    for i, e in enumerate(hits, 1):
        typer.echo(f"  [{i}] {e.get('book_name')} · {e.get('title')}")
    idx = 1 if len(hits) == 1 else typer.prompt("选哪一篇", type=int)
    if not 1 <= idx <= len(hits):
        typer.secho("序号不在范围里。", fg=typer.colors.RED)
        raise typer.Exit(1)
    entry = hits[idx - 1]

    existing = load_corrections(settings.corrections_dir)
    prior = existing.get(entry["source_url"])
    if prior is not None:
        typer.secho(
            f"这篇已经有勘误了：{prior.name}，将在它的基础上改。", fg=typer.colors.CYAN
        )
        body = prior.body
        reason = prior.reason
        out_path = prior.path
    else:
        raw = settings.data_dir / "raw" / "yuque" / entry["path"]
        _, body = parse_frontmatter(raw.read_text(encoding="utf-8"))
        body = body.strip()
        reason = ""
        out_path = settings.corrections_dir / (
            slugify(f"{entry['book_slug']}-{entry['title']}", entry["book_slug"]) + ".md"
        )

    if retire:
        reason = reason or typer.prompt("作废原因（会写进文件，半年后你会需要它）")
        body = ""
    else:
        # ⭐ 用 click 的编辑器：它负责临时文件、编码和「用户没存就当放弃」。
        # 自己起 subprocess 的话，Windows 上要处理 notepad 不返回退出码这类破事
        edited = typer.edit(body, extension=".md")
        if edited is None:
            typer.secho("没有改动，什么都没写。", fg=typer.colors.YELLOW)
            raise typer.Exit(0)
        body = edited.strip()
        if not body:
            typer.secho(
                "正文被清空了。整篇作废请用 `copilot correct <关键词> --retire`。",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        reason = typer.prompt("改了什么、为什么改", default=reason or None)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_correction(
            target_url=entry["source_url"],
            title=f"{entry.get('book_name') or ''} · {entry['title']}".strip(" ·"),
            # 记下此刻语雀那边的版本。它后来又更新了的话，
            # `copilot corrections` 会把这条标成「过期」——这是这套机制的核心
            based_on=entry.get("content_updated_at", ""),
            reason=reason,
            body=body,
            retired=retire,
        ),
        encoding="utf-8",
    )
    typer.secho(f"已写入 {out_path}", fg=typer.colors.GREEN)
    typer.echo("接下来：")
    typer.echo("  copilot ingest        # 让勘误生效（本机）")
    typer.echo("  git add corrections/  # 改动进版本库")
    typer.echo("  ./deploy/deploy.sh    # 推到服务器")


@app.command()
def corrections(
    check: bool = typer.Option(False, "--check", help="有过期勘误时退出码非 0，给部署脚本用"),
) -> None:
    """列出所有人工勘误，并标出哪些已经过期。

    过期 = 写完这条勘误之后，语雀那篇原文又更新了。此时勘误仍然生效，
    但它依据的原文已经变了——可能语雀那边已经自己改对了，
    也可能改了别的地方而你的覆盖把新内容一起盖掉了。**需要人去看一眼。**
    """
    from copilot.config import get_settings
    from copilot.ingest.corrections import load_corrections, stale_corrections

    items = load_corrections(get_settings().corrections_dir)
    if not items:
        typer.echo("还没有任何勘误。")
        return

    # 按 target_url 索引，不拿 Correction 本身当 key——它是 slots dataclass，
    # 有 __eq__ 没 __hash__，塞进 dict 会直接 TypeError
    stale = {c.target_url: now for c, now in stale_corrections(items.values(), _load_manifest())}
    for c in sorted(items.values(), key=lambda x: x.name):
        mark, color = ("作废", typer.colors.MAGENTA) if c.retired else ("勘误", typer.colors.GREEN)
        if c.target_url in stale:
            mark, color = "过期", typer.colors.YELLOW
        typer.secho(f"  [{mark}] {c.title or c.name}", fg=color)
        typer.echo(f"         {c.reason}")
        typer.echo(f"         {c.target_url}")
        if c.target_url in stale:
            typer.secho(
                f"         ⚠️ 语雀已更新到 {stale[c.target_url]}"
                f"（勘误基于 {c.based_on}），去核对一下",
                fg=typer.colors.YELLOW,
            )

    typer.echo("")
    typer.secho(f"共 {len(items)} 条，其中过期 {len(stale)} 条", fg=typer.colors.CYAN)
    if check and stale:
        raise typer.Exit(1)


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


@app.command(name="corrections-export")
def corrections_export(
    dry_run: bool = typer.Option(False, "--dry-run", help="只看会写哪些文件，不落盘"),
) -> None:
    """把网页上写的勘误导成 `corrections/*.md`，好进版本管理。

    网页那一路存在数据库里——它要能立刻生效，而**服务器上的 corrections/
    目录每次部署都会被仓库版本整个覆盖**，所以不能往那儿写文件。

    代价是那些勘误不进 Git：没有 diff、没有 review、换台服务器要单独备份数据库。
    这条命令就是把它们捞回仓库的路：导一次、看一眼 diff、提交。

        uv run copilot corrections-export --dry-run   # 先看会写什么
        uv run copilot corrections-export             # 真写
        git add corrections/ && git commit

    导出**不会删数据库里的记录**。两边都在时以数据库为准（见 merge_corrections），
    所以导出之后行为不变；真想让文件版接管，把网页上那条删掉即可。
    """
    import asyncio

    asyncio.run(_corrections_export(dry_run))


async def _corrections_export(dry_run: bool) -> None:
    from sqlalchemy import select

    from copilot.config import get_settings
    from copilot.db.models import Correction as CorrectionRow
    from copilot.db.session import SessionLocal
    from copilot.ingest.corrections import load_corrections, render_correction, slugify

    settings = get_settings()
    async with SessionLocal() as session:
        rows = list((await session.execute(select(CorrectionRow))).scalars())

    if not rows:
        typer.secho("数据库里没有网页勘误。", fg=typer.colors.CYAN)
        return

    # 已有的文件按 target_url 索引：同一篇要覆盖原来那个文件，
    # 而不是按标题另起一个 slug——否则同一篇会有两个文件，ingest 直接抛
    existing = load_corrections(settings.corrections_dir)

    for row in rows:
        prior = existing.get(row.target_url)
        out_path = (
            prior.path
            if prior is not None and prior.path is not None
            else settings.corrections_dir / f"{slugify(row.title, row.id.hex[:8])}.md"
        )
        text = render_correction(
            target_url=row.target_url,
            title=row.title,
            based_on=row.based_on,
            reason=row.reason,
            body=row.body,
            retired=row.retired,
        )
        verb = "覆盖" if out_path.exists() else "新建"
        typer.echo(f"  {verb} {out_path.name}　← {row.title or row.target_url}")
        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")

    if dry_run:
        typer.secho(f"\n（--dry-run，没有真写）共 {len(rows)} 条", fg=typer.colors.YELLOW)
    else:
        typer.secho(
            f"\n导出 {len(rows)} 条到 {settings.corrections_dir}。"
            "看一眼 diff 再提交；数据库里的记录仍然保留。",
            fg=typer.colors.GREEN,
        )
