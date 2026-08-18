"""勘误层：覆盖语雀原文、作废整篇、以及「勘误悄悄失效」的几种方式。

这套测试盯的主要不是「能不能覆盖」——那条路很短。盯的是**失效时会不会有人知道**：
target_url 抄错、两条勘误撞车、语雀原文后来又更新了。
这三件事的共同点是**页面上完全看不出来**，答案照样有理有据，只是依据是错的。
"""

from __future__ import annotations

import pytest

from copilot.ingest.corrections import (
    CorrectionError,
    apply_corrections,
    load_corrections,
    parse_correction,
    render_correction,
    slugify,
    stale_corrections,
)
from copilot.ingest.pipeline import SourceDoc

URL = "https://www.yuque.com/wdterpqjb/express/jd-mianadan"


def write_correction(dirpath, name, **kw):
    kw.setdefault("target_url", URL)
    kw.setdefault("title", "打单发货 · 京东电子面单")
    kw.setdefault("based_on", "2026-08-12T03:22:00.000Z")
    kw.setdefault("reason", "操作上限原文写 300 单，实际是 500 单")
    kw.setdefault("body", "批量换货一次最多 500 单。")
    path = dirpath / f"{name}.md"
    path.write_text(render_correction(**kw), encoding="utf-8")
    return path


def yuque_doc(body="批量换货一次最多 300 单。", url=URL):
    return SourceDoc(title="打单发货 · 京东电子面单", markdown=body, source_type="yuque", source_url=url)


# ---------- 解析 ----------


def test_roundtrip(tmp_path):
    path = write_correction(tmp_path, "jd")
    c = parse_correction(path)
    assert c.target_url == URL
    assert c.body == "批量换货一次最多 500 单。"
    assert c.reason.startswith("操作上限")
    assert not c.retired


def test_reason_is_required(tmp_path):
    """理由必填。半年后你会需要它——而那时语雀原文早就变了，无从倒推。"""
    path = write_correction(tmp_path, "jd", reason="")
    with pytest.raises(CorrectionError, match="reason"):
        parse_correction(path)


def test_empty_body_is_rejected(tmp_path):
    """正文空 = 把整篇文档清成空白，这多半是手滑而不是本意。
    真要作废有 retired 这条明路。"""
    path = write_correction(tmp_path, "jd", body="")
    with pytest.raises(CorrectionError, match="正文"):
        parse_correction(path)


def test_retired_may_have_empty_body(tmp_path):
    path = write_correction(tmp_path, "jd", body="", retired=True, reason="语雀那边已删除")
    assert parse_correction(path).retired


def test_two_corrections_for_one_doc_is_an_error(tmp_path):
    """撞车必须报错。静默取其一的话，生效的是哪份取决于文件名排序——
    改个文件名就换一份内容，而且没有任何迹象。"""
    write_correction(tmp_path, "a-jd")
    write_correction(tmp_path, "b-jd", body="另一个版本")
    with pytest.raises(CorrectionError, match="同一篇"):
        load_corrections(tmp_path)


def test_missing_dir_is_not_an_error(tmp_path):
    assert load_corrections(tmp_path / "还没建") == {}


# ---------- 覆盖 ----------


def test_correction_replaces_the_body(tmp_path):
    write_correction(tmp_path, "jd")
    docs, applied, missed = apply_corrections([yuque_doc()], load_corrections(tmp_path))
    assert len(docs) == 1
    assert docs[0].markdown == "批量换货一次最多 500 单。"
    assert docs[0].source_url == URL  # 引用还要指回语雀原文
    assert len(applied) == 1 and missed == []


def test_correction_changes_the_content_hash(tmp_path):
    """内容变了 hash 就得变，否则 ingest 会当成「没改」整篇跳过，
    勘误写了也白写。"""
    write_correction(tmp_path, "jd")
    original = yuque_doc()
    docs, _, _ = apply_corrections([original], load_corrections(tmp_path))
    assert docs[0].content_hash != original.content_hash


def test_untouched_docs_pass_through(tmp_path):
    write_correction(tmp_path, "jd")
    other = yuque_doc(url="https://www.yuque.com/wdterpqjb/other/xiazai")
    docs, applied, missed = apply_corrections([yuque_doc(), other], load_corrections(tmp_path))
    assert len(docs) == 2
    assert other in docs
    assert len(applied) == 1


def test_retired_doc_is_dropped_from_ingest(tmp_path):
    write_correction(tmp_path, "jd", body="", retired=True, reason="语雀已删")
    docs, applied, _ = apply_corrections([yuque_doc()], load_corrections(tmp_path))
    assert docs == []
    assert applied[0].retired


def test_wrong_target_url_is_reported_not_swallowed(tmp_path):
    """⭐ 最容易骗到人的失败：url 抄错了。

    没有这个返回值的话，勘误一个字都没生效，而 ingest 输出一切正常——
    你以为改好了，线上还在用错的答案。
    """
    write_correction(tmp_path, "jd", target_url="https://www.yuque.com/wdterpqjb/express/typo")
    docs, applied, missed = apply_corrections([yuque_doc()], load_corrections(tmp_path))
    assert docs[0].markdown == "批量换货一次最多 300 单。"  # 原文没被动
    assert applied == []
    assert len(missed) == 1 and "typo" in missed[0].target_url


# ---------- 过期 ----------


def _manifest(updated_at):
    return {"express/jd": {"source_url": URL, "content_updated_at": updated_at}}


def test_yuque_updated_after_correction_is_stale(tmp_path):
    write_correction(tmp_path, "jd")
    items = load_corrections(tmp_path).values()
    assert stale_corrections(items, _manifest("2026-09-01T00:00:00.000Z"))


def test_untouched_yuque_is_not_stale(tmp_path):
    write_correction(tmp_path, "jd")
    items = load_corrections(tmp_path).values()
    assert stale_corrections(items, _manifest("2026-08-12T03:22:00.000Z")) == []


def test_stale_correction_still_applies(tmp_path):
    """过期只是「要人看一眼」，不是「自动失效」。

    自动失效意味着某天知识库悄悄换回了错的原文，而那正是勘误要解决的问题。
    """
    write_correction(tmp_path, "jd")
    corrections = load_corrections(tmp_path)
    docs, applied, _ = apply_corrections([yuque_doc()], corrections)
    assert docs[0].markdown == "批量换货一次最多 500 单。"
    assert len(applied) == 1


def test_slug_keeps_chinese(tmp_path):
    """文件名要能一眼看出改的是哪篇。拼音化或哈希化都等于让人去 grep 内容。"""
    assert slugify("express-京东电子面单", "x") == "express-京东电子面单"


def test_readme_is_not_a_correction(tmp_path):
    """corrections/README.md 是这一层自己的说明文档。

    不排掉的话它会因为「缺 target_url」让整个 ingest 直接失败——
    而报错信息指向的是一个谁都没打算当勘误的文件。
    """
    (tmp_path / "README.md").write_text("# 勘误层\n\n怎么用……", encoding="utf-8")
    write_correction(tmp_path, "jd")
    assert list(load_corrections(tmp_path)) == [URL]
