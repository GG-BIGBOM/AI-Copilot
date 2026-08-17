"""入库管线：本地 Markdown → documents + chunks（含向量）。

三条不能破的规矩：

1. **chunks.owner_id 必须与所属 document 一致。** 这是数据隔离的地基，
   写错了就是把 A 的文档泄漏给 B。整个项目只有这里往 chunks 写 owner_id。
2. **content_hash 没变就整篇跳过。** 省的是 embedding 调用，是真金白银。
3. **重新入库先删旧块。** 否则同一篇文档会在库里留下两代块，
   检索时旧内容和新内容一起冒出来，且没有任何报错。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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
            n = await _ingest_one(session, src, embedder, owner_id, force, settings)
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


async def _ingest_one(
    session: AsyncSession,
    src: SourceDoc,
    embedder: Embedder,
    owner_id: uuid.UUID | None,
    force: bool,
    settings,
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

    chunks = chunk_markdown(
        src.markdown, size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    if not chunks:
        return None

    vectors = embedder.embed_documents([c.content for c in chunks])

    if existing:
        doc = existing
        # 重新入库必须先清旧块，否则库里会留下两代内容一起被检索到
        await session.execute(delete(ChunkRow).where(ChunkRow.document_id == doc.id))
    else:
        doc = Document(owner_id=owner_id, source_type=src.source_type)
        session.add(doc)

    doc.title = src.title
    doc.source_url = src.source_url
    doc.stored_path = src.stored_path
    doc.original_filename = src.original_filename
    doc.size_bytes = src.size_bytes
    doc.content_hash = digest
    doc.status = "done"
    doc.error = None
    doc.chunk_count = len(chunks)
    await session.flush()  # 拿到 doc.id

    session.add_all(
        [
            ChunkRow(
                document_id=doc.id,
                # ⚠️ 隔离红线：必须跟着文档走，不能是别的值
                owner_id=doc.owner_id,
                ordinal=c.ordinal,
                content=c.content,
                embedding=vec,
                title=src.title,
                heading=c.heading,
                source_url=src.source_url,
                images=c.images or None,
            )
            for c, vec in zip(chunks, vectors, strict=True)
        ]
    )
    await session.commit()
    return len(chunks)
