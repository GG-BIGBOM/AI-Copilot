"""入库管线：Markdown → documents + chunks（含向量）。

三条不能破的规矩：

1. **chunks.owner_id 必须与所属 document 一致。** 这是数据隔离的地基，
   写错了就是把 A 的文档泄漏给 B。整个项目只有 `write_chunks()` 一处
   往 chunks 写 owner_id，两条入库路径（语雀批量、用户上传）都走它。
2. **content_hash 没变就整篇跳过。** 省的是 embedding 调用，是真金白银。
3. **重新入库先删旧块。** 否则同一篇文档会在库里留下两代块，
   检索时旧内容和新内容一起冒出来，且没有任何报错。

两条入库路径的差别只在 documents 行谁建：
    语雀批量  `ingest_documents()` —— 按 source_url 找或建
    用户上传  `write_chunks()`     —— 行在上传那一刻就建好了（status=pending，
                                     好让用户立刻在列表里看到「排队中」），
                                     worker 只补块
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copilot import assets, spaces
from copilot import lexical as lexical_mod
from copilot.config import get_settings
from copilot.db.models import Chunk as ChunkRow
from copilot.db.models import Document
from copilot.ingest.chunker import chunk_markdown, parse_frontmatter
from copilot.providers.base import Embedder

Reporter = Callable[[str], None]


@dataclass
class IngestStats:
    documents: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceDoc:
    """一篇待入库的文档。"""

    title: str
    markdown: str
    source_type: str
    source_url: str | None = None
    stored_path: str | None = None
    original_filename: str | None = None
    size_bytes: int | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_yuque_dir(root: Path) -> Iterable[SourceDoc]:
    """读 sync.py 落下的 data/raw/yuque/**/*.md。"""
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        body = body.strip()
        if not body:
            continue
        # README 是目录自己的说明文档，不是语料。
        # 混进去会变成一篇「怎么用这个目录」的可检索文档，答案里冒出来非常怪
        if path.stem.lower() == "readme":
            continue
        book = meta.get("book") or ""
        title = meta.get("title") or path.stem
        yield SourceDoc(
            title=f"{book} · {title}" if book else title,
            markdown=body,
            source_type="yuque",
            source_url=meta.get("source_url"),
            size_bytes=len(body.encode("utf-8")),
        )


async def ingest_documents(
    session: AsyncSession,
    docs: Iterable[SourceDoc],
    embedder: Embedder,
    *,
    owner_id: uuid.UUID | None = None,
    force: bool = False,
    report: Reporter | None = None,
    space_id: uuid.UUID | None = None,
) -> IngestStats:
    """把一批文档切分、向量化、写库。

    Args:
        owner_id: None 写公共库（人人可见）；非 None 写该用户私有库
        force: 忽略 content_hash 判定，全部重新向量化
    """
    say = report or (lambda m: None)
    settings = get_settings()
    stats = IngestStats()
    docs = list(docs)
    stats.documents = len(docs)

    for i, src in enumerate(docs, 1):
        try:
            n = await _ingest_one(session, src, embedder, owner_id, force, settings, space_id)
        except Exception as e:  # noqa: BLE001 - 单篇失败不能中断整批
            await session.rollback()
            stats.failed += 1
            stats.errors.append(f"[{src.title}] {type(e).__name__}: {e}")
            continue

        if n is None:
            stats.skipped += 1
        else:
            stats.ingested += 1
            stats.chunks += n

        if i % 25 == 0 or i == len(docs):
            say(
                f"  {i}/{len(docs)}　入库 {stats.ingested}　跳过 {stats.skipped}"
                f"　块 {stats.chunks}　失败 {stats.failed}"
            )

    return stats


async def _write_tsv(session: AsyncSession, document_id: uuid.UUID) -> int:
    """给这篇文档的块补上 `content_tsv`（W1.2）。返回写了几块。

    ⭐ **切词在 Python 侧（jieba），`to_tsvector` 只负责收下已经切好的词。**
    Postgres 没有中文分词配置，理由和用法见 `copilot.lexical` 文件头。

    ⚠️ **没装 jieba 时静静跳过，不抛。** `hybrid` 是可选 extra，
    服务器上默认不装；入库因为一个默认关着的功能而失败，是不能接受的。
    代价是这些块的 `content_tsv` 为 NULL——而 NULL 在词法查询里就是
    "不命中"，也就是退回纯向量检索，那本来就是 `HYBRID_ENABLED=false` 的行为。

    ⚠️ **一条 executemany，不是一块一个 round trip。** 语雀全量入库是
    5000 块量级，一块一次的话光网络往返就要几十秒——而这一步本该是免费的。
    """
    if not lexical_mod.available():
        return 0
    rows = (
        await session.execute(
            select(ChunkRow.id, ChunkRow.content).where(ChunkRow.document_id == document_id)
        )
    ).all()
    params = [
        {"cid": cid, "tok": lexical_mod.tokenize(content)} for cid, content in rows
    ]
    if not params:
        return 0
    await session.execute(
        # ⚠️ **打在 `__table__` 上，不是打在 ORM 实体上。**
        # `update(ChunkRow)` + 一串参数字典会走 SQLAlchemy 的
        # "ORM bulk update by primary key" 那条路，它要求每个字典里
        # 有一个叫 `id` 的键，报出来的是 "No primary key value supplied"——
        # 一个和"我明明写了 WHERE"完全对不上的错误。走 Core 就没有这层猜测。
        update(ChunkRow.__table__)
        .where(ChunkRow.__table__.c.id == bindparam("cid"))
        .values(content_tsv=func.to_tsvector("simple", bindparam("tok"))),
        params,
    )
    return len(params)


async def write_chunks(
    session: AsyncSession,
    doc: Document,
    markdown: str,
    embedder: Embedder,
    settings=None,
    image_positions: dict[str, dict] | None = None,
) -> int:
    """切分 + 向量化 + 用新块整体替换该文档的旧块。返回块数，0 表示切不出内容。

    ⚠️ **这是全项目唯一往 `chunks.owner_id` 写值的地方**，且只能取
    `doc.owner_id`。隔离的地基在这一行上——写成别的值不会报错，
    只会在某天让 A 的私有文档出现在 B 的答案里。

    `image_positions` 是「磁盘相对路径 → 第几页 / 第几张 slide / 哪个工作表」，
    解析嵌图时攒出来的（M17）。**只有解析器知道这件事**——正文里的
    `![](asset://…)` 不带位置信息，而位置错了的表现是「答案配了另一页的截图」。

    不提交事务，交给调用方：上传那条路径还要在同一个事务里改 `status`。
    """
    s = settings or get_settings()
    chunks = chunk_markdown(markdown, size=s.chunk_size, overlap=s.chunk_overlap)
    if not chunks:
        return 0

    # 先向量化、再动库。embedding 是网络调用，失败率远高于本地写库；
    # 顺序反了的话，旧块已删、新块还没到，文档就凭空空了一段。
    vectors = embedder.embed_documents([c.content for c in chunks])

    await session.flush()  # 新建的 Document 在这里才拿到 id
    # 重新入库必须先清旧块，否则库里会留下两代内容一起被检索到
    await session.execute(delete(ChunkRow).where(ChunkRow.document_id == doc.id))

    session.add_all(
        [
            ChunkRow(
                document_id=doc.id,
                # ⚠️ 隔离红线：必须跟着文档走，不能是别的值
                owner_id=doc.owner_id,
                # ⚠️ 同 owner_id：只能取所属文档的那一个。写成别的值不会报错，
                # 只会让这一块出现在错误的 ERP 版本的答案里
                knowledge_space_id=doc.knowledge_space_id,
                ordinal=c.ordinal,
                content=c.content,
                embedding=vec,
                title=doc.title,
                heading=c.heading,
                source_url=doc.source_url,
                images=c.images or None,
            )
            for c, vec in zip(chunks, vectors, strict=True)
        ]
    )
    # ⚠️ 必须先 flush：新块还在 session 的待写队列里，
    # `_write_tsv` 是按 `document_id` 去库里查的，不 flush 会一块都查不到——
    # 表现是"新入库的文档词法检索永远搜不到"，而且没有任何报错
    await session.flush()

    # 词法索引（W1.2）。**和块一起写，不做成异步补算**——补算意味着
    # 有一段时间里新块只有向量没有分词，而那段时间里的答案会时好时坏，
    # 没有任何报错，也无从复现。
    await _write_tsv(session, doc.id)

    # M14-B 双写：块上的 `images` 仍是事实来源，这里再落一份带 owner 的资产行，
    # 好让私有图能走要鉴权的 `/api/images/{id}`。**放在 add_all 之后**——
    # 它要按这一轮的图去删旧行，得先知道这一轮有哪些图
    await assets.sync_document_assets(
        session, doc, [img for c in chunks for img in c.images], positions=image_positions
    )

    doc.chunk_count = len(chunks)
    return len(chunks)


async def _ingest_one(
    session: AsyncSession,
    src: SourceDoc,
    embedder: Embedder,
    owner_id: uuid.UUID | None,
    force: bool,
    settings,
    space_id: uuid.UUID | None = None,
) -> int | None:
    """入库一篇。返回块数；返回 None 表示内容没变、已跳过。"""
    digest = src.content_hash

    # 同一 owner + 同一来源视为同一篇文档
    stmt = select(Document).where(
        Document.source_url == src.source_url
        if src.source_url
        else Document.title == src.title
    )
    stmt = stmt.where(
        Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing and existing.content_hash == digest and not force and existing.status == "done":
        return None

    # ⚠️ 同上传那条路（`routes/docs.py`）：`knowledge_space_id` 是 NOT NULL，
    # 不写就是每一次入库都 NotNullViolation。**在建行之前就取好**——
    # 放到 `session.add()` 之后再 await，会触发 autoflush 把一个字段还没填全的
    # Document 刷进库，报出来的是「title 不能为空」，指不到真正的原因。
    # 默认给旗舰版：`copilot ingest` 灌的就是语雀那套旗舰版语料，
    # 和 M14-A 的回填规则一致
    if existing is None or existing.knowledge_space_id is None:
        space_id = space_id or await spaces.default_id(session)

    if existing:
        doc = existing
        if doc.knowledge_space_id is None:
            doc.knowledge_space_id = space_id
    else:
        doc = Document(
            owner_id=owner_id, source_type=src.source_type, knowledge_space_id=space_id
        )
        session.add(doc)

    doc.title = src.title
    doc.source_url = src.source_url
    doc.stored_path = src.stored_path
    doc.original_filename = src.original_filename
    doc.size_bytes = src.size_bytes
    doc.content_hash = digest
    doc.status = "done"
    doc.error = None

    n = await write_chunks(session, doc, src.markdown, embedder, settings)
    if n == 0:
        # 切不出块就当整篇没来过：回滚掉刚才对 documents 的插入/改动，
        # 已存在的那篇保持原样（连旧块一起）
        await session.rollback()
        return None

    await session.commit()
    return n
