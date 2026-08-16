"""中文文档切分。

切分策略：**先按 Markdown 标题分段，段内再按长度切**。

为什么不直接按固定长度切：一刀切会把「拼多多授权步骤」的第 2 步和第 3 步
劈到两个块里，检索命中其中一块，答案就是残的。按标题分段能保住语义完整性。

每个块都带着 `heading`（所在章节路径），这是引用能显示成
「订单对接-拼多多.md · 第 2 节 授权与下载」的原因——没有它，
用户只知道答案来自某篇文档，不知道来自哪一段，溯源就是空话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Markdown ATX 标题：# 到 ######
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.M)
# 中文句子结束符。切分时优先在这些位置断开，别把句子劈两半
_SENTENCE_END = re.compile(r"(?<=[。！？；\n])")
# 标题里残留的 Markdown 强调标记与链接语法。章节名要拿去显示给用户看，
# 不清掉的话引用会长成「**2、搜索及扫码**」这样
_EMPHASIS_RE = re.compile(r"[*_`~]+")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean_heading(text: str) -> str:
    """把 Markdown 标题洗成可直接展示的纯文本。"""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" 　:：-")


@dataclass(slots=True)
class Chunk:
    """一个待入库的文本块。"""

    ordinal: int
    content: str
    heading: str | None  # 章节路径，如「一、营销管理 › 1、营销步骤」


@dataclass(slots=True)
class Section:
    """按标题切出来的一段。"""

    heading_path: list[str]
    body: str

    @property
    def heading(self) -> str | None:
        return " › ".join(self.heading_path) if self.heading_path else None


def split_sections(markdown: str) -> list[Section]:
    """按 Markdown 标题把正文切成若干段，并算出每段的标题路径。"""
    matches = list(_HEADING_RE.finditer(markdown))

    # 没有标题的文档（语雀里不少），整篇当一段
    if not matches:
        text = markdown.strip()
        return [Section(heading_path=[], body=text)] if text else []

    sections: list[Section] = []

    # 第一个标题之前的引言部分
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append(Section(heading_path=[], body=preamble))

    # 维护标题栈，用于算出层级路径
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = clean_heading(m.group(2))
        if not title:
            continue

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[m.end() : end].strip()
        if body:
            sections.append(Section(heading_path=[t for _, t in stack], body=body))

    return sections


def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    """把过长的段落按句子边界切开，带重叠。

    重叠是为了防止答案正好横跨两块的接缝——上一块的结尾会出现在下一块的开头。
    """
    if len(text) <= size:
        return [text]

    # 先按句子边界拆成最小单元
    pieces = [p for p in _SENTENCE_END.split(text) if p.strip()]

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        # 单句就超长（表格、打印模板 JSON、长列表），只能硬切
        if len(piece) > size:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            hard = [piece[i : i + size].strip() for i in range(0, len(piece), size)]
            # 硬切的尾巴常常只剩几个字符（如 JSON 末尾的 `ext1"}`），
            # 单独成块纯属噪声。并进上一块。
            if len(hard) > 1 and len(hard[-1]) < size // 4:
                hard[-2] = f"{hard[-2]}{hard[-1]}"
                hard.pop()
            chunks.extend(hard)
            continue

        if len(buf) + len(piece) <= size:
            buf += piece
        else:
            chunks.append(buf.strip())
            # 用上一块的尾部作为重叠开头
            tail = buf[-overlap:] if overlap else ""
            buf = tail + piece

    if buf.strip():
        chunks.append(buf.strip())

    return [c for c in chunks if c]


def chunk_markdown(
    markdown: str,
    *,
    size: int,
    overlap: int,
    min_chars: int = 5,
) -> list[Chunk]:
    """把一篇 Markdown 切成带章节信息的块。

    **合并策略**：真实语料里段落长度中位数只有 101 字（实测 786 篇语雀文档），
    远小于目标块长。若一段一块，会切出大量百字碎片——既浪费 embedding 调用，
    也让模型拿到的上下文太少。所以把相邻的短段打包进同一块，
    但**不跨一级标题合并**：「一、营销管理」和「二、短信查询」是两回事，
    混进一块会让检索命中后答非所问。

    Args:
        size: 目标块长度（中文按字符算）
        overlap: 超长段落切开时相邻块的重叠字符数
        min_chars: 短于此长度的段丢弃。**实测该设得很低**——语料里 20 字上下的
            短段大多是「（操作路径：【app】-【店铺销售统计】）」这类操作指引，
            正是用户最想要的答案。默认 5 只滤掉零宽空格之类的空壳。
    """
    sections = [s for s in split_sections(markdown) if len(s.body.strip()) >= min_chars]
    if not sections:
        # 有些文档的内容**全在标题里**，一个正文段落都没有
        # （如「拼多多面单模板设置步骤」，通篇只有两行 #### 标题）。
        # 按段落切会得到 0 块，整篇文档凭空消失且不报错——
        # 这类恰恰是「XX 怎么设置」的高频问题，不能丢。
        fallback = clean_heading(_HEADING_RE.sub(r"\2", markdown).replace("\n", " "))
        if len(fallback) < min_chars:
            return []
        return [Chunk(ordinal=0, content=fallback, heading=None)]

    chunks: list[Chunk] = []
    ordinal = 0

    def emit(text: str, heading: str | None) -> None:
        nonlocal ordinal
        text = text.strip()
        if len(text) < min_chars:
            return
        # 把章节路径拼进正文开头：检索时这些词本身就是有效信号
        # （用户问「营销管理怎么用」，命中的块正文里可能根本没有这四个字）
        content = f"{heading}\n\n{text}" if heading else text
        chunks.append(Chunk(ordinal=ordinal, content=content, heading=heading))
        ordinal += 1

    buf: list[Section] = []
    buf_len = 0
    buf_top: str | None = None

    def flush() -> None:
        """把缓冲里的若干段合成一块。

        合并后块的 heading 取第一段的（代表这块的主题），但**每段自己的子标题
        要写进正文**——否则「1、营销步骤」「2、结果统计」合并后就成了两段
        没头没尾的文字，模型分不清哪句属于哪一步。
        """
        nonlocal buf, buf_len, buf_top
        if not buf:
            return
        head = buf[0]
        parts: list[str] = [head.body.strip()]
        for sec in buf[1:]:
            leaf = sec.heading_path[-1] if sec.heading_path else None
            parts.append(f"【{leaf}】\n{sec.body.strip()}" if leaf else sec.body.strip())
        emit("\n\n".join(parts), head.heading)
        buf, buf_len, buf_top = [], 0, None

    for section in sections:
        top = section.heading_path[0] if section.heading_path else None
        body = section.body.strip()

        # 单段就超长：先冲掉缓冲，再把这段自己切开
        if len(body) > size:
            flush()
            for piece in _split_long_text(body, size, overlap):
                emit(piece, section.heading)
            continue

        # 换了一级标题，或者装不下了，就先封块
        if buf and (top != buf_top or buf_len + len(body) > size):
            flush()

        if not buf:
            buf_top = top
        buf.append(section)
        buf_len += len(body) + 2

    flush()
    return chunks


@dataclass(slots=True)
class ParsedDocument:
    """从磁盘读进来的一篇文档。"""

    title: str
    markdown: str
    source_url: str | None = None
    book_name: str | None = None
    slug: str | None = None
    content_updated_at: str | None = None
    extra: dict = field(default_factory=dict)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 sync.py 写出来的 YAML frontmatter。

    自己解析而不上 PyYAML：格式是我们自己写的，只有扁平的 key: value，
    值要么是 JSON 字符串要么是裸标量。为这点需求引入一个依赖不值得。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    import json

    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith('"'):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = raw.strip('"')
        meta[key.strip()] = raw

    return meta, text[m.end() :]
