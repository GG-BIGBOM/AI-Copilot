"""把语雀知识库同步到本地文件。

先落文件再入库，是为了把「抓取」和「检索」两件事分开验证——同时上，
出问题时分不清是解析的锅还是切分的锅。

产物：
    data/raw/yuque/_manifest.json          增量台账
    data/raw/yuque/<book_slug>/<doc>.md    带 frontmatter 的正文

增量靠 manifest 里的 content_updated_at：语雀自己给的时间戳，
不用下载正文就能判断跳过，比正文 hash 更省。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from copilot.config import get_settings
from copilot.sources.yuque import (
    Book,
    Doc,
    YuqueClient,
    YuqueError,
    YuqueRestricted,
    build_breadcrumbs,
    parse_login,
)

MANIFEST_NAME = "_manifest.json"


class Reporter(Protocol):
    """进度回调，让 CLI 决定怎么显示。"""

    def __call__(self, message: str) -> None: ...


@dataclass
class SyncStats:
    books: int = 0
    total_docs: int = 0
    fetched: int = 0
    skipped: int = 0
    restricted: int = 0  # 私密文档，无权限——不是错误
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    restricted_titles: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.fetched


def _safe_name(name: str) -> str:
    """文件名消毒。Windows 下这些字符会直接报错。"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip(". ")[:120] or "untitled"


def _doc_to_markdown(doc: Doc, breadcrumb: list[str]) -> str:
    """写成带 YAML frontmatter 的 Markdown，元信息和正文放一起，入库时直接读。"""
    crumb = " / ".join(breadcrumb)
    lines = [
        "---",
        f"title: {json.dumps(doc.title, ensure_ascii=False)}",
        f"book: {json.dumps(doc.book_name, ensure_ascii=False)}",
        f"book_slug: {doc.book_slug}",
        f"slug: {doc.slug}",
        f"doc_id: {doc.id}",
        f"source_url: {doc.source_url}",
        f"content_updated_at: {doc.content_updated_at}",
        f"word_count: {doc.word_count}",
        f"breadcrumb: {json.dumps(crumb, ensure_ascii=False)}",
        "---",
        "",
        doc.markdown,
        "",
    ]
    return "\n".join(lines)


def load_manifest(root: Path) -> dict[str, dict]:
    path = root / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_manifest(root: Path, manifest: dict[str, dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def sync_yuque(
    url_or_login: str,
    *,
    only_books: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    report: Reporter | None = None,
) -> SyncStats:
    """抓取整个团队空间下所有公开知识库到本地文件。

    Args:
        url_or_login: https://www.yuque.com/xxx 或裸 login
        only_books: 只抓这些 book slug；None 表示全抓
        limit: 每个库最多抓几篇，用于小批量验证
        force: 忽略增量判定，全量重抓
    """
    say = report or (lambda m: None)
    root = get_settings().data_dir / "raw" / "yuque"
    root.mkdir(parents=True, exist_ok=True)

    manifest = {} if force else load_manifest(root)
    stats = SyncStats()
    login = parse_login(url_or_login)

    with YuqueClient() as client:
        group_id, group_name = client.fetch_group_id(login)
        say(f"空间：{group_name}（login={login}, id={group_id}）")

        books = client.list_books(group_id)
        if only_books:
            wanted = set(only_books)
            books = [b for b in books if b.slug in wanted]
        stats.books = len(books)
        stats.total_docs = sum(b.items_count for b in books)
        say(f"公开知识库 {len(books)} 个，共约 {stats.total_docs} 篇\n")

        for book in books:
            _sync_book(client, login, book, root, manifest, stats, limit, say)
            save_manifest(root, manifest)  # 每个库存一次，中途挂了也不用重头来

    save_manifest(root, manifest)
    return stats


def _sync_book(
    client: YuqueClient,
    login: str,
    book: Book,
    root: Path,
    manifest: dict[str, dict],
    stats: SyncStats,
    limit: int | None,
    say: Reporter,
) -> None:
    try:
        nodes = client.fetch_toc(book.id)
    except YuqueError as e:
        stats.failed += 1
        stats.errors.append(f"[{book.slug}] 目录获取失败: {e}")
        say(f"  ✗ {book.name}: 目录获取失败")
        return

    crumbs = build_breadcrumbs(nodes)
    docs = [n for n in nodes if n.type == "DOC" and n.slug]
    if limit:
        docs = docs[:limit]

    book_dir = root / _safe_name(book.slug)
    book_dir.mkdir(parents=True, exist_ok=True)

    fetched = skipped = failed = restricted = 0
    for node in docs:
        key = f"{book.slug}/{node.slug}"
        prev = manifest.get(key)

        try:
            doc = client.fetch_doc(login, book, node)
        except YuqueRestricted:
            # 作者设了权限，匿名拿不到。重试无用，也不该当成错误
            restricted += 1
            stats.restricted_titles.append(f"{book.name} / {node.title}")
            continue
        except YuqueError as e:
            # 单篇失败不中断整库——这是长时间抓取能不能跑完的关键
            failed += 1
            stats.errors.append(f"[{key}] {e}")
            continue

        # 增量：语雀给的时间戳没变就跳过。放在下载后判断是因为
        # 目录接口不带这个字段，省不掉这一次请求，但省掉了写盘和后续 embedding
        if not doc.content_updated_at:
            pass  # 没有时间戳就只能每次都写
        elif prev and prev.get("content_updated_at") == doc.content_updated_at:
            skipped += 1
            continue

        path = book_dir / f"{_safe_name(node.slug)}.md"
        path.write_text(_doc_to_markdown(doc, crumbs.get(node.uuid, [])), encoding="utf-8")

        manifest[key] = {
            "title": doc.title,
            "book_slug": book.slug,
            "book_name": book.name,
            "source_url": doc.source_url,
            "content_updated_at": doc.content_updated_at,
            "word_count": doc.word_count,
            "path": str(path.relative_to(root)).replace("\\", "/"),
        }
        fetched += 1

    stats.fetched += fetched
    stats.skipped += skipped
    stats.restricted += restricted
    stats.failed += failed

    mark = "✗" if failed else "✓"
    line = f"  {mark} {book.name:<12} 新增/更新 {fetched:>3}　跳过 {skipped:>3}"
    if restricted:
        line += f"　私密 {restricted:>2}"
    if failed:
        line += f"　失败 {failed:>2}"
    say(line)
