"""上传解析的 ZIP 安全检查与超时（M13 P9）。

⭐ **这一组题的判据是两条，缺一不可：**

    1. 真正的压缩炸弹要被挡下来
    2. **正常的 Office 文档一份都不能被误伤**

第 2 条比第 1 条更容易出事。阈值拍脑袋定小了，用户传一份普通的操作手册
会得到「文件不安全」——他不知道为什么，也没有别的办法，
而这个功能的全部价值就是让他能自助上传。
所以这里既造炸弹也造真文档，两边都要断言。
"""

from __future__ import annotations

import time
import zipfile

import pytest
from samples import make_docx, make_pptx

from copilot.ingest import zipguard
from copilot.ingest.parsers import ParseError, parse_upload
from copilot.ingest.zipguard import UnsafeArchive, check_zip_safety

# ─────────────────────────────────────────────────────────
# 不能误伤：真实 Office 文档必须全部放行
# ─────────────────────────────────────────────────────────


def test_real_docx_passes(tmp_path):
    check_zip_safety(make_docx(tmp_path / "a.docx"), kind="Word")


def test_real_pptx_passes(tmp_path):
    check_zip_safety(make_pptx(tmp_path / "a.pptx"), kind="PPT")


def test_real_office_files_are_nowhere_near_the_limits(tmp_path):
    """⭐ 不只是「过了」，还要**离阈值很远**。

    刚好压线通过的阈值是不稳的：换一版 Word、多插几张图就翻车，
    而翻车的样子是用户上传失败。这条断言把余量本身钉死——
    以后有人想调小阈值，得先让这条测试同意。
    """
    for path, kind in ((make_docx(tmp_path / "a.docx"), "Word"), (make_pptx(tmp_path / "b.pptx"), "PPT")):
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
        raw = sum(i.file_size for i in infos)
        packed = sum(i.compress_size for i in infos)
        assert len(infos) < zipguard.MAX_ARCHIVE_ENTRIES / 10, f"{kind} 条目数离上限太近"
        assert raw < zipguard.MAX_UNCOMPRESSED_BYTES / 10, f"{kind} 解压后大小离上限太近"
        # ⚠️ Office 的正文是 XML，压缩比二三十是**常态**，不是可疑信号
        assert raw / packed < zipguard.MAX_COMPRESSION_RATIO / 5, f"{kind} 压缩比离上限太近"


# ─────────────────────────────────────────────────────────
# 要挡下来的
# ─────────────────────────────────────────────────────────


def _bomb(path, *, entries: int = 1, size: int = 200 * 1024 * 1024):
    """造一个压缩炸弹：全是 0 的大文件，压完只剩几 KB。

    ⚠️ **写的是真数据，不是伪造的头。** 伪造 `file_size` 也能骗过检查，
    但那测的是「检查会不会读元数据」，不是「炸弹会不会被挡」——
    真数据才和线上遇到的东西同形。
    zlib 对全 0 的压缩比在 1000:1 以上，所以 200MB 只写出几百 KB。
    """
    chunk = b"\0" * (1024 * 1024)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in range(entries):
            with zf.open(f"bomb{n}.bin", "w") as fh:
                for _ in range(size // len(chunk)):
                    fh.write(chunk)
    return path


@pytest.fixture(scope="module")
def bomb(tmp_path_factory):
    """一颗按**生产阈值**造的真炸弹，三个用例共用。

    ⚠️ 造它要写 100MB 的零（压完只剩几百 KB），一次就够慢了——
    每个用例各造一颗会让这个文件跑将近一分钟。
    """
    return _bomb(tmp_path_factory.mktemp("bomb") / "bomb.docx", entries=1, size=100 * 1024 * 1024)


def test_single_huge_entry_is_rejected(bomb):
    """一个条目声明自己有 100MB —— 超过单条上限 80MB。"""
    assert bomb.stat().st_size < 1024 * 1024, "炸弹本身应该很小，否则这个用例没造对"
    with pytest.raises(UnsafeArchive, match="超大条目"):
        check_zip_safety(bomb, kind="Word")


def test_too_many_entries_is_rejected(tmp_path):
    path = tmp_path / "many.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in range(zipguard.MAX_ARCHIVE_ENTRIES + 1):
            zf.writestr(f"f{n}.xml", "x")
    with pytest.raises(UnsafeArchive, match="条目"):
        check_zip_safety(path, kind="Word")


def test_total_uncompressed_size_is_capped(tmp_path, monkeypatch):
    """单条都不超限，加起来超限 —— 分散式的炸弹。

    ⚠️ 这里把两条上限**按比例调小**再测，不是按生产值造 300MB 的零：
    那要写将近一分钟，而这条用例要证的是「逐条累加会触发总量上限」，
    和具体数字无关。生产值本身由
    `test_real_office_files_are_nowhere_near_the_limits` 守着。
    """
    monkeypatch.setattr(zipguard, "MAX_SINGLE_ENTRY_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(zipguard, "MAX_UNCOMPRESSED_BYTES", 20 * 1024 * 1024)
    path = tmp_path / "spread.docx"
    _bomb(path, entries=6, size=4 * 1024 * 1024)  # 单条 4MB < 8MB，合计 24MB > 20MB
    with pytest.raises(UnsafeArchive, match="解压后过大"):
        check_zip_safety(path, kind="Word")


def test_corrupt_zip_names_the_file_type(tmp_path):
    """⚠️ 这条检查跑在 python-docx **之前**，所以「文件坏了」这句话现在由它说。

    不带类型名的话，用户在知识库页面上看到的是一句不知道在说哪个文件的错误。
    """
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip file at all")
    with pytest.raises(UnsafeArchive, match="Word"):
        check_zip_safety(path, kind="Word")


def test_tiny_file_is_not_flagged_on_ratio(tmp_path):
    """⚠️ 小文件的压缩比天然虚高（ZIP 的固定开销占了分母的大半）。

    不设下限的话，最先被拦下的会是最小的那些正常文档。
    """
    path = tmp_path / "tiny.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.xml", "a" * 50_000)  # 压缩比几百，但只有 50KB
    check_zip_safety(path, kind="Word")  # 不该抛


# ─────────────────────────────────────────────────────────
# 接进解析链路：用户看到的是人话，不是堆栈
# ─────────────────────────────────────────────────────────


def test_parse_upload_rejects_bomb_with_readable_message(bomb):
    """⚠️ 错误消息会**原样存进 `documents.error`**，显示在用户的知识库页面上。

    所以它必须是一句中文，不能是 `zipfile.BadZipFile` 或者一段堆栈。
    """
    path = bomb
    with pytest.raises(ParseError) as got:
        parse_upload(path)
    msg = str(got.value)
    assert "Word" in msg and "拒绝解析" in msg
    assert "Traceback" not in msg and "Error" not in msg


def test_bomb_is_rejected_before_any_decompression(bomb):
    """⭐ **检查必须先于解析**，否则炸弹已经在内存里了。

    用时间来证：只读中央目录是毫秒级的，真解压 100MB 不可能这么快。
    """
    path = bomb
    t0 = time.monotonic()
    with pytest.raises(ParseError):
        parse_upload(path)
    assert time.monotonic() - t0 < 2.0, "慢到这个程度，说明它已经开始解压了"


# ─────────────────────────────────────────────────────────
# 解析超时
# ─────────────────────────────────────────────────────────


def test_parser_timeout_gives_a_human_message(tmp_path, monkeypatch):
    """卡住的解析要变成一次**失败**，而不是一直占着唯一的 worker。

    ⚠️ 这一道只把「无限期卡住」降级成「一次失败」——Python 杀不掉线程，
    那条线程还在后台跑。真正兜住资源的是 zipguard（挡在解析之前）
    和 systemd 的 MemoryMax + Restart。见 `_run_with_timeout` 的注释。
    """
    from copilot.ingest import parsers

    def never_returns(_path):
        time.sleep(30)

    monkeypatch.setitem(parsers.PARSERS, ".md", never_returns)
    monkeypatch.setattr(parsers, "PARSER_TIMEOUT", 0.3)

    path = tmp_path / "slow.md"
    path.write_text("# hi", encoding="utf-8")
    t0 = time.monotonic()
    with pytest.raises(ParseError, match="解析时间过长"):
        parse_upload(path)
    assert time.monotonic() - t0 < 5.0, "超时没生效，一直等到解析器自己结束"


def test_vision_path_is_not_subject_to_the_cpu_timeout(tmp_path, monkeypatch):
    """⚠️ 视觉那条路是网络调用，**不能套本机 CPU 的超时口径**。

    一份 20 页的扫描件要逐页发给 Kimi，一页几秒，合法文件也能超过 120 秒。
    卡它只会把「正常但慢」判成「文件损坏」。
    """
    from copilot.ingest import parsers

    seen = []

    def slow_vision(path, vision=None):
        time.sleep(0.6)
        seen.append(path)
        return parsers.ParsedUpload(markdown="# 读出来了")

    monkeypatch.setitem(parsers.VISION_PARSERS, ".png", slow_vision)
    monkeypatch.setattr(parsers, "PARSER_TIMEOUT", 0.1)

    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert parse_upload(path, vision=object()).markdown == "# 读出来了"
    assert seen, "视觉解析器压根没被调到"
