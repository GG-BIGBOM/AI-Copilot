"""语雀公开知识库抓取。

**不需要 API token**（token 要超级会员）。走的是语雀网页版自己在用的内部
JSON 接口，链路是：

    1. GET /{login}                        → 解析页面里的 appData 拿 group.id
    2. GET /api/groups/{group_id}/books    → 知识库列表
    3. GET /api/catalog_nodes?book_id=     → 某个库的目录树
    4. GET /api/docs/{slug}?book_id=       → 正文（Lake HTML）+ content_updated_at

只有第 1 步依赖 HTML 解析，而且 group_id 拿到后可以缓存——**语雀改版时的脆弱面
只有这一处**。其余三步都是结构化 JSON。

增量判定用 `content_updated_at`（语雀自己给的时间戳），比正文 hash 更准也更省：
不用下载正文就能判断要不要跳过。
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from copilot.config import get_settings

YUQUE_HOST = "https://www.yuque.com"

# 页面里嵌的 JSON：window.appData = JSON.parse(decodeURIComponent("....."))
_APP_DATA_RE = re.compile(r'JSON\.parse\(decodeURIComponent\("(.+?)"\)\)', re.S)


class YuqueError(RuntimeError):
    pass


class YuqueRestricted(YuqueError):
    """文档存在于目录，但匿名访问不到——作者设了权限。

    语雀的表现是：网页版返回 401，内容接口返回 404 并附带 docTitle。
    这不是抓取失败，重试多少次都一样，所以单独归类，别混进错误里
    污染真正需要排查的信号。
    """


@dataclass(slots=True)
class Book:
    """一个知识库。"""

    id: int
    slug: str
    name: str
    items_count: int

    @property
    def url_path(self) -> str:
        return self.slug


@dataclass(slots=True)
class TocNode:
    """目录里的一项。type 为 DOC 的才是文档，TITLE 是分组标题。"""

    type: str
    title: str
    slug: str | None
    doc_id: int | None
    level: int
    parent_uuid: str
    uuid: str


@dataclass(slots=True)
class Doc:
    """一篇文档的完整内容。"""

    id: int
    slug: str
    title: str
    book_slug: str
    book_name: str
    markdown: str
    content_updated_at: str
    word_count: int
    breadcrumb: list[str] = field(default_factory=list)

    @property
    def source_url(self) -> str:
        return f"{YUQUE_HOST}/{self.login}/{self.book_slug}/{self.slug}"

    login: str = ""


def parse_login(url_or_login: str) -> str:
    """从 https://www.yuque.com/wdterpqjb 或裸 login 里取出 login。"""
    s = url_or_login.strip().rstrip("/")
    if s.startswith("http"):
        parts = [p for p in urlparse(s).path.split("/") if p]
        if not parts:
            raise YuqueError(f"URL 里找不到 login: {url_or_login}")
        return parts[0]
    return s


def image_card_src(tag) -> str | None:
    """从图片卡片里取出图片地址。

    语雀的图片不是 `<img>`，而是
        <card type="inline" name="image" value="data:%7B%22src%22%3A%22https...">
    `value` 去掉 `data:` 前缀后是 URL 编码的 JSON，里面有 src/width/height。
    """
    raw = tag.get("value") or ""
    if raw.startswith("data:"):
        raw = raw[5:]
    if not raw:
        return None
    try:
        payload = json.loads(unquote(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    src = payload.get("src")
    return src if isinstance(src, str) and src.startswith("http") else None


def lake_to_markdown(lake_html: str) -> str:
    """语雀 Lake HTML → Markdown。

    Lake 是语雀自家的富文本格式，本质是带一堆 data-lake-* 属性的 HTML。
    直接转 Markdown 能保住标题层级，切分时才有 heading 可用作溯源信息。

    ⚠️ **图片必须在删卡片之前先救出来。** 语雀的配图也是 card
    （`name="image"`），早先一句 `select("card, ...")` 全删，导致 786 篇文档里
    约 3000 张操作截图凭空消失——而 ERP 文档「点哪个按钮」全靠这些图说清楚。
    附件、脑图那些卡片确实转不出文本，照删。
    """
    if not lake_html:
        return ""

    soup = BeautifulSoup(lake_html, "html.parser")

    # 先把图片卡片换成正经的 <img>，markdownify 才会转成 ![](src)
    for card in soup.select('card[name="image"], [data-card-name="image"]'):
        src = image_card_src(card)
        if src:
            card.replace_with(soup.new_tag("img", src=src))
        else:
            card.decompose()

    # 剩下的卡片（附件、脑图、第三方嵌入）转不出有意义的文本，去掉免得留一堆噪声
    for tag in soup.select("card, [data-card-name], script, style"):
        tag.decompose()

    md = markdownify(str(soup), heading_style="ATX", bullets="-")

    # markdownify 会留下大量空行，压成最多两个换行
    md = re.sub(r"\n{3,}", "\n\n", md)
    # 语雀正文里常见的全角空格
    md = md.replace("　", " ")
    return md.strip()


class YuqueClient:
    """带限速与重试的语雀抓取客户端。

    限速是保守的（默认 1.5 req/s）：公开页没有明示配额，官方 API 是 5000 次/小时，
    我们宁可慢一点也别把自己封了——807 篇文档按 1.5 req/s 也就 9 分钟。
    """

    def __init__(
        self,
        rate_limit_per_sec: float | None = None,
        max_retries: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        s = get_settings()
        self.min_interval = 1.0 / (rate_limit_per_sec or s.yuque_rate_limit_per_sec)
        self.max_retries = max_retries or s.yuque_max_retries
        self._last_request_at = 0.0
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent or s.yuque_user_agent,
                "Accept": "application/json, text/html",
            },
            follow_redirects=True,
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> YuqueClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------- 底层 ----------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_interval - elapsed
        if wait > 0:
            # 加抖动，避免固定节奏被识别成机器人
            time.sleep(wait + random.uniform(0, 0.3))
        self._last_request_at = time.monotonic()

    def _get(self, url: str, *, referer: str | None = None) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp
                # 私密文档：目录里有、正文拿不到。重试无意义，也不算错误
                if resp.status_code in (401, 403, 404):
                    raise YuqueRestricted(f"HTTP {resp.status_code}: {url}")
                # 429/5xx 值得重试，其余 4xx 重试也没用
                if resp.status_code != 429 and resp.status_code < 500:
                    raise YuqueError(f"HTTP {resp.status_code}: {url}\n{resp.text[:200]}")
                last_exc = YuqueError(f"HTTP {resp.status_code}: {url}")
            except httpx.HTTPError as e:
                last_exc = e
            # 指数退避
            time.sleep(2**attempt + random.uniform(0, 1))
        raise YuqueError(f"重试 {self.max_retries} 次仍失败: {url}") from last_exc

    def _get_json(self, url: str, *, referer: str | None = None) -> dict:
        resp = self._get(url, referer=referer)
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise YuqueError(f"返回的不是 JSON: {url}\n{resp.text[:200]}") from e

    # ---------- 四步链路 ----------

    def fetch_group_id(self, login: str) -> tuple[int, str]:
        """第 1 步：解析主页 appData 拿 group id 和名称。

        全链路唯一依赖 HTML 结构的地方。语雀改版时先看这里。
        """
        resp = self._get(f"{YUQUE_HOST}/{login}")
        m = _APP_DATA_RE.search(resp.text)
        if not m:
            raise YuqueError(
                f"主页里找不到 appData，语雀可能改版了。检查 {YUQUE_HOST}/{login} 的 HTML 结构"
            )
        data = json.loads(unquote(m.group(1)))
        group = data.get("group") or {}
        gid = group.get("id")
        if not gid:
            raise YuqueError(f"appData 里没有 group.id，{login} 可能不是团队空间")
        return int(gid), group.get("name") or login

    def list_books(self, group_id: int) -> list[Book]:
        """第 2 步：知识库列表。注意接口只认数字 id，传 login 会 422。"""
        data = self._get_json(f"{YUQUE_HOST}/api/groups/{group_id}/books")
        return [
            Book(
                id=b["id"],
                slug=b["slug"],
                name=b.get("name") or b["slug"],
                items_count=b.get("items_count") or 0,
            )
            for b in data.get("data", [])
            if b.get("type") == "Book"
        ]

    def fetch_toc(self, book_id: int) -> list[TocNode]:
        """第 3 步：目录树。"""
        data = self._get_json(f"{YUQUE_HOST}/api/catalog_nodes?book_id={book_id}")
        nodes = []
        for n in data.get("data", []):
            nodes.append(
                TocNode(
                    type=n.get("type") or "",
                    title=n.get("title") or "",
                    slug=n.get("url"),  # 字段名是 url 不是 slug
                    doc_id=n.get("doc_id"),
                    level=n.get("level") or 0,
                    parent_uuid=n.get("parent_uuid") or "",
                    uuid=n.get("uuid") or "",
                )
            )
        return nodes

    def fetch_doc(self, login: str, book: Book, node: TocNode) -> Doc:
        """第 4 步：正文。"""
        if not node.slug:
            raise YuqueError(f"目录项没有 slug，取不了正文: {node.title}")
        url = f"{YUQUE_HOST}/api/docs/{node.slug}?book_id={book.id}&merge_dynamic_data=false"
        data = self._get_json(url, referer=f"{YUQUE_HOST}/{login}/{book.slug}/{node.slug}").get(
            "data", {}
        )
        return Doc(
            id=data.get("id") or node.doc_id or 0,
            slug=node.slug,
            title=data.get("title") or node.title,
            book_slug=book.slug,
            book_name=book.name,
            markdown=lake_to_markdown(data.get("content") or ""),
            content_updated_at=data.get("content_updated_at") or "",
            word_count=data.get("word_count") or 0,
            login=login,
        )


def build_breadcrumbs(nodes: list[TocNode]) -> dict[str, list[str]]:
    """按 uuid 算出每个节点的层级路径，用作 chunk 的 heading 溯源信息。

    例：{"某uuid": ["订单管理", "拼多多对接"]}
    """
    by_uuid = {n.uuid: n for n in nodes if n.uuid}
    crumbs: dict[str, list[str]] = {}
    for node in nodes:
        path: list[str] = []
        cur = node
        seen: set[str] = set()
        while cur.parent_uuid and cur.parent_uuid in by_uuid:
            if cur.parent_uuid in seen:  # 防环
                break
            seen.add(cur.parent_uuid)
            cur = by_uuid[cur.parent_uuid]
            path.insert(0, cur.title)
        crumbs[node.uuid] = path
    return crumbs
