"""把语雀配图镜像到本地。

**为什么必须镜像，不能直接外链：** 语雀 CDN 有防盗链。实测同一张图——

    curl <图片地址>                                → HTTP 200
    curl -H 'Referer: https://liushun666.cn/' <图> → HTTP 403

浏览器加载 `<img>` 时默认会带 Referer，所以页面上直接写 cdn.nlark.com 的地址，
用户看到的是**满屏裂图**。加 `referrerpolicy="no-referrer"` 眼下能绕过去，
但那是把整站的图押在「语雀不收紧策略」上，而且哪天收紧了是静默故障——
图裂了没有任何报错。落到本地自己发，运行时零外部依赖。

命名按 URL 的 sha256 做内容寻址：同一张图在多篇文档里出现只存一份，
重跑同步时已存在的直接跳过。两级目录打散，避免一个目录塞 3000 个文件。
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from copilot.config import get_settings

# Markdown 图片语法。markdownify 产出的形如 ![](https://...)，也兼容带 alt 和 title 的
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*(\S+?)(?:\s+\"[^\"]*\")?\s*\)")

# 对外的访问前缀。线上 nginx 直接 alias 到 data/images/，不经过 Python
PUBLIC_PREFIX = "/images"


class ImageError(RuntimeError):
    pass


@dataclass
class MirrorStats:
    downloaded: int = 0
    reused: int = 0  # 本地已有，跳过下载
    failed: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)


def image_id(url: str) -> str:
    """按 URL 做内容寻址。16 位十六进制，够 3000 张图不撞。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def marker_id(url: str) -> str:
    """写进正文标记 `[图:xxxx]` 的短 id。

    只取 4 位是因为它要进 chunk 正文、跟着一起做 embedding——越短噪声越小。
    作用域只在一篇文档内部（同一篇里撞 4 位的概率可以忽略），
    真正的定位仍然靠 chunk 自带的 images 列表。
    """
    return image_id(url)[:4]


def _suffix(url: str) -> str:
    """从 URL 取扩展名。取不到或不在白名单就按 png 处理。

    白名单不只是洁癖：文件名直接落盘，放任 URL 里的后缀等于让远端决定
    我们磁盘上出现什么扩展名。
    """
    ext = Path(urlparse(url).path).suffix.lower()
    return ext if ext in get_settings().image_allowed_suffixes else ".png"


def relative_path(url: str) -> str:
    """`ab/abcdef0123456789.png` —— 两级目录打散。"""
    ident = image_id(url)
    return f"{ident[:2]}/{ident}{_suffix(url)}"


def public_url(url: str) -> str:
    """存进数据库的地址，根相对路径。

    前端拼上 API_BASE 再用：开发时打后端 8000，线上同源交给 nginx。
    """
    return f"{PUBLIC_PREFIX}/{relative_path(url)}"


def local_path(url: str, root: Path | None = None) -> Path:
    return (root or get_settings().image_dir) / relative_path(url)


def find_image_urls(markdown: str) -> list[str]:
    """按出现顺序列出正文里的图片地址，去重但保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _MD_IMAGE_RE.finditer(markdown):
        url = m.group(2)
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    return out


class ImageMirror:
    """下载器。带限速、重试、大小上限。

    单独一个 httpx.Client，**不带 Referer**——带了就是 403。
    """

    def __init__(self, root: Path | None = None) -> None:
        s = get_settings()
        self.root = root or s.image_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._min_interval = 1.0 / s.image_rate_limit_per_sec
        self._max_retries = s.image_max_retries
        self._max_bytes = s.image_max_bytes
        self._last_at = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": s.yuque_user_agent},
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ImageMirror:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_at)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.05))
        self._last_at = time.monotonic()

    def fetch(self, url: str, stats: MirrorStats) -> bool:
        """下载一张图。已存在则跳过。返回本地是否可用。"""
        dest = self.root / relative_path(url)
        if dest.exists() and dest.stat().st_size > 0:
            stats.reused += 1
            return True

        dest.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None

        for attempt in range(self._max_retries):
            self._throttle()
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as e:
                last = e
            else:
                if resp.status_code == 200:
                    data = resp.content
                    if len(data) > self._max_bytes:
                        stats.failed += 1
                        stats.errors.append(f"{url} 超过 {self._max_bytes} 字节，跳过")
                        return False
                    # 先写临时文件再改名：中途挂掉不会留下半张图，
                    # 而半张图会因为 exists() 为真被永远当成"已下载"
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    tmp.write_bytes(data)
                    tmp.replace(dest)
                    stats.downloaded += 1
                    stats.bytes += len(data)
                    return True
                # 403 多半是防盗链或图被删了，重试无意义
                if resp.status_code in (401, 403, 404):
                    stats.failed += 1
                    stats.errors.append(f"HTTP {resp.status_code}: {url}")
                    return False
                last = ImageError(f"HTTP {resp.status_code}")
            time.sleep(min(2**attempt + random.uniform(0, 1), 20))

        stats.failed += 1
        stats.errors.append(f"重试 {self._max_retries} 次仍失败: {url} ({last})")
        return False


def rewrite_markdown(
    markdown: str,
    mirror: ImageMirror | None,
    stats: MirrorStats,
) -> str:
    """把正文里的远端图片地址换成本地地址，顺带把图下下来。

    下载失败的那张**整个从正文里去掉**——留一个指向 403 的地址，
    最后只会在页面上显示成裂图。宁可没有图，不要坏掉的图。
    """
    if mirror is None:
        return markdown

    resolved: dict[str, str | None] = {}
    for url in find_image_urls(markdown):
        resolved[url] = public_url(url) if mirror.fetch(url, stats) else None

    def replace(m: re.Match[str]) -> str:
        url = m.group(2)
        if not url.startswith("http"):
            return m.group(0)
        local = resolved.get(url)
        return f"![{m.group(1)}]({local})" if local else ""

    return _MD_IMAGE_RE.sub(replace, markdown)
