"""勘误层：语雀原文写错了，在这里覆盖它。

**为什么不直接改 `data/raw/yuque/*.md`。** 那个目录是 sync 的产物，
`data/` 又在 `.gitignore` 里。直接改会同时踩三个坑：改动没有记录、
换台机器就没了、而且语雀那篇一更新，sync 重新落盘就把你的修改**静默**冲掉——
出问题时既查不出是谁改的，也查不出什么时候没的。

所以勘误是**独立的一层**：

    corrections/<slug>.md      进 Git，能 diff、能回滚、能看见是谁为什么改的
        ↓ ingest 时覆盖
    data/raw/yuque/**/*.md     sync 的产物，永远保持和语雀一致，不手工碰

一篇勘误就是一个文件，元信息全在 frontmatter 里（不另立 manifest——
两份数据总有一天会对不上）：

    target_url  被勘误的语雀文档，也是和原文对齐的唯一键
    based_on    写这篇勘误时，语雀那边的 content_updated_at
    reason      为什么改。**必填**，半年后你会需要它
    retired     true 表示整篇作废（语雀删了、或内容彻底过时），从索引里删掉

`based_on` 是这一层的核心。语雀那篇后来又更新了，说明勘误依据的原文已经变了，
这时候继续无声地覆盖就又回到了「静默冲掉」——只是方向反过来。
所以 `corrections --check` 会把这种情况标成**过期**，ingest 也会大声警告，
但仍然照常覆盖：过期的勘误多半仍比错的原文更接近事实，
真正危险的是「没人知道它过期了」，不是覆盖本身。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from copilot.ingest.chunker import parse_frontmatter
from copilot.ingest.pipeline import SourceDoc

SLUG_RE = re.compile(r"[^0-9a-zA-Z一-鿿]+")


class CorrectionError(ValueError):
    """勘误文件本身有问题（缺字段、目标写错）。"""


@dataclass(slots=True)
class Correction:
    path: Path
    target_url: str
    reason: str
    based_on: str = ""
    title: str = ""
    retired: bool = False
    body: str = ""

    @property
    def name(self) -> str:
        return self.path.stem


def _as_bool(raw: str) -> bool:
    return str(raw).strip().lower() in {"true", "1", "yes", "y"}


def parse_correction(path: Path) -> Correction:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    target = (meta.get("target_url") or "").strip()
    if not target:
        raise CorrectionError(f"{path.name}：frontmatter 缺 target_url")
    reason = (meta.get("reason") or "").strip()
    if not reason:
        raise CorrectionError(f"{path.name}：frontmatter 缺 reason（为什么改，必填）")

    retired = _as_bool(meta.get("retired", ""))
    body = body.strip()
    # 作废的勘误不需要正文；普通勘误没正文等于把文档清空，那多半是手滑
    if not retired and not body:
        raise CorrectionError(f"{path.name}：正文是空的。整篇作废请写 retired: true")

    return Correction(
        path=path,
        target_url=target,
        reason=reason,
        based_on=(meta.get("based_on") or "").strip(),
        title=(meta.get("title") or "").strip(),
        retired=retired,
        body=body,
    )


def load_corrections(root: Path) -> dict[str, Correction]:
    """读 corrections/ 下所有勘误，按 target_url 索引。

    两篇勘误指向同一个 url 是配置错误，直接抛——静默取其一的话，
    行为取决于文件名排序，改个文件名就换一份内容，没人查得出来。
    """
    if not root.exists():
        return {}

    out: dict[str, Correction] = {}
    for path in sorted(root.glob("*.md")):
        # `_` 开头的是草稿，README 是这一层自己的说明——都不是勘误。
        # 不排掉的话 README 会因为「缺 target_url」让整个 ingest 直接失败
        if path.name.startswith("_") or path.stem.lower() == "readme":
            continue
        c = parse_correction(path)
        if c.target_url in out:
            raise CorrectionError(
                f"{path.name} 和 {out[c.target_url].path.name} 指向同一篇文档："
                f"{c.target_url}。合并成一个文件。"
            )
        out[c.target_url] = c
    return out


def apply_corrections(
    docs: Iterable[SourceDoc], corrections: dict[str, Correction]
) -> tuple[list[SourceDoc], list[Correction], list[Correction]]:
    """把勘误盖到语雀原文上。

    返回 (入库用的文档列表, 生效的勘误, 没对上号的勘误)。

    **没对上号要单独返回，不能忽略。** 那说明 target_url 写错了、或者
    语雀那篇改了地址——两种都是「你以为改好了，其实一个字都没生效」，
    是这套机制最容易骗到人的失败方式。
    """
    used: dict[str, Correction] = {}
    out: list[SourceDoc] = []

    for src in docs:
        c = corrections.get(src.source_url or "")
        if c is None:
            out.append(src)
            continue
        used[c.target_url] = c
        if c.retired:
            continue  # 从入库列表里拿掉；库里的旧行由调用方按 retired 清
        out.append(
            SourceDoc(
                title=c.title or src.title,
                markdown=c.body,
                source_type=src.source_type,
                source_url=src.source_url,
                size_bytes=len(c.body.encode("utf-8")),
            )
        )

    missed = [c for url, c in corrections.items() if url not in used]
    return out, list(used.values()), missed


def stale_corrections(
    corrections: Iterable[Correction], manifest: dict[str, dict]
) -> list[tuple[Correction, str]]:
    """找出「写完之后语雀又更新了」的勘误。返回 (勘误, 语雀现在的时间戳)。"""
    by_url = {
        entry.get("source_url"): entry.get("content_updated_at", "")
        for entry in manifest.values()
    }
    out = []
    for c in corrections:
        now = by_url.get(c.target_url)
        # 语雀时间戳是 ISO8601 且位数固定，字符串比较等价于时间比较
        if now and c.based_on and now > c.based_on:
            out.append((c, now))
    return out


def slugify(title: str, fallback: str) -> str:
    s = SLUG_RE.sub("-", title).strip("-")
    return (s[:60] or fallback).lower()


def render_correction(
    *,
    target_url: str,
    title: str,
    based_on: str,
    reason: str,
    body: str,
    retired: bool = False,
) -> str:
    """写成和 sync.py 同一种 frontmatter 格式（扁平 key: value，字符串走 JSON）。"""
    lines = [
        "---",
        f"target_url: {target_url}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"based_on: {based_on}",
        f"reason: {json.dumps(reason, ensure_ascii=False)}",
    ]
    if retired:
        lines.append("retired: true")
    lines += ["---", "", body.strip(), ""]
    return "\n".join(lines)
