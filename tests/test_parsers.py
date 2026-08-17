"""上传文件的解析测试。不打网络、不碰数据库，可以随便跑。

守住的都是**不报错的那类失败**：
    编码猜错 → 整篇变乱码，照样入库、永远检索不到
    表格跑到文末 → 字段和含义的对应关系没了
    扫描件 PDF → 一篇空文档标着「已完成」，用户搜不到还不知道为什么
"""

from __future__ import annotations

import pytest
from samples import make_docx, make_pdf, make_pptx, make_scanned_pdf

from copilot.ingest.chunker import chunk_markdown
from copilot.ingest.parsers import ParseError, parse_upload

# ---------- 纯文本 ----------


def test_utf8_text(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 面单设置\n\n先绑定物流账号。", encoding="utf-8")
    assert "绑定物流账号" in parse_upload(p).markdown


def test_gbk_text_decodes(tmp_path):
    """中文 Windows 的记事本默认存 GBK。没有兜底就是 UnicodeDecodeError，
    用 errors='replace' 则更糟——不报错，但整篇是「锟斤拷」。"""
    p = tmp_path / "gbk.txt"
    p.write_bytes("旺店通的退货入库怎么操作".encode("gb18030"))
    assert parse_upload(p).markdown == "旺店通的退货入库怎么操作"


def test_utf8_bom_stripped(tmp_path):
    """带 BOM 的 UTF-8（Excel/记事本导出的常态）。BOM 留在开头会让首个
    Markdown 标题的 `#` 不在行首，整篇的章节结构就散了。"""
    p = tmp_path / "bom.md"
    p.write_bytes("﻿# 标题\n\n正文".encode())
    assert parse_upload(p).markdown.startswith("# 标题")


def test_undecodable_bytes_raise(tmp_path):
    p = tmp_path / "bin.txt"
    p.write_bytes(b"\xff\xfe\xfa\x00\x01\x02\x03" * 8 + b"\xff")
    with pytest.raises(ParseError, match="编码"):
        parse_upload(p)


def test_unsupported_extension(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(ParseError, match="不支持"):
        parse_upload(p)


def test_suffix_can_be_given_explicitly(tmp_path):
    """落盘名是 uuid，后缀虽然保留，但仍支持显式指定——
    worker 用的是原始文件名的后缀，这样即使落盘规则改了也不影响解析。"""
    p = tmp_path / "no-extension"
    p.write_text("正文内容", encoding="utf-8")
    assert parse_upload(p, suffix=".md").markdown == "正文内容"


# ---------- docx ----------


def test_docx_headings_become_markdown(tmp_path):
    """标题样式必须转成 `#`。丢了的话，整篇变一坨没有章节的长文本，
    引用就只能说「来自某篇文档」，说不出来自哪一节。"""
    md = parse_upload(make_docx(tmp_path / "a.docx")).markdown
    assert "# 一、电子面单设置" in md
    assert "## 1、模板选择" in md


def test_docx_table_stays_in_place(tmp_path):
    """⭐ 表格必须留在它所属的那一节里，不能被甩到文末。

    python-docx 的 paragraphs / tables 是两个独立列表，按它们拼装的实现
    会让这条断言失败——而线上的表现只是「答案里字段对不上」。
    """
    md = parse_upload(make_docx(tmp_path / "a.docx")).markdown
    assert "| 运单号 |" in md, "表格没转成 Markdown 表格"
    assert md.index("| 运单号 |") < md.index("保存后即可打印"), "表格跑到正文后面去了"


def test_docx_chunks_keep_section_path(tmp_path):
    """解析出来的 Markdown 要能被既有切分器认出章节——这是转 Markdown 的全部目的。"""
    md = parse_upload(make_docx(tmp_path / "a.docx")).markdown
    chunks = chunk_markdown(md, size=500, overlap=80)
    assert chunks
    assert any(c.heading and "电子面单设置" in c.heading for c in chunks)


def test_corrupt_docx_raises_readable_error(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"not a zip file at all")
    with pytest.raises(ParseError, match="Word"):
        parse_upload(p)


# ---------- pptx ----------


def test_pptx_one_section_per_slide(tmp_path):
    md = parse_upload(make_pptx(tmp_path / "a.pptx")).markdown
    assert "## 第 1 页 面单打印流程" in md
    assert "## 第 2 页 常见问题" in md
    assert "第一步：绑定物流账号" in md


def test_pptx_title_not_duplicated(tmp_path):
    """标题占位符本身也是个文本框，不去重的话每页标题会出现两遍。"""
    md = parse_upload(make_pptx(tmp_path / "a.pptx")).markdown
    assert md.count("面单打印流程") == 1


# ---------- pdf ----------


def test_pdf_extracts_text_per_page(tmp_path):
    parsed = parse_upload(make_pdf(tmp_path / "a.pdf"))
    assert "Hello PDF world" in parsed.markdown
    assert "## 第 1 页" in parsed.markdown
    assert parsed.note == "共 1 页"


def test_scanned_pdf_refuses_instead_of_ingesting_nothing(tmp_path):
    """⭐ 扫描件必须报错，不能静默入库。

    不拦的话：状态「已完成」、块数 0、用户永远搜不到，且没有任何地方提示。
    这是本次里程碑最不该出现的那类失败。
    """
    with pytest.raises(ParseError, match="扫描件"):
        parse_upload(make_scanned_pdf(tmp_path / "blank.pdf"))
