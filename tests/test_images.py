"""语雀配图链路：Lake 卡片 → 本地镜像 → 块标记 → 上下文编号。

这条链路上有两个"静默故障"，都是这里守着的：

  1. **图片被当成卡片一起删掉**——786 篇文档里约 3000 张操作截图凭空消失，
     不报错，只是答案里再也没有截图。
  2. **图号错位**——某段被 min_chars 滤掉后，如果靠"数第几张"来对应，
     后面所有图会整体偏移一张，于是答案配上错误的截图，同样不报错。

不打网络。
"""

from __future__ import annotations

import re

from copilot.ingest.chunker import chunk_markdown, extract_images, images_in
from copilot.retrieve import Citation, RetrievalResult, RetrievedChunk
from copilot.sources.images import (
    _MD_IMAGE_RE,
    find_image_urls,
    image_id,
    marker_id,
    public_url,
    relative_path,
)
from copilot.sources.yuque import image_card_src, lake_to_markdown

CDN = "https://cdn.nlark.com/yuque/0/2022/png/5371448/1653273359030-cea345e2.png"


def image_card(src: str) -> str:
    """造一个语雀的图片卡片。value 是 URL 编码的 JSON，前缀 `data:`。"""
    import json
    import urllib.parse

    payload = urllib.parse.quote(json.dumps({"src": src, "width": 1536, "height": 679}))
    return f'<card type="inline" name="image" value="data:{payload}"></card>'


# ---------- Lake HTML → Markdown ----------


def test_image_card_src_decodes():
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(image_card(CDN), "html.parser").find("card")
    assert image_card_src(tag) == CDN


def test_images_survive_lake_conversion():
    """⭐ 核心回归：图片是 card，早先一句 select('card') 全删，整批截图消失。"""
    html = f"<p>第一步</p>{image_card(CDN)}<p>第二步</p>"
    md = lake_to_markdown(html)
    assert find_image_urls(md) == [CDN], f"图片没救出来：{md!r}"


def test_non_image_cards_still_dropped():
    """附件、脑图这类卡片转不出文本，仍然该删——放行会留一堆噪声。"""
    html = '<p>正文</p><card type="block" name="attachment" value="data:%7B%7D"></card>'
    md = lake_to_markdown(html)
    assert "attachment" not in md and "card" not in md


def test_image_card_without_src_is_dropped():
    """value 解不出 src 的卡片不能留成空 img。"""
    html = '<p>正文</p><card type="inline" name="image" value="data:%7B%7D"></card>'
    assert not find_image_urls(lake_to_markdown(html))


# ---------- 本地镜像的命名 ----------


def test_same_url_maps_to_same_file():
    """内容寻址：同一张图在多篇文档里出现只存一份。"""
    assert relative_path(CDN) == relative_path(CDN)
    assert image_id(CDN) != image_id(CDN + "?x=1")


def test_path_is_two_level_and_public_url_is_root_relative():
    rel = relative_path(CDN)
    assert re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{16}\.png", rel), rel
    assert public_url(CDN) == f"/images/{rel}"


def test_unknown_suffix_falls_back_to_png():
    """扩展名直接落盘，不能让远端 URL 决定我们磁盘上出现什么后缀。"""
    assert relative_path("https://x.com/a/b.exe").endswith(".png")
    assert relative_path("https://x.com/a/b.gif").endswith(".gif")


def test_markdown_image_regex_handles_alt_and_title():
    md = f'![截图]({CDN} "标题")'
    m = _MD_IMAGE_RE.search(md)
    assert m and m.group(2) == CDN


# ---------- 正文标记 ----------


def test_extract_replaces_images_with_short_marker():
    md = f"第一步\n\n![]({CDN})\n\n第二步"
    text, mapping = extract_images(md)
    ident = marker_id(CDN)
    assert f"[图:{ident}]" in text
    assert mapping == {ident: CDN}
    assert CDN not in text, "地址还留在正文里，会白白参与 embedding"


def test_marker_is_short():
    """标记要跟着正文做 embedding，越短噪声越小。"""
    assert len(f"[图:{marker_id(CDN)}]") == 8


def test_local_paths_are_extracted_too():
    """镜像之后正文里是 /images/... 本地路径，不再是 http——也必须能抽出来。"""
    md = "步骤\n\n![](/images/ab/abcdef0123456789.png)"
    text, mapping = extract_images(md)
    assert len(mapping) == 1 and "[图:" in text


# ---------- 切分时图片跟着块走 ----------


def test_chunk_carries_its_images():
    md = f"# 设置步骤\n\n打开设置页面并进入打印配置项。\n\n![]({CDN})\n\n然后保存。"
    chunks = chunk_markdown(md, size=500, overlap=80)
    assert any(c.images for c in chunks), "块没带上图片"
    got = [i["url"] for c in chunks for i in c.images]
    assert got == [CDN]


def test_no_image_is_lost_across_chunks():
    """⭐ 每张图都得落到某个块上。丢了不会报错，只是答案再也配不上图。"""
    body = "这是一段足够长的说明文字，用来把内容撑开以便切成多块。" * 6
    urls = [f"{CDN}?i={i}" for i in range(6)]
    md = "\n\n".join(f"## 第{i}节\n\n{body}\n\n![]({u})" for i, u in enumerate(urls))

    chunks = chunk_markdown(md, size=300, overlap=60)
    landed = {i["url"] for c in chunks for i in c.images}
    assert landed == set(urls), f"丢了：{set(urls) - landed}"


def test_dropped_section_does_not_shift_later_images():
    """⭐ 这条就是「标记自带 id」而不是「数第几张图」的全部理由。

    中间夹一段短到会被 min_chars 滤掉的内容。若靠计数对应，被滤掉那段之后的
    图会整体偏移一张——「丙」的步骤配上「甲」的截图，而且**不会有任何报错**。
    """
    a, b = f"{CDN}?a=1", f"{CDN}?b=2"
    md = (
        f"## 甲\n\n这一节有足够长的正文用来独立成块，说明第一个操作步骤怎么做。\n\n![]({a})\n\n"
        f"## 乙\n\n短\n\n"
        f"## 丙\n\n这一节同样有足够长的正文用来独立成块，说明第二个操作步骤怎么做。\n\n![]({b})"
    )
    chunks = chunk_markdown(md, size=200, overlap=40, min_chars=5)

    # 每块挂的图，必须正文里真有对应标记，且 id 与地址自洽
    for c in chunks:
        for img in c.images:
            assert f"[图:{img['id']}]" in c.content, f"块里挂了正文没有的图：{c.content!r}"
            assert img["id"] == marker_id(img["url"]), "id 与地址对不上"

    # 关键断言：a 只能出现在讲「甲」的块里，b 只能出现在讲「丙」的块里
    for url, own, other in ((a, "甲", "丙"), (b, "丙", "甲")):
        holders = [c for c in chunks if any(i["url"] == url for i in c.images)]
        assert holders, f"{own} 的图丢了"
        for c in holders:
            assert own in c.content, f"{own} 的图跑到了别处：{c.content!r}"
            assert other not in c.content, f"{own} 的图错位到了 {other}：{c.content!r}"


def test_image_only_section_survives_via_its_heading():
    """只有标题加一张截图的小节：不能留下没有文字的空块，也不该整个丢掉。

    `[图:xxxx]` 有 8 个字符，会骗过 min_chars 留下一个永远检索不到的空块；
    但把标题算进去，这一节靠标题仍然搜得到，图也就跟着能显示出来。
    """
    md = f"# 拼多多面单模板设置\n\n![]({CDN})"
    chunks = chunk_markdown(md, size=500, overlap=80)

    assert chunks, "只有标题和截图的小节被整个丢掉了"
    for c in chunks:
        stripped = re.sub(r"\[图:[0-9a-f]{4}\]", "", c.content).strip()
        assert stripped, f"出现了没有任何文字的块：{c.content!r}"
    assert "拼多多面单模板设置" in chunks[0].content
    assert [i["url"] for i in chunks[0].images] == [CDN]


def test_textless_chunk_without_heading_is_dropped():
    """既没标题也没文字，只有图——这种块检索不到，留着纯属噪声。"""
    assert chunk_markdown(f"![]({CDN})", size=500, overlap=80) == []


def test_overlap_does_not_split_a_marker():
    """重叠切点落在标记中间会留下 `图:a3` 这种残字。"""
    body = "这是用来撑长度的说明文字。" * 30
    md = f"# 标题\n\n{body}![]({CDN})\n\n{body}"
    for c in chunk_markdown(md, size=200, overlap=50):
        assert "图:" not in re.sub(r"\[图:[0-9a-f]{4}\]", "", c.content), (
            f"重叠切坏了标记：{c.content!r}"
        )


def test_images_in_dedupes():
    mapping = {"aaaa": "/1.png"}
    assert images_in("[图:aaaa] 文字 [图:aaaa]", mapping) == [{"id": "aaaa", "url": "/1.png"}]


def test_images_in_ignores_unknown_ids():
    assert images_in("[图:ffff]", {"aaaa": "/1.png"}) == []


# ---------- 检索时的全局编号 ----------


def _chunk(n: int, content: str, images: list[dict]) -> RetrievedChunk:
    return RetrievedChunk(
        content=content,
        images=images,
        citation=Citation(n=n, title=f"文档{n}", heading=None, source_url=None, score=1.0),
    )


def test_citations_are_renumbered_after_chunks_are_filtered_out():
    """⭐ 编号是「这一轮材料的第几条」，不是块的身份。

    2026-08-23 人工验收：「星辰电商的对账以什么为准？」——点名主体那道闸门
    滤掉了公共材料，剩下两块仍标着 1 和 4，于是页面上出现「来源 · 2」下面
    列着 1 和 4；送进模型的材料也标着 [1] [4]，模型照抄，正文引用跟着跳号。
    """
    kept = RetrievalResult(chunks=[_chunk(2, "私有一", []), _chunk(4, "私有二", [])])

    renumbered = kept.renumbered()

    assert [c.n for c in renumbered.citations] == [1, 2]
    assert [c.title for c in renumbered.citations] == ["文档2", "文档4"], "只换号，别换内容"
    assert renumbered.build_context().text.startswith("[1] 来源：")
    assert "[2] 来源：" in renumbered.build_context().text
    # 原对象不动：调用方可能还要用它算别的
    assert [c.n for c in kept.citations] == [2, 4]


def test_context_renumbers_markers_globally():
    """块里存的是 [图:a3f9]，送进模型的必须是从 1 开始的连续编号。"""
    result = RetrievalResult(
        chunks=[
            _chunk(1, "步骤一 [图:aaaa] 步骤二 [图:bbbb]", [
                {"id": "aaaa", "url": "/1.png"},
                {"id": "bbbb", "url": "/2.png"},
            ]),
            _chunk(2, "另一篇 [图:cccc]", [{"id": "cccc", "url": "/3.png"}]),
        ]
    )
    bundle = result.build_context()
    assert "[图1]" in bundle.text and "[图2]" in bundle.text and "[图3]" in bundle.text
    assert "[图:aaaa]" not in bundle.text, "原始标记漏给模型了"
    assert bundle.images == [
        {"n": 1, "url": "/1.png"},
        {"n": 2, "url": "/2.png"},
        {"n": 3, "url": "/3.png"},
    ]


def test_same_image_in_two_chunks_keeps_one_number():
    result = RetrievalResult(
        chunks=[
            _chunk(1, "甲 [图:aaaa]", [{"id": "aaaa", "url": "/1.png"}]),
            _chunk(2, "乙 [图:aaaa]", [{"id": "aaaa", "url": "/1.png"}]),
        ]
    )
    bundle = result.build_context()
    assert bundle.images == [{"n": 1, "url": "/1.png"}]
    assert bundle.text.count("[图1]") == 2


def test_marker_without_a_known_url_is_removed():
    """块正文有标记但 images 列里没有对应地址时，标记不能留给模型——
    它会照着引用一个根本不存在的图。"""
    bundle = RetrievalResult(chunks=[_chunk(1, "文字 [图:dead]", [])]).build_context()
    assert "图" not in bundle.text.replace("来源", "")
    assert bundle.images == []


def test_context_without_images_is_unchanged():
    bundle = RetrievalResult(chunks=[_chunk(1, "纯文字答案", [])]).build_context()
    assert "纯文字答案" in bundle.text
    assert bundle.images == []


def test_to_context_still_returns_text():
    """旧入口保持可用。"""
    result = RetrievalResult(chunks=[_chunk(1, "内容 [图:aaaa]", [{"id": "aaaa", "url": "/1.png"}])])
    assert result.to_context() == result.build_context().text
