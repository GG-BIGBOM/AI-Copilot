"""切分器测试。

切分质量直接决定检索天花板：块切碎了答案不完整，切大了噪声淹没信号，
章节信息丢了引用就没法溯源。这些都不会报错，只会让效果悄悄变差——
所以必须用测试钉住。
"""

from __future__ import annotations

from copilot.ingest.chunker import (
    chunk_markdown,
    clean_heading,
    parse_frontmatter,
    split_sections,
)

SAMPLE = """引言部分，没有标题。

### 一、营销管理

营销管理的总体说明。

#### 1、营销步骤

步骤一：设计短信模板。步骤二：新建营销方案。

#### 2、结果统计

统计营销后的订单数与金额。

### 二、短信发送查询

用于查询通过系统发送的短信。
"""


# ---------- 分段 ----------


def test_sections_capture_preamble():
    """标题之前的引言不能丢——很多语雀文档的关键说明就在开头。"""
    secs = split_sections(SAMPLE)
    assert secs[0].heading_path == []
    assert "引言部分" in secs[0].body


def test_sections_build_heading_path():
    """子标题要带上父标题，引用才能显示完整位置。"""
    secs = split_sections(SAMPLE)
    paths = [s.heading for s in secs if s.heading]
    assert "一、营销管理 › 1、营销步骤" in paths
    assert "一、营销管理 › 2、结果统计" in paths


def test_heading_stack_pops_on_same_level():
    """同级标题要替换而不是累加，否则路径会越拼越长。"""
    secs = split_sections(SAMPLE)
    last = [s for s in secs if s.heading and s.heading.startswith("二、")]
    assert last, "第二个一级标题没被识别"
    assert last[0].heading == "二、短信发送查询", "同级标题不该继承前一个的路径"


def test_document_without_headings():
    """语雀里不少文档没有任何标题，整篇当一段，不能丢。"""
    secs = split_sections("就是一段大白话，没有任何标题。")
    assert len(secs) == 1
    assert secs[0].heading is None


def test_empty_document():
    assert split_sections("") == []
    assert split_sections("   \n\n  ") == []


# ---------- 切块 ----------


def test_chunks_carry_heading():
    chunks = chunk_markdown(SAMPLE, size=500, overlap=80)
    assert chunks, "样本切不出块"
    assert all(c.heading or c.ordinal == 0 for c in chunks)
    assert any(c.heading and "营销管理" in c.heading for c in chunks)


def test_heading_is_prepended_to_content():
    """章节名要拼进正文——用户问「营销管理怎么用」时，
    命中的块正文里可能根本没有这四个字，靠标题才能召回。"""
    chunks = chunk_markdown(SAMPLE, size=500, overlap=80)
    sec = next(c for c in chunks if c.heading and "营销管理" in c.heading)
    assert sec.content.startswith("一、营销管理")


def test_merged_subheadings_survive_in_content():
    """多个小节合并成一块时，各自的子标题必须写进正文，
    否则合并后就是几段没头没尾的文字，模型分不清哪句属于哪一步。"""
    chunks = chunk_markdown(SAMPLE, size=500, overlap=80)
    merged = next(c for c in chunks if c.heading and "营销管理" in c.heading)
    assert "1、营销步骤" in merged.content
    assert "2、结果统计" in merged.content
    assert "步骤一" in merged.content


def test_long_section_is_split():
    long_body = "### 标题\n\n" + "这是一句测试文本。" * 200  # 约 1800 字
    chunks = chunk_markdown(long_body, size=300, overlap=50)
    assert len(chunks) > 1, "超长段落应当被切开"
    # 允许拼上的标题带来一点超出
    assert all(len(c.content) < 300 + 120 for c in chunks)


def test_overlap_preserves_boundary_context():
    """相邻块要有重叠，防止答案正好横跨接缝被切断。"""
    body = "".join(f"第{i}句话的内容在这里。" for i in range(60))
    chunks = chunk_markdown(f"### 标题\n\n{body}", size=200, overlap=60)
    assert len(chunks) >= 2
    prev_tail = chunks[0].content[-40:]
    # 后一块的开头应当能在前一块的尾部找到痕迹
    assert any(seg and seg in chunks[1].content for seg in [prev_tail[-20:]])


def test_only_empty_shells_dropped():
    """默认阈值只滤空壳。20 字上下的操作路径是真内容，实测语料里大量存在，
    丢了等于把用户最想要的答案扔掉。"""
    md = "### 甲\n\n​\n\n### 乙\n\n（操作路径：【app】-【店铺销售统计】）"
    chunks = chunk_markdown(md, size=500, overlap=50)
    joined = " ".join(c.content for c in chunks)
    assert "店铺销售统计" in joined, "短的操作路径不该被当噪声丢掉"


def test_short_sections_are_packed_together():
    """相邻短段要合并——语料中位数只有 101 字，一段一块会切出海量碎片。"""
    md = "### 步骤\n\n" + "\n\n".join(f"#### 第{i}步\n\n这一步的说明文字。" for i in range(1, 9))
    chunks = chunk_markdown(md, size=500, overlap=50)
    assert len(chunks) < 8, f"8 个短小节应当被合并，实际切出 {len(chunks)} 块"


def test_packing_does_not_cross_top_level_heading():
    """不跨一级标题合并——两个话题混进一块，检索命中后会答非所问。"""
    md = "### 一、营销管理\n\n营销的说明。\n\n### 二、短信查询\n\n短信的说明。"
    chunks = chunk_markdown(md, size=500, overlap=50)
    assert len(chunks) == 2, "不同一级标题的内容不该合并"
    assert "营销" in chunks[0].content and "短信的说明" in chunks[1].content


def test_ordinal_is_sequential():
    chunks = chunk_markdown(SAMPLE, size=200, overlap=40)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_oversized_single_sentence_is_hard_split():
    """一句话就超长（表格、长列表）时不能死循环，得硬切。"""
    chunks = chunk_markdown("### 表\n\n" + "甲" * 900, size=200, overlap=30)
    assert len(chunks) > 1


def test_heading_only_document_still_produces_a_chunk():
    """内容全在标题里的文档不能整篇丢掉。

    实测语料里有 9 篇这样的文档（「拼多多面单模板设置步骤」等），
    按段落切会得到 0 块、文档凭空消失且不报错——而这类恰恰是
    「XX 怎么设置」的高频问题。
    """
    md = "#### 拼多多模板操作步骤请点击：\n\n#### 拼多多电子面单对接步骤请点击："
    chunks = chunk_markdown(md, size=500, overlap=50)
    assert len(chunks) == 1, "标题型文档应当兜底产出一块"
    assert "拼多多" in chunks[0].content
    assert "电子面单" in chunks[0].content


def test_hard_split_merges_tiny_tail():
    """硬切超长文本时，末尾的零头要并进上一块，别单独成噪声块。

    实测有文档正文里含打印模板 JSON，硬切后剩下 `ext1"}` 这种 6 字残片。
    """
    blob = "### 模板\n\n" + "x" * 205  # 无句号，只能硬切
    chunks = chunk_markdown(blob, size=100, overlap=0)
    assert all(len(c.content) > 25 for c in chunks), (
        f"出现了过短的碎块：{[len(c.content) for c in chunks]}"
    )


# ---------- 标题清洗 ----------


def test_clean_heading_strips_markdown():
    """章节名要拿去显示给用户，残留的 ** 会让引用长成「**2、搜索及扫码**」。"""
    assert clean_heading("**2、搜索及扫码**") == "2、搜索及扫码"
    assert clean_heading("`代码`配置") == "代码配置"
    assert clean_heading("[订单规则](https://x.com/y)") == "订单规则"
    assert clean_heading("  多余   空格  ") == "多余 空格"


def test_headings_in_real_pipeline_are_clean():
    md = "### **一、营销管理**\n\n内容内容内容内容内容内容内容。"
    chunks = chunk_markdown(md, size=500, overlap=50)
    assert chunks[0].heading == "一、营销管理"
    assert "**" not in chunks[0].content.split("\n")[0]


# ---------- frontmatter ----------


def test_parse_frontmatter():
    text = (
        "---\n"
        'title: "客户档案"\n'
        "book_slug: crm\n"
        "source_url: https://www.yuque.com/a/b/c\n"
        "word_count: 540\n"
        "---\n"
        "\n正文开始。"
    )
    meta, body = parse_frontmatter(text)
    assert meta["title"] == "客户档案"
    assert meta["book_slug"] == "crm"
    assert meta["source_url"] == "https://www.yuque.com/a/b/c"
    assert body.strip() == "正文开始。"


def test_parse_frontmatter_absent():
    meta, body = parse_frontmatter("没有 frontmatter 的正文")
    assert meta == {}
    assert body == "没有 frontmatter 的正文"


# ---------- 二进制垃圾过滤（M8 清点索引时发现的） ----------


def test_lakesheet_payload_is_junk():
    """语雀内嵌表格的载荷。M8 清点发现索引里有 700 块这种东西（13%）。"""
    from copilot.ingest.chunker import looks_like_junk

    payload = '{"format":"lakesheet","version":"3.5.5","larkJson":true,"sheet":"x\x9c'
    assert looks_like_junk(payload + "A" * 200)


def test_compressed_garbage_is_junk():
    """压缩数据被当文本读出来的样子：满屏拉丁扩展区字符。"""
    from copilot.ingest.chunker import looks_like_junk

    assert looks_like_junk("'MtÒD'MtÒD'MuÒT'ÍtÒL'­ê¤U´ªVuÒªNZÕI«:iU'­ê¤U´¦ÖtÒNZÓIk:iM" * 3)


def test_real_content_is_not_junk():
    """⭐ 反例最重要。误杀正文是比留着垃圾更严重的错——留着只是浪费，
    误杀是让一整篇文档从知识库里消失，而且不会有任何报错。"""
    from copilot.ingest.chunker import looks_like_junk

    assert not looks_like_junk(
        "一、电子面单设置\n\n先在【设置】-【基本设置】-【物流】里绑定物流账号，"
        "再选择京东标准模板。注意：偏移量设置为上边距 3 毫米。"
    )


def test_mapping_table_with_multiplication_signs_is_not_junk():
    """⭐ `×` 和 `÷`（U+00D7/U+00F7）落在拉丁扩展区里，但真实的对接映射表
    大量用它们标「支持/不支持」。判定必须排除这两个字符，否则整张表被误杀——
    第一版就踩了这个坑，把「授权信息一览表」判成了垃圾。"""
    from copilot.ingest.chunker import looks_like_junk

    assert not looks_like_junk(
        "一、授权信息一览表\n\n| 平台 | 时效 | 抓单 | 发货 | 退款 |\n"
        "| 格格家 | 永久 | × | √ | × |\n| 云集 | 永久 | × | √ | √ |\n"
        "| 小米有品 | 永久 | √ | × | √ |\n| 每日一淘 | 永久 | × | √ | × |"
    )


def test_junk_chunks_never_enter_the_index():
    """端到端：含载荷的文档切出来的块里不该有垃圾。"""
    md = (
        "## 销售排行\n\n这是一段正常的说明文字，讲的是销售排行怎么看。\n\n"
        '{"format":"lakesheet","version":"3.5.5","larkJson":true,"sheet":"' + "Ò¥þ" * 400 + '"}'
    )
    chunks = chunk_markdown(md, size=500, overlap=80)
    assert chunks, "把正文也一起滤掉了"
    assert any("销售排行怎么看" in c.content for c in chunks)
    assert not any("lakesheet" in c.content for c in chunks)
