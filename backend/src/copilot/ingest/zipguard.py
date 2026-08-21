"""上传解析前的 ZIP 安全检查（M13 P9）。

**为什么 20MB 的上传上限挡不住这件事。**
`.docx` / `.pptx` / `.xlsx` 本质都是 ZIP。一个 20MB 的合法上传，解压出来可以是
几十 GB——压缩炸弹的整个原理就是「进去很小，出来很大」。而这台机器上：

    worker 只有一个进程，`MemoryMax=400M`
    解析是同步的 CPU 活，故意没丢线程池（见 jobs/worker.py 文件头）

也就是说，**一份文件就能把唯一的 worker 拖死**，之后所有人的上传都停在
「解析中」，而页面上不会有任何异常提示。

⭐ **这个检查不解压任何东西。** ZIP 的中央目录里就写着每一条目的
压缩前/压缩后大小，`zipfile.ZipFile.infolist()` 只读那张表。
所以它的代价是几毫秒，而且**先于** python-docx / python-pptx 执行——
等它们开始读的时候，炸弹已经被挡在外面了。

⚠️ **它不是完备的防线，也不假装是。** 中央目录里的大小是文件自己声明的，
一个足够恶意的构造可以谎报。挡住那一类的是另外两道：
    1. `PARSER_TIMEOUT`（见 parsers.py）—— 卡住的解析有个头
    2. systemd 的 `MemoryMax=400M` —— 真吃爆了被收走的是 worker，网站还在
这一道的定位是**最便宜的那一道**：几毫秒，挡掉绝大多数，且永远不会误伤
一份正常的 Office 文档。

─────────────────────────────────────────────────────────
阈值是量出来的，不是拍的

2026-08-21 用 `tests/samples.py` 造的真实 docx / pptx 实测：

    a.docx   17 条目   解压后 827 KB   压缩后 35 KB   整体压缩比 23.7
             最大单条 word/stylesWithEffects.xml 438 KB，单条压缩比 32.2
    a.pptx   40 条目   解压后  97 KB   压缩后 24 KB   整体压缩比  4.1

Office 的正文是 XML，压缩比二三十是常态（**不是可疑信号**）。
真正的压缩炸弹是三位数以上的比例、或者单条几个 G。
所以每条阈值都留了至少 8 倍余量——**宁可漏掉一个刁钻的构造，
也绝不能拦下一份正常的操作手册**：后者会变成用户上传失败，
而他完全不知道为什么。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# 条目数上限。实测 pptx 40 条；一份 500 页、每页配图的大 PPT 大约 2000 条。
# 5000 留了两倍多余量，而压缩炸弹常见的构造是几万到几十万条
MAX_ARCHIVE_ENTRIES = 5000

# 解压后总大小上限。上传上限是 20MB，300MB 相当于允许整体压缩比 15——
# 实测 docx 是 23.7，所以对**满 20MB 的文件**这一条比压缩比那条先触发；
# 而真实的 20MB 文档里大头是图片（图片几乎不压缩），整体比例远低于 15
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024

# 单条目解压后上限。实测最大单条 438KB。80MB 是压倒性的余量，
# 它要挡的是「一条目声明自己有 4GB」那一类
MAX_SINGLE_ENTRY_BYTES = 80 * 1024 * 1024

# 整体压缩比上限。实测 docx 23.7 / pptx 4.1，200 留了 8 倍余量。
# ⚠️ 看**整体**不看单条：Office 里单个 XML 条目压到 1:50 很正常
# （styles.xml 全是重复标签），按单条卡会误伤一大片正常文档
MAX_COMPRESSION_RATIO = 200

# 压缩后总大小低于这个值时不看压缩比。
# ⚠️ **这一条不能省。** 一个几百字节的小文件，压缩比很容易算出上百
# （ZIP 自身的固定开销就占了分母的大半），而它解压出来才几十 KB，
# 一点危险都没有。不设下限的话，最先被拦下的会是最小的那些正常文档
_RATIO_FLOOR_BYTES = 64 * 1024


class UnsafeArchive(Exception):
    """这个压缩包不安全。消息会原样存进 `documents.error` 给用户看，写成人话。"""


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f}MB"


def check_zip_safety(path: Path, kind: str = "Office") -> None:
    """解析 Office 文档之前过一道。不安全就抛 `UnsafeArchive`。

    只读中央目录，不解压、不落盘、不占内存。

    `kind` 是给用户看的文件类型名（"Word" / "PPT"）。
    ⚠️ **这个参数不是装饰。** 这条检查跑在 python-docx / python-pptx **之前**，
    所以从此以后「文件坏了」这句话是由它说的——不带类型名的话，
    用户在知识库页面上看到的就是一句不知道在说哪个文件的错误。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise UnsafeArchive(f"打不开这个 {kind} 文件：它不是有效的压缩包，可能已损坏") from e

    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise UnsafeArchive(
            f"这个 {kind} 文件内部结构异常：包含 {len(infos)} 个条目，"
            f"超过上限 {MAX_ARCHIVE_ENTRIES}"
        )

    total_raw = 0
    total_packed = 0
    for info in infos:
        # ⚠️ 逐条累加**并逐条检查**，不是全部加完再看。
        # 一份声明了几十万条、每条 4GB 的包，累加本身没有代价（读的是元数据），
        # 但早退能让错误信息指向真正越界的那一条
        if info.file_size > MAX_SINGLE_ENTRY_BYTES:
            raise UnsafeArchive(
                f"这个 {kind} 文件内部有一个超大条目（{_mb(info.file_size)}，"
                f"上限 {_mb(MAX_SINGLE_ENTRY_BYTES)}），已拒绝解析"
            )
        total_raw += info.file_size
        total_packed += info.compress_size
        if total_raw > MAX_UNCOMPRESSED_BYTES:
            raise UnsafeArchive(
                f"这个 {kind} 文件解压后过大（超过 {_mb(MAX_UNCOMPRESSED_BYTES)}），"
                "已拒绝解析"
            )

    if total_packed >= _RATIO_FLOOR_BYTES:
        ratio = total_raw / total_packed
        if ratio > MAX_COMPRESSION_RATIO:
            raise UnsafeArchive(
                f"这个 {kind} 文件压缩比异常（{ratio:.0f}:1，"
                f"上限 {MAX_COMPRESSION_RATIO}:1），已拒绝解析"
            )
