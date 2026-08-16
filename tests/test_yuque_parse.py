"""语雀解析回归测试。

语雀是别人的网站，随时可能改版。这些测试用固化样本守住解析逻辑：
改版导致解析失效时，测试会立刻指出碎在哪一步，而不是等到同步时
拿到一堆空文档才发现。

样本存在 tests/fixtures/yuque/ 下，由 scripts/refresh_yuque_fixtures.py 更新。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.sources.yuque import (
    Book,
    TocNode,
    YuqueError,
    build_breadcrumbs,
    lake_to_markdown,
    parse_login,
)

FIXTURES = Path(__file__).parent / "fixtures" / "yuque"


def _load(name: str) -> str:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"样本缺失: {path}，跑 scripts/refresh_yuque_fixtures.py 生成")
    return path.read_text(encoding="utf-8")


# ---------- parse_login ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.yuque.com/wdterpqjb", "wdterpqjb"),
        ("https://www.yuque.com/wdterpqjb/", "wdterpqjb"),
        ("https://www.yuque.com/wdterpqjb/crm", "wdterpqjb"),
        ("wdterpqjb", "wdterpqjb"),
        ("  wdterpqjb  ", "wdterpqjb"),
    ],
)
def test_parse_login(raw, expected):
    assert parse_login(raw) == expected


def test_parse_login_rejects_empty_path():
    with pytest.raises(YuqueError):
        parse_login("https://www.yuque.com/")


# ---------- Lake HTML → Markdown ----------


def test_lake_preserves_heading_hierarchy():
    """标题层级必须保住——chunk 的 heading 溯源信息全靠它。"""
    html = "<h3>一、营销管理</h3><p>正文</p><h4>1、营销步骤</h4><p>细节</p>"
    md = lake_to_markdown(html)
    assert "### 一、营销管理" in md
    assert "#### 1、营销步骤" in md


def test_lake_strips_cards_and_scripts():
    """卡片/脚本转不出有意义的文本，留着只会污染检索。"""
    html = (
        "<p>有用的正文</p>"
        '<card data-card-name="yuqueinline">附件占位</card>'
        "<script>alert(1)</script>"
    )
    md = lake_to_markdown(html)
    assert "有用的正文" in md
    assert "附件占位" not in md
    assert "alert" not in md


def test_lake_collapses_blank_lines():
    md = lake_to_markdown("<p>甲</p><br/><br/><br/><p>乙</p>")
    assert "\n\n\n" not in md


def test_lake_normalizes_fullwidth_space():
    assert "　" not in lake_to_markdown("<p>发货　规则</p>")


def test_lake_handles_empty():
    assert lake_to_markdown("") == ""
    assert lake_to_markdown("<p></p>").strip() == ""


def test_lake_real_sample():
    """真实语雀正文样本：转换后必须有实质内容，且不残留 Lake 标记。"""
    md = lake_to_markdown(_load("doc_content.html"))
    assert len(md) > 200, "真实文档转出来不该这么短，解析大概率坏了"
    assert "data-lake-id" not in md
    assert "<!doctype lake>" not in md
    assert md.count("#") > 0, "真实文档应当有标题"


# ---------- 接口返回结构 ----------


def test_books_response_shape():
    """知识库列表的字段没变。少了 slug 或 id，后续全链路都走不下去。"""
    data = json.loads(_load("books.json"))["data"]
    assert data, "样本里没有知识库"
    for b in data:
        assert isinstance(b["id"], int)
        assert b["slug"]
        assert "items_count" in b


def test_toc_uses_url_field_not_slug():
    """目录节点的文档标识字段叫 url 不是 slug——踩过一次，用测试钉住。"""
    data = json.loads(_load("catalog_nodes.json"))["data"]
    docs = [n for n in data if n.get("type") == "DOC"]
    assert docs, "样本目录里没有 DOC 节点"
    assert all("url" in n for n in docs), "DOC 节点应当有 url 字段"


def test_doc_response_has_content_and_timestamp():
    """正文在 content 字段；content_updated_at 是增量判定的依据，不能少。"""
    data = json.loads(_load("doc.json"))["data"]
    assert data.get("content"), "正文应在 content 字段"
    assert data.get("content_updated_at"), "缺 content_updated_at，增量同步会退化成每次全量"


# ---------- 面包屑 ----------


def _node(uuid: str, title: str, parent: str = "") -> TocNode:
    return TocNode(
        type="DOC", title=title, slug=uuid, doc_id=1, level=0, parent_uuid=parent, uuid=uuid
    )


def test_breadcrumb_builds_path():
    nodes = [
        _node("a", "订单管理"),
        _node("b", "平台对接", parent="a"),
        _node("c", "拼多多", parent="b"),
    ]
    crumbs = build_breadcrumbs(nodes)
    assert crumbs["c"] == ["订单管理", "平台对接"]
    assert crumbs["a"] == []


def test_breadcrumb_survives_cycle():
    """脏数据造成的父子环不能让同步整个挂掉。"""
    nodes = [_node("a", "甲", parent="b"), _node("b", "乙", parent="a")]
    crumbs = build_breadcrumbs(nodes)
    assert isinstance(crumbs["a"], list)


def test_book_dataclass():
    b = Book(id=1, slug="crm", name="CRM", items_count=4)
    assert b.url_path == "crm"
