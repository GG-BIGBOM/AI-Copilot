"""上传文件的解析测试。不打网络、不碰数据库，可以随便跑。

守住的都是**不报错的那类失败**：
    编码猜错 → 整篇变乱码，照样入库、永远检索不到
    表格跑到文末 → 字段和含义的对应关系没了
    扫描件 PDF → 一篇空文档标着「已完成」，用户搜不到还不知道为什么
"""

from __future__ import annotations

import pytest
from samples import make_docx, make_pdf, make_png, make_pptx, make_scanned_pdf

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


# ---------- 图片 / 扫描件（视觉模型）----------


class FakeVision:
    """假的读图客户端。真打 Kimi 的话这些测试要联网、要花钱、还不稳定。"""

    def __init__(self, reply="## 批量换货\n\n操作上限：一次 500 单", error=None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[bytes, str, str]] = []

    def transcribe(self, raw: bytes, mime: str = "image/png", hint: str = "") -> str:
        self.calls.append((raw, mime, hint))
        if self.error is not None:
            raise self.error
        return self.reply


def test_image_goes_through_vision(tmp_path):
    v = FakeVision()
    parsed = parse_upload(make_png(tmp_path / "界面截图.png"), vision=v)
    assert "操作上限：一次 500 单" in parsed.markdown
    assert len(v.calls) == 1
    assert v.calls[0][1] == "image/png"


def test_image_without_vision_says_so_in_plain_words(tmp_path):
    """没配 VISION_API_KEY 时要给一句人话，不能是 AttributeError。

    这条错误会原样存进 documents.error 显示给用户看。
    """
    with pytest.raises(ParseError, match="图片解析"):
        parse_upload(make_png(tmp_path / "a.png"))


def test_image_gets_a_heading_so_chunker_can_section_it(tmp_path):
    """图片没有天然的标题层级。不补 `#` 的话切分器给不出「第 N 节」，
    引用里就只剩一个光秃秃的文件名。"""
    v = FakeVision(reply="订单管理 › 批量换货，一次最多 500 单")
    parsed = parse_upload(make_png(tmp_path / "批量换货界面.png"), vision=v)
    assert parsed.markdown.startswith("# 批量换货界面")
    assert chunk_markdown(parsed.markdown, size=500, overlap=80)[0].heading


def test_image_with_no_text_is_rejected(tmp_path):
    """模型说「这张图没字」时不能入库一篇空文档——
    那会显示「已完成」却永远搜不到，和上传坏了无法区分。"""
    with pytest.raises(ParseError, match="没有可识别的文字"):
        parse_upload(make_png(tmp_path / "logo.png"), vision=FakeVision(reply=""))


def test_vision_failure_becomes_a_readable_parse_error(tmp_path):
    from copilot.providers.vision import VisionError

    v = FakeVision(error=VisionError("读图服务连不上（ConnectTimeout）"))
    with pytest.raises(ParseError, match="连不上"):
        parse_upload(make_png(tmp_path / "a.png"), vision=v)


def test_scanned_pdf_falls_back_to_vision(tmp_path):
    """⭐ 原来这里是直接拒收，现在改成逐页读图。"""
    v = FakeVision(reply="实施验收单\n\n客户名称：某某电商")
    parsed = parse_upload(make_scanned_pdf(tmp_path / "scan.pdf"), vision=v)
    assert "## 第 1 页" in parsed.markdown
    assert "客户名称：某某电商" in parsed.markdown
    assert "扫描件" in parsed.note
    assert len(v.calls) == 1


def test_scanned_pdf_without_vision_still_refuses(tmp_path):
    """没有视觉能力时，行为要和以前完全一致：明确拒收，不静默入库空文档。"""
    with pytest.raises(ParseError, match="扫描件"):
        parse_upload(make_scanned_pdf(tmp_path / "blank.pdf"))


def test_scanned_pdf_page_limit_is_disclosed(tmp_path, monkeypatch):
    """⭐ 截断必须写进 note。

    不说的话，用户搜不到第 21 页的内容时只会以为知识库不好用——
    而真正的原因是我们为了省钱主动没读。
    """
    from pypdf import PdfWriter

    from copilot.config import get_settings

    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=200, height=200)
    path = tmp_path / "long.pdf"
    with path.open("wb") as f:
        writer.write(f)

    # 改实例不改类：Settings 是 pydantic 模型，上限是字段不是类属性
    monkeypatch.setattr(get_settings(), "vision_pdf_max_pages", 2)
    v = FakeVision(reply="一些文字")
    parsed = parse_upload(path, vision=v)
    assert len(v.calls) == 2  # 只读了 2 页，没有偷偷读满 4 页
    assert "共 4 页" in parsed.note and "上限 2 页" in parsed.note


def test_one_bad_page_does_not_kill_the_whole_scan(tmp_path):
    """单页失败要留痕，不能静默跳过——用户会拿到一份缺页却看着完整的文档。"""
    from copilot.providers.vision import VisionError

    class FlakyVision(FakeVision):
        def transcribe(self, raw, mime="image/png", hint=""):
            self.calls.append((raw, mime, hint))
            if len(self.calls) == 1:
                raise VisionError("这一页超时了")
            return "第二页的内容"

    writer_path = tmp_path / "two.pdf"
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with writer_path.open("wb") as f:
        writer.write(f)

    parsed = parse_upload(writer_path, vision=FlakyVision())
    assert "没能识别" in parsed.markdown  # 第 1 页留了痕
    assert "第二页的内容" in parsed.markdown
