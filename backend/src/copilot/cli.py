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

# ⚠️ 这里只有 typer 和 metrics 是模块级导入，别再往上加。
# 其余一律在子命令内部 import：`copilot --help` 要是把 SQLAlchemy、httpx、
# pydantic-ai 全拖起来，一次补全要等两秒。`metrics` 是纯 dataclass，没有代价
from copilot import metrics

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
        # 命令行没有会话，也就没有「这条会话钉在哪一版」。用默认空间（旗舰版）。
        # ⚠️ 不能不传：检索缺空间时是 fail closed 的，表现会是「这条命令
        # 突然什么都查不到」，而它正是部署完当场验一句用的工具。
        from copilot import spaces

        space_id = await spaces.default_id(session)
        llm = ChatLLM()
        try:
            if show_chunks:
                from copilot.retrieve import search

                result = await search(
                    session, question, embedder, reranker, space_id=space_id
                )
                typer.secho("── 召回的原文 ──", fg=typer.colors.BRIGHT_BLACK)
                for rc in result.chunks:
                    typer.secho(
                        f"[{rc.citation.n}] {rc.citation.label}　分数 {rc.citation.score:.4f}",
                        fg=typer.colors.BRIGHT_BLACK,
                    )
                    typer.secho(f"    {rc.content[:180]}…\n", fg=typer.colors.BRIGHT_BLACK)

            answer = await ask_stream(
                session, question, embedder, reranker, llm, space_id=space_id
            )
            # ⚠️ `ask_stream` 吐的是 `(kind, text)`，kind 是 `reasoning`（模型草稿）
            # 或 `content`（正文）。**草稿不是答案**：落库、判「暂无此内容」都只看
            # content（见 qa.StreamedAnswer）。
            # 这里原来把整个元组直接 echo 又 join，于是终端上是
            # `('content', '知识')('content', '库')…`，最后 join 抛
            # `TypeError: sequence item 0: expected str instance, tuple found`。
            # 详解档加草稿那一版（6af2c23）漏改了这条路——它只在 CLI 上，
            # 网页那条走的是 `routes/chat.py`，所以一直没人踩到。
            buf: list[str] = []
            for kind, piece in answer.stream:
                if kind != "content":
                    continue  # 草稿只给网页做「正在思考」用，命令行不打
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
        # ⚠️ 换行自己定死。`write_text` 默认 `newline=None`，Windows 上写出 CRLF——
        # 而这个文件下一步就要 `git add` 进版本库、再 rsync 到 Linux 服务器。
        # 同一个默认值曾经把 `deploy/backup.sh` 写成 CRLF，结果备份每天照常
        # "跑完"、每天什么都没备份（见 deploy.sh 文件头）
        newline="\n",
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


@app.command(name="seed-user")
def seed_user(
    email: str = typer.Option(..., "--email", help="账号邮箱"),
    password: str = typer.Option(..., "--password", help="明文口令"),
    admin: bool = typer.Option(False, "--admin", help="顺便给管理员"),
) -> None:
    """建一个演示账号，**绕过邀请码**。只给 docker-compose 那套本地环境用（W1.3）。

    ⚠️⚠️ **必须显式设 `COPILOT_ALLOW_SEED_USER=1` 才肯跑。**

    邀请码是这个站唯一的准入闸门（见 `db/models.InviteCode`）。
    留一个能凭命令行凭空造账号的口子，等于在那道闸门旁边开了一扇小门——
    而小门的危险不在于它存在，在于**有一天没人记得它存在**。
    多一个环境变量挡着，是让"在生产上手滑跑一次"这件事不可能悄悄发生：
    compose 里那一行写得明明白白，服务器上那一行根本不存在。

    已经有同名账号时**只改口令、不重建**——重建会把他的会话和文档一起带走。
    """
    import os

    if os.getenv("COPILOT_ALLOW_SEED_USER") != "1":
        typer.secho(
            "拒绝：seed-user 只在本地演示环境可用。\n"
            "要用的话显式设 COPILOT_ALLOW_SEED_USER=1（docker-compose 里已经设了）。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    import asyncio

    asyncio.run(_seed_user(email.strip().lower(), password, admin))


async def _seed_user(email: str, password: str, admin: bool) -> None:
    from sqlalchemy import select

    from copilot.auth.security import hash_password
    from copilot.config import get_settings
    from copilot.db.models import User
    from copilot.db.session import SessionLocal, engine

    s = get_settings()
    if len(password) < s.password_min_length:
        typer.secho(f"口令至少 {s.password_min_length} 位", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is None:
                user = User(email=email, password_hash=hash_password(password), is_admin=admin)
                session.add(user)
                verb = "建好了"
            else:
                # 只改口令。删了重建会连着把他的会话、文档、私有块一起带走，
                # 而这个命令的用途只是"让我能登进去看看"
                user.password_hash = hash_password(password)
                if admin:
                    user.is_admin = True
                verb = "已存在，改了口令"
            await session.commit()
        typer.secho(f"演示账号 {email} {verb}。", fg=typer.colors.GREEN)
    finally:
        await engine.dispose()


@app.command(name="backfill-tsv")
def backfill_tsv(
    batch: int = typer.Option(500, "--batch", help="一批多少块。内存和事务大小的闸门"),
    force: bool = typer.Option(False, "--force", help="连已经有分词的块也重算"),
) -> None:
    """给存量块补上词法索引 `content_tsv`（W1.2）。

    ⭐ **上线顺序是：迁移 → 这个命令 → 才谈开不开 `HYBRID_ENABLED`。**
    列刚加出来时旧块的它全是 NULL，也就是词法那一路一条都召不回——
    在那个状态下打开开关，量到的是"hybrid 没用"，而真相是它压根没数据。

    改了切词方式（换 jieba 模式、加自定义词典）之后要 `--force` 重跑一遍：
    索引侧和查询侧用不同切法的表现是「库里明明有这个词却搜不到」，
    而且没有任何报错（见 `copilot.lexical.cut`）。
    """
    import asyncio

    asyncio.run(_backfill_tsv(batch=batch, force=force))


async def _backfill_tsv(*, batch: int, force: bool) -> None:
    from sqlalchemy import bindparam, func, select, update

    from copilot import lexical
    from copilot.db.models import Chunk
    from copilot.db.session import SessionLocal, engine

    if not lexical.available():
        typer.secho(
            "没装 jieba，没法分词。装法：cd backend && uv sync --extra hybrid",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    done = 0
    try:
        while True:
            async with SessionLocal() as session:
                stmt = select(Chunk.id, Chunk.content).limit(batch)
                # ⭐ 默认只挑还没算过的，所以这个命令**可以断了再跑**。
                # 5000 块要打几十秒，中途 Ctrl+C 之后从头再来是很蠢的。
                # `--force` 时全部重算，靠 offset 翻页
                stmt = (
                    stmt.offset(done) if force else stmt.where(Chunk.content_tsv.is_(None))
                )
                rows = (await session.execute(stmt)).all()
                if not rows:
                    break
                await session.execute(
                    # ⚠️ **打在 `__table__` 上，不是打在 ORM 实体上。**
                    # `update(Chunk)` + 一串参数字典会走 SQLAlchemy 的
                    # "ORM bulk update by primary key" 那条路，它要求每个字典里
                    # 有一个叫 `id` 的键，报出来的是 "No primary key value supplied"——
                    # 一个和"我明明写了 WHERE"完全对不上的错误。走 Core 就没有这层猜测。
                    update(Chunk.__table__)
                    .where(Chunk.__table__.c.id == bindparam("cid"))
                    .values(content_tsv=func.to_tsvector("simple", bindparam("tok"))),
                    [{"cid": cid, "tok": lexical.tokenize(content)} for cid, content in rows],
                )
                await session.commit()
                done += len(rows)
                typer.echo(f"  已回填 {done} 块")
        async with SessionLocal() as session:
            left = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.content_tsv.is_(None))
            )
        typer.secho(f"完成：本次 {done} 块，仍为空的 {left} 块", fg=typer.colors.GREEN)
    finally:
        await engine.dispose()


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
            # `newline` 见上面 `correct` 那一处：勘误文件进版本库，也上服务器
            out_path.write_text(text, encoding="utf-8", newline="\n")

    if dry_run:
        typer.secho(f"\n（--dry-run，没有真写）共 {len(rows)} 条", fg=typer.colors.YELLOW)
    else:
        typer.secho(
            f"\n导出 {len(rows)} 条到 {settings.corrections_dir}。"
            "看一眼 diff 再提交；数据库里的记录仍然保留。",
            fg=typer.colors.GREEN,
        )


@app.command()
def admin(
    email: str = typer.Argument(..., help="要设为管理员的账号邮箱"),
    revoke: bool = typer.Option(False, "--revoke", help="改成取消管理员"),
) -> None:
    """把某个账号设为管理员（能在网页上生成邀请码）。

    ⚠️ **只有这一条路。** 网页上没有「把自己升级成管理员」的接口——
    留那个口子等于邀请制形同虚设：任何注册用户都能给自己发无限邀请码。

        uv run copilot admin you@example.com
        uv run copilot admin someone@example.com --revoke
    """
    import asyncio

    asyncio.run(_admin(email, revoke))


async def _admin(email: str, revoke: bool) -> None:
    from sqlalchemy import select

    from copilot.db.models import User
    from copilot.db.session import SessionLocal

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == email.strip().lower()))
        ).scalar_one_or_none()
        if user is None:
            typer.secho(f"库里没有这个账号：{email}", fg=typer.colors.RED)
            raise typer.Exit(1)
        user.is_admin = not revoke
        await session.commit()

    verb = "取消了" if revoke else "设为"
    typer.secho(f"{email} 已{verb}管理员。", fg=typer.colors.GREEN)


# ─────────────────────────────────────────────────────────
# 数据保留（M13 P6）
#
# ⚠️ **「定期删 N 天前的数据」不是一条保留策略，是一句愿望。**
# 真要能执行，得先回答清楚：哪一类留多久、为什么是这个数、谁来跑、
# 跑错了怎么办。下面这张表就是那几个答案。
#
#     普通 trace     30 天   够看清「最近一个月系统表现如何」，也够排查上周的问题。
#                            再久的价值急剧下降——一条两个月前的普通问答，
#                            没人会去翻它，而它占的是同一块 40G 磁盘
#     踩过的 trace   90 天   ⭐ 这一类是**评测集的原料**：「用户差评 → 找失败原因 →
#                            加进评测集」这个闭环有时要跨好几周才走完
#                            （见 db/models.py 里 RequestTrace 的注释）。
#                            30 天删掉，等于把还没来得及消化的失败样本扔了
#     出错的 trace   90 天   同理：`ok=false` 的行是排查线上事故的原始材料，
#                            而事故复盘常常发生在事发很久以后
#
# 聊天记录不在这里删。用户自己删会话时，业务数据当场就删干净了
# （见 routes/chat.py 的 delete_conversation）；trace 是**另一层**，
# 刻意不跟着删——它记的是「系统那天表现如何」，不是「他说过什么」。
# 这一点在 OPERATIONS.md 里也写着，别处的判断要以那两处为准。
# ─────────────────────────────────────────────────────────

# 普通请求台账保留天数
TRACE_RETENTION_DAYS = 30
# 带 👎 的、以及出错的那些，留久一点
TRACE_KEEP_LONGER_DAYS = 90


# 悬空截图保留多久。24 小时是「写一段纠错稿最长会花多久」的宽裕估计——
# 传了图、写到一半去开会、回来接着写，这条路要活得下来
ORPHAN_IMAGE_HOURS = 24


@app.command(name="prune-images")
def prune_images(
    apply: bool = typer.Option(False, "--apply", help="真的删。默认只看不动"),
    hours: int = typer.Option(
        ORPHAN_IMAGE_HOURS, "--hours", help=f"悬空多久算过期（默认 {ORPHAN_IMAGE_HOURS} 小时）"
    ),
) -> None:
    """清掉**没人认领**的纠错截图（M17.1）。**默认只预演。**

    纠错里的图是「先传、后绑」：用户贴图的时候还没有 correction 行可挂，
    所以那一刻它两个归属列都是空的。提交时才绑上——而**没提交的那些
    永远不会有人来绑**：写了一半关掉页面、传完又删掉、改稿时换了一张。
    库里没有任何一行指向它们，磁盘上却一直留着。

    这和 2026-08-24 上传事故里那两个孤儿文件是同一类问题：
    **没有任何地方看得出来它们存在。**

        copilot prune-images            # 看看会删多少
        copilot prune-images --apply    # 真删

    ⚠️ **文件只在没有别的行引用同一个路径时才删。** 图按内容寻址，
    同一张截图可能同时属于另一条纠错或另一篇文档——删早了，
    那边就变成一个指向空文件的行（表现是"图裂了"）。
    """
    import asyncio

    asyncio.run(_prune_images(apply=apply, hours=hours))


async def _prune_images(*, apply: bool, hours: int, maker=None) -> None:
    """`maker` 是给测试用的会话工厂，同 `_prune_traces`。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, select

    from copilot import assets
    from copilot.db.models import ImageAsset
    from copilot.db.session import SessionLocal

    maker = maker or SessionLocal
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    async with maker() as session:
        orphans = list(
            (
                await session.execute(
                    select(ImageAsset).where(
                        ImageAsset.document_id.is_(None),
                        ImageAsset.correction_id.is_(None),
                        ImageAsset.created_at < cutoff,
                    )
                )
            ).scalars()
        )
        typer.echo(f"悬空超过 {hours} 小时的截图：{len(orphans)} 张")
        if not orphans:
            typer.secho("没有要清的。", fg=typer.colors.GREEN)
            return
        if not apply:
            typer.secho("这是预演，什么都没删。确认无误后加 --apply。", fg=typer.colors.YELLOW)
            return

        ids = [row.id for row in orphans]
        paths = {row.storage_path for row in orphans}
        # 先删行，再看还有没有别的行用着同一个文件
        await session.execute(delete(ImageAsset).where(ImageAsset.id.in_(ids)))
        await session.flush()
        still_used = set(
            (
                await session.execute(
                    select(ImageAsset.storage_path).where(ImageAsset.storage_path.in_(paths))
                )
            ).scalars()
        )
        removed = 0
        for path in paths - still_used:
            try:
                assets.absolute_path(path, private=True).unlink(missing_ok=True)
                removed += 1
            except (OSError, ValueError) as e:  # noqa: PERF203 - 一张删不掉不该停下
                typer.secho(f"删不掉 {path}：{e}", fg=typer.colors.YELLOW)
        await session.commit()
        typer.secho(f"已清 {len(ids)} 行、{removed} 个文件。", fg=typer.colors.GREEN)


@app.command(name="prune-traces")
def prune_traces(
    apply: bool = typer.Option(False, "--apply", help="真的删。默认只看不动"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="预演。这本来就是默认行为，写出来只是为了脚本里能说清楚"
    ),
    days: int = typer.Option(
        TRACE_RETENTION_DAYS, "--days", help=f"普通台账保留天数（默认 {TRACE_RETENTION_DAYS}）"
    ),
    keep_days: int = typer.Option(
        TRACE_KEEP_LONGER_DAYS,
        "--keep-days",
        help=f"差评 / 出错的台账保留天数（默认 {TRACE_KEEP_LONGER_DAYS}）",
    ),
) -> None:
    """按保留策略清理 `request_trace`。**默认只预演，不删。**

    ⭐ **默认 dry-run 不是谨慎，是因为这条命令要挂进 systemd timer 每天跑。**
    一个默认就删的命令，配错一个参数就是每天悄悄多删一批，
    而台账没了是**不可再生**的——它记的是当时那一轮的检索链路，重跑不出来。
    所以真删必须显式写 `--apply`。

        copilot prune-traces              # 看看会删多少
        copilot prune-traces --apply      # 真删
    """
    import asyncio

    # ⚠️ 两个开关同时给 = 意图不明，**直接报错，不要替他猜**。
    # 猜「听保守的那个」看起来更友好，代价是 timer 里一条写错的命令
    # 会安安静静地每天什么都不做，而你以为它在清理——
    # 半年后磁盘满了才发现。报错至少会被 systemd 记成失败。
    if apply and dry_run:
        typer.secho(
            "--apply 和 --dry-run 不能同时给。要删就只写 --apply，要预演就都不写。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    asyncio.run(_prune_traces(apply=apply, days=days, keep_days=keep_days))


async def _prune_traces(*, apply: bool, days: int, keep_days: int, maker=None) -> None:
    """`maker` 是给测试用的会话工厂，和 `jobs/worker.run_once` 同一个套路。

    测试里每个用例跑在各自的事件循环上，而模块级的 `SessionLocal` 绑着一个
    共享连接池——借它写就会撞上「Event loop is closed」。留一个注入口比
    monkeypatch 模块变量干净：调用方看得见这件事，而不是靠夹具在背后换掉它。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, func, or_, select

    from copilot.db.models import RequestTrace
    from copilot.db.session import SessionLocal

    maker = maker or SessionLocal

    now = datetime.now(UTC)
    plain_before = now - timedelta(days=days)
    kept_before = now - timedelta(days=keep_days)

    # ⚠️ **一条 WHERE 就把三类都表达完，不要分三次删。**
    # 分三次的话，「普通那次」的条件必须显式排除掉差评和出错的行；
    # 漏写一个否定条件，就会在第一步把该留 90 天的行删掉，
    # 而那一步跑完之后没有任何办法知道它删过什么。
    # ⚠️⚠️ **`coalesce` 这一层不能省——少了它，这条命令什么都不会删，而且不报错。**
    #
    # SQL 的三值逻辑：`feedback` 是 NULL 时（389 行里有 376 行都是），
    # `feedback = 'down'` 的结果不是 false 而是 **NULL**；
    # `NULL OR false` 还是 NULL；再取反 `NOT NULL` **仍然是 NULL**，
    # 于是那一行既不满足「留久一点」也不满足「普通到期」，两边都落空。
    #
    # 2026-08-21 实测这三句的差别：
    #     where feedback is null                                    389
    #     where not (feedback = 'down' or ok = false)                  0   ← 全被 NULL 吃掉
    #     where not (coalesce(feedback,'') = 'down' or ok = false)    376
    #
    # 失效的样子是最坏的一种：timer 每天照常跑、日志每天写「到期 0 行」，
    # 而磁盘一直在涨。等有人发现，已经是几个月以后了。
    keeps_longer = or_(
        func.coalesce(RequestTrace.feedback, "") == "down", RequestTrace.ok.is_(False)
    )
    doomed = or_(
        (RequestTrace.created_at < plain_before) & ~keeps_longer,
        (RequestTrace.created_at < kept_before) & keeps_longer,
    )

    async with maker() as session:
        total = await session.scalar(select(func.count(RequestTrace.id)))
        n_doomed = await session.scalar(
            select(func.count(RequestTrace.id)).where(doomed)
        )
        n_down = await session.scalar(
            select(func.count(RequestTrace.id)).where(doomed, RequestTrace.feedback == "down")
        )
        n_err = await session.scalar(
            select(func.count(RequestTrace.id)).where(doomed, RequestTrace.ok.is_(False))
        )
        oldest = await session.scalar(select(func.min(RequestTrace.created_at)))

        typer.echo(f"台账共 {total} 行，最早一行 {oldest or '（空表）'}")
        typer.echo(f"保留策略：普通 {days} 天，差评 / 出错 {keep_days} 天")
        typer.echo(
            f"到期 {n_doomed} 行（其中差评 {n_down}、出错 {n_err}），"
            f"留下 {total - n_doomed} 行"
        )

        if not n_doomed:
            typer.secho("没有到期的台账，不用清。", fg=typer.colors.GREEN)
            return
        if not apply:
            typer.secho("这是预演，什么都没删。确认无误后加 --apply。", fg=typer.colors.YELLOW)
            return

        await session.execute(delete(RequestTrace).where(doomed))
        await session.commit()
        left = await session.scalar(select(func.count(RequestTrace.id)))
        typer.secho(f"已删 {n_doomed} 行，现存 {left} 行。", fg=typer.colors.GREEN)


# ─────────────────────────────────────────────────────────
# 周质量报告（M13 P10）
#
# ⭐ **不做后台 Dashboard，先做这条命令。** 理由和 M11 P2 不给反馈做页面
# 是同一条：线上 3 个真实账号，一周产不出几十条数据。一个页面要前端路由、
# 权限、图表库和一份长期维护，而这条命令十分钟能写完、一秒能跑完，
# 且**它回答的问题和页面完全一样**。
#
# 真正的判据是「这些问题现在有没有答案」：
#     最近 7 天出了多少问题？多少来自知识库、多少来自常识、多少是拒答？
#     有没有越过工具乱答 ERP？有多少差评？p95 首字延迟是多少？
# 有答案，就不需要页面。
# ─────────────────────────────────────────────────────────


@app.command(name="quality-report")
def quality_report(
    days: int = typer.Option(7, "--days", help="统计最近几天（默认 7）"),
    user: str = typer.Option(
        "", "--user", metavar="EMAIL", help="只看某个人的（默认全站）"
    ),
    route: str = typer.Option(
        "", "--route", help="只看某条路：direct | agent | canned（默认全部）"
    ),
) -> None:
    """最近 N 天的质量与成本概览。默认 7 天。

        copilot quality-report
        copilot quality-report --days 30
        copilot quality-report --user someone@example.com
        copilot quality-report --route agent --days 7     # ⭐ 灰度观察就看这一条

    `--user` 是给「某个人说慢 / 说答不准」那种排查用的：全站的 p95 被
    大多数正常请求压着，一个人的糟糕体验在里面看不出来。

    ⭐ **`--route agent` 是 M13 P12 的灰度观察入口。** 删掉旧直路之前要看的
    那一串数字（Agent 请求数、tools 为空、越过工具直答、常识回答、拒答、
    差评、出错、p95 首字）全在这一份报告里，只是把范围收到 Agent 那条路上。
    """
    import asyncio

    if route and route not in ("direct", "agent", "canned"):
        typer.secho(f"没有这条路：{route}（可选 direct / agent / canned）", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    asyncio.run(_quality_report(days, email=user or None, route=route or None))


# ⚠️ 口径统一在 `copilot.metrics` 里，这里只负责**把它印出来**。
# 管理台的 Overview 读的是同一批函数——两边各算一次的话，某天有人改了
# 「差评率的分母」或「延迟算不算寒暄」，另一处不会跟着变，
# 于是同一天在两个页面上有两个数，而看的人无从判断哪个是真的。
_pct = metrics.pct
_percentile = metrics.percentile


def _latency_line(label: str, lat: metrics.Latency) -> str:
    if lat.p50 is None:
        return f"  {label:<22} —"
    return f"  {label:<22} p50 {lat.p50:>6} ms    p95 {lat.p95:>6} ms    （{lat.count} 次）"


async def _quality_report(
    days: int, *, email: str | None = None, route: str | None = None, maker=None, user_id=None
) -> None:
    """`maker` / `user_id` 是注入口，同 `_prune_traces`：
    测试要用自己的会话工厂（每个用例一个事件循环），也要把统计范围
    限定在自己造的那几行上——否则它量的是开发库里攒下的全部历史。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from copilot.api import trace as trace_mod
    from copilot.db.models import RequestTrace, User
    from copilot.db.session import SessionLocal

    maker = maker or SessionLocal
    since = datetime.now(UTC) - timedelta(days=days)

    async with maker() as session:
        if email:
            user_id = await session.scalar(select(User.id).where(User.email == email.lower()))
            if user_id is None:
                typer.secho(f"库里没有这个用户：{email}", fg=typer.colors.RED)
                raise typer.Exit(code=1)

        stmt = select(RequestTrace).where(RequestTrace.created_at >= since)
        if user_id is not None:
            stmt = stmt.where(RequestTrace.user_id == user_id)
        if route is not None:
            stmt = stmt.where(RequestTrace.route == route)
        rows = list((await session.execute(stmt)).scalars())

        typer.echo("=" * 66)
        scope = f"　只看 {email}" if email else ""
        scope += f"　只看 {route} 路" if route else ""
        typer.echo(f"  最近 {days} 天　{since:%Y-%m-%d %H:%M} UTC 起{scope}")
        typer.echo("=" * 66)

        if not rows:
            typer.secho("这段时间一条请求都没有。", fg=typer.colors.YELLOW)
            return

        # ⭐ 所有口径都在 `copilot.metrics.summarize` 里，这里只排版
        stat = metrics.summarize(rows)
        total = stat.total
        typer.echo(f"  提问数                 {total}")
        typer.echo(f"  活跃用户               {stat.users}")
        typer.echo("")

        # ---- 答案来源（M13 P5 的那一列）----
        #
        # ⚠️ **老数据是 NULL**（那时候还没有这一列），单独列出来。
        # 把它并进任何一类都会让那一类凭空变大，而这份报告的用途
        # 恰恰是判断「常识兜底放开之后，到底有多少回答没有出处」
        by_source = stat.by_source
        label = {
            trace_mod.VERIFIED: "标准答案（人写定，未经模型改写）",
            trace_mod.KB: "知识库回答",
            trace_mod.GENERAL: "常识回答（无出处）",
            trace_mod.NO_ANSWER: "拒答",
            trace_mod.CANNED: "寒暄（0 成本）",
            trace_mod.TOOL: "工具（出方案/查文档）",
        }
        typer.echo("  答案来源")
        for key in (*label, metrics.LEGACY):
            if n := by_source.get(key):
                typer.echo(f"    {label.get(key, key):<22} {n:>5}   {_pct(n, total)}")
        typer.echo("")

        # ---- 反馈 ----
        typer.echo(f"  👍                     {stat.up}")
        typer.echo(f"  👎                     {stat.down}")
        # 分母是**被评价过的**，不是全部请求：绝大多数轮次没人点过，
        # 拿总数当分母只会得到一个恒定接近 0、看不出变化的数
        typer.echo(
            f"  差评率                 {stat.feedback_rate}"
            f"　（分母 = 被评价过的 {stat.up + stat.down} 轮）"
        )
        typer.echo("")

        # ---- 越过工具直答：M11 验收标准第 8 条 ----
        #
        # Agent 这一轮**一个工具都没调**，却给出了一段有出处样子的答案。
        # `agent/guard.py` 那道硬防线拦的就是它；这里数的是**漏过去的**。
        # ⚠️ 只看 Agent 路：直路的 tools 恒为空数组，混进来会把每一条直路
        # 都算成违规
        color = typer.colors.RED if stat.bypass else typer.colors.GREEN
        # ⭐ Agent 路上 `tools` 为空的轮次单独列一行（M13 P12 的灰度观察项）。
        # 它**不等于**违规：追问「你有哪些平台？」和寒暄都不该调工具。
        # 违规的是「tools 为空 + 写出了一段有出处样子的答案」，也就是下一行。
        # 两个数分开看，才分得出「Agent 在正常追问」和「Agent 在越线」
        if stat.agent_total:
            typer.echo(f"  Agent 轮次             {stat.agent_total}")
            typer.echo(
                f"    其中 tools 为空       {stat.agent_no_tool}"
                "   （追问 / 寒暄是正常的，不等于违规）"
            )
        typer.secho(f"  越过工具直答           {stat.bypass}", fg=color)
        typer.echo(f"  用户主动中断           {stat.interrupted}   {_pct(stat.interrupted, total)}")
        typer.echo(f"  出错                   {stat.errors}   {_pct(stat.errors, total)}")
        if stat.bypass_ids:
            typer.echo("    （这几轮值得逐条看）")
            # 命令行是管理员自己的终端，这里可以带上问题原文——
            # 管理台的概览页不行（见 metrics.Summary 的注释）
            by_id = {str(r.id): r for r in rows}
            for trace_id in stat.bypass_ids[:5]:
                typer.echo(f"      {trace_id}  {by_id[trace_id].question[:40]}")
        typer.echo("")

        # ---- 延迟：M13 P11 ----
        #
        # ⚠️ 寒暄那条路不算进去。它一次模型调用都不花、首字是毫秒级的，
        # 混进来会把 p50 拉到看不出问题——而这两个数字存在的意义
        # 就是回答「用户等了多久」
        typer.echo("  延迟（不含寒暄）")
        typer.echo(_latency_line("首字 TTFB", stat.ttfb))
        typer.echo(_latency_line("总时长", stat.duration))
        typer.echo("")

        # ---- token ----
        #
        # ⚠️ **只有一个总数，没有 input / output 的拆分。**
        # `usage.estimate_tokens` 是按字符数估的（连上下文一起算），
        # 它压根不区分进出。要拆就得解析流式响应末尾的 usage 字段——
        # 那是另一件事，而且这份报告的用途（看量级、看趋势）不需要它。
        # **宁可少报一个数，也不要报一个编出来的拆分。**
        typer.echo(f"  token 合计             {stat.tokens}")
        if stat.answered:
            typer.echo(
                f"  平均 token / 回答      {stat.tokens // stat.answered}　（不含寒暄）"
            )

        # 成本：**没有可靠的价格配置就不印。**
        # 硬编码一个单价，半年后模型换了、价格调了，报告会一本正经地
        # 给出一个错的成本——那比不给更糟，因为它看起来像真的。
        typer.echo("")
        typer.secho(
            "  成本未估算：没有配置 token 单价。硬编码一个价格会在换模型/调价后"
            "静静地给出错数字。", fg=typer.colors.BRIGHT_BLACK,
        )

        # ---- 台账健康度 ----
        oldest = await session.scalar(select(func.min(RequestTrace.created_at)))
        all_rows = await session.scalar(select(func.count(RequestTrace.id)))
        typer.echo("")
        typer.echo(f"  台账共 {all_rows} 行，最早 {oldest:%Y-%m-%d}　"
                   f"（保留策略见 copilot prune-traces）")


# ⚠️ **这一段必须留在文件最末尾。**
# 它原来在文件中间（`worker` 和 `corrections-export` 之间），
# 于是 `python -m copilot.cli <后面那些命令>` 一律报 "No such command"——
# 模块自上而下执行到这里就把 app() 跑了，下面的 `@app.command` 还没注册。
# 装出来的 `copilot` 入口点不受影响（它是 import 完整个模块再调 app），
# 所以这个坑只在用 `python -m` 的时候才踩得到，找起来相当费劲。
if __name__ == "__main__":
    app()
