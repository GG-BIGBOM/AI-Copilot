"""图片资产（M14-B）：让每一张图都有一个能查 owner 的行。

这个模块管两件事，两件都只有一句话：

    写   入库时把 `chunks.images` 里出现的图同步成 `image_assets` 行
    读   检索时把**私有**图的地址换成 `/api/images/{id}`，公共图原样不动

⚠️⚠️ **为什么私有图必须换地址。** 块上存的地址是 `/images/ab/xxxx.png`，
线上这个前缀由 nginx 直接 alias 到磁盘目录，**不经过 Python，没有任何鉴权**。
公共的语雀截图这样发本来就对（人人可见），可 M17 一旦从用户上传的文档里
解出嵌图，同一条链路就会把别人的私有截图挂在一个只要猜中哈希就能取的
公网地址上。所以私有图在离开检索层之前就要换成要鉴权的那条路径。

⚠️ **换不成就丢掉，不能原样放行**（见 `serving_images`）。一张私有图
如果没有对应的资产行，"保守"的做法看起来是保留原地址——那恰恰是把它
泄漏出去。没有图只是少一张截图，泄漏是不可挽回的。

**双写期内 `chunks.images` 仍是事实来源**，这张表是它的派生副本：
检索照旧从块上的对照表出发，这里只回答「这张图是谁的」。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.config import get_settings
from copilot.db.models import Chunk, Document, ImageAsset
from copilot.sources.images import PUBLIC_PREFIX

logger = logging.getLogger(__name__)

# 私有图的访问前缀。公共图继续走 `PUBLIC_PREFIX`（nginx 直发）
API_PREFIX = "/api/images"

# ⭐ 上传文档里解出来的图，在 Markdown 里写成 `asset://ab/xxxx.png`（M17）。
# **故意不是一个能直接访问的路径。** 用 `/images/…` 的话，一旦哪天有人把
# 私有图写进了公共目录，它立刻就是公网可取的；而 `asset://` 这个 scheme
# 浏览器根本不认，漏出去的表现是"图裂了"，不是"内容泄漏了"。
# 真正的地址在检索层由 `serving_images()` 换成 `/api/images/{id}`
ASSET_SCHEME = "asset://"

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
DEFAULT_MIME = "application/octet-stream"


def storage_path_of(url: str) -> str | None:
    """把正文里的图片地址还原成磁盘相对路径。认两种形状，别的一律 None：

        /images/ab/xxxx.png    语雀镜像下来的公共图
        asset://ab/xxxx.png    上传文档里解出来的图（M17）

    ⚠️ **地址的形状不决定权限。** 它只回答"这张图在磁盘上叫什么"；
    "谁能看"由 `ImageAsset.owner_id` 说了算（见 `routes/images.py`）。
    凭 Markdown 里写的东西判权限，等于把权限判断交给了正文内容。
    """
    for prefix in (f"{PUBLIC_PREFIX}/", ASSET_SCHEME):
        if url.startswith(prefix):
            rel = url[len(prefix) :].strip()
            break
    else:
        return None
    # 库里存的本来就是我们自己按 sha256 生成的两级路径，这里只是不给
    # 任何别的写入方留口子：带 `..`、绝对路径、反斜杠的一律不认
    if not rel or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
        return None
    return rel


def mime_of(storage_path: str) -> str:
    return _MIME_BY_SUFFIX.get(Path(storage_path).suffix.lower(), DEFAULT_MIME)


def root_for(*, private: bool) -> Path:
    """图片落在哪个根目录。**这一句就是公私隔离的物理落点。**

    公共图在 `data/images/`（nginx 直发），私有图在 `data/private-images/`
    （只有 `/api/images/{id}` 能取）。由 `owner_id` 决定，不由调用方决定——
    调用方能选目录的话，迟早有一处把私有图写进公共目录，而那种错
    没有任何症状（图能显示，正是问题所在）。
    """
    s = get_settings()
    return s.private_image_dir if private else s.image_dir


def absolute_path(storage_path: str, *, private: bool = False) -> Path:
    """还原成磁盘路径，顺手挡一道穿越。

    规矩和 `config.upload_path` 一样：库里存相对路径，取用时校验没有跑出
    根目录。`storage_path` 正常情况下是我们自己生成的，但一个会读文件的
    接口不该假设库里的值一定干净。
    """
    base = root_for(private=private).resolve()
    p = (base / storage_path).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"图片路径越界：{storage_path!r}")
    return p


def _probe(storage_path: str, *, private: bool) -> tuple[str | None, int | None]:
    """读盘量一下大小和 sha256。文件不在就返回 (None, None)。

    尽力而为：镜像失败、部署时数据目录没同步过来，都不该让入库整个失败——
    资产行的用处是鉴权，而鉴权只依赖 owner，不依赖这两个值。
    """
    try:
        path = absolute_path(storage_path, private=private)
        data = path.read_bytes()
    except (OSError, ValueError):
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


def store_bytes(data: bytes, suffix: str, *, private: bool) -> tuple[str, str]:
    """把一张图落盘，返回（磁盘相对路径，正文里该写的地址）。

    **按内容寻址**（sha256 前 16 位 + 两级目录），和语雀镜像同一套规矩：
    同一张图在一篇文档里出现十次只存一份，重传同一份文件也不会翻倍。

    ⚠️ 先写临时文件再改名。中途挂掉会留下半张图，而半张图会因为
    `exists()` 为真被后面每一次都当成"已经存过了"。
    """
    ident = hashlib.sha256(data).hexdigest()[:16]
    ext = suffix.lower() if suffix.lower() in get_settings().image_allowed_suffixes else ".png"
    rel = f"{ident[:2]}/{ident}{ext}"

    dest = absolute_path(rel, private=private)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and dest.stat().st_size > 0):
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    return rel, (f"{ASSET_SCHEME}{rel}" if private else f"{PUBLIC_PREFIX}/{rel}")


@dataclass
class UploadImageSink:
    """解析器往里丢图片，它负责落盘、限流、记归属（M17）。

    ⚠️ **解析器不碰数据库、也不知道图落在哪个目录。** 它只管"这张图属于
    第几页 / 第几张 slide"，剩下的在这里——parsers 因此保持可离线单测，
    而"私有图落进私有目录"这条规则只有这一处实现。

    三道闸门，每一道都写清了不做会怎样：

        张数上限   一份 PPT 能塞几百张图，worker 的 MemoryMax 是 400M
        单张上限   同上，而且 10MB 一张的图在答案里也没人看得动
        太小丢掉   图标、分隔线、logo 收进来只会让答案挂一排装饰性小图
    """

    private: bool
    max_images: int = 0  # 0 = 读配置
    saved: dict[str, dict] = field(default_factory=dict)  # rel -> 归属信息
    skipped: int = 0

    def __post_init__(self) -> None:
        s = get_settings()
        self.max_images = self.max_images or s.upload_max_images_per_doc
        self._min = s.upload_image_min_bytes
        self._max = s.image_max_bytes

    def save(self, data: bytes, suffix: str, **meta) -> str | None:
        """收一张图。返回正文里该写的地址；被闸门挡掉时返回 None。

        **返回 None 时解析器就不要往正文里写这张图**——写一个取不到的地址
        比没有图更糟（页面上是一张裂图，而用户不知道为什么）。
        """
        if len(self.saved) >= self.max_images or not (self._min <= len(data) <= self._max):
            self.skipped += 1
            return None
        try:
            rel, ref = store_bytes(data, suffix, private=self.private)
        except OSError:
            logger.warning("嵌图落盘失败，跳过这一张", exc_info=True)
            self.skipped += 1
            return None
        # 同一张图出现在两处时，保留**第一次**的归属：正文里第一次出现的位置
        # 才是它真正说明的那一段
        self.saved.setdefault(rel, {k: v for k, v in meta.items() if v is not None})
        return ref


# 归属字段。**只有这几个**——`sync_document_assets` 会照单往行上写，
# 多写一个不存在的键会直接炸（而不是被静默忽略）
POSITION_FIELDS = ("page_number", "slide_number", "sheet_name", "anchor")


async def sync_document_assets(
    session: AsyncSession,
    doc: Document,
    images: list[dict],
    positions: dict[str, dict] | None = None,
) -> int:
    """把一篇文档这一轮引用到的图同步成资产行。返回行数。

    `images` 是这篇文档所有块的 `images` 拼起来（同一张图重复出现没关系）。
    `positions` 是「磁盘相对路径 → 归属信息」（第几页 / 第几张 slide /
    哪个工作表），由解析时的 `UploadImageSink` 攒出来（M17）。

    ⚠️ **这是全项目唯一往 `image_assets.owner_id` 写值的地方**，且只能取
    `doc.owner_id`——和 `write_chunks` 写 `chunks.owner_id` 是同一条红线。
    写成别的值不会报错，只会让 `/api/images/` 的越权检查放行。

    重新入库时**要删掉这一轮不再出现的行**：文档改版后图片被换掉，旧行留着
    就是一个仍然可访问的孤儿。不删物理文件——盘上的文件按内容寻址，
    很可能还有别的文档在用（真正的物理清理留到 M17，那时私有图才第一次落盘）。

    不提交事务，交给调用方（和 `write_chunks` 一致）。
    """
    wanted: dict[str, str | None] = {}
    for img in images:
        url = (img or {}).get("url")
        if not isinstance(url, str):
            continue
        rel = storage_path_of(url)
        if rel is None:
            # 只记日志不抛：正文里塞了个外链图不该让整篇文档入不了库，
            # 它的后果只是这张图没有资产行——而没有资产行的私有图会被丢掉
            logger.warning("跳过不认识的图片地址：%s（文档 %s）", url, doc.id)
            continue
        wanted.setdefault(rel, (img or {}).get("id"))

    private = doc.owner_id is not None
    positions = positions or {}
    rows = list(
        (
            await session.execute(select(ImageAsset).where(ImageAsset.document_id == doc.id))
        ).scalars()
    )
    existing = {r.storage_path: r for r in rows}

    stale = [r.id for path, r in existing.items() if path not in wanted]
    if stale:
        await session.execute(delete(ImageAsset).where(ImageAsset.id.in_(stale)))

    for rel, marker in wanted.items():
        where = {k: v for k, v in positions.get(rel, {}).items() if k in POSITION_FIELDS}
        row = existing.get(rel)
        if row is None:
            sha, size = _probe(rel, private=private)
            session.add(
                ImageAsset(
                    document_id=doc.id,
                    owner_id=doc.owner_id,
                    knowledge_space_id=doc.knowledge_space_id,
                    storage_path=rel,
                    marker=marker,
                    mime_type=mime_of(rel),
                    sha256=sha,
                    file_size=size,
                    **where,
                )
            )
            continue
        # 已有行：跟着文档走一遍（文档可能改过 owner 或空间），
        # 但**不重算 sha256**——文件按内容寻址，路径没变内容就没变
        row.owner_id = doc.owner_id
        row.knowledge_space_id = doc.knowledge_space_id
        row.marker = marker
        row.mime_type = mime_of(rel)
        for field_name, value in where.items():
            setattr(row, field_name, value)
        if row.sha256 is None:
            row.sha256, row.file_size = _probe(rel, private=private)

    return len(wanted)


async def document_image_paths(session: AsyncSession, doc: Document) -> list[str]:
    """这篇文档引用了哪些图片文件（磁盘相对路径）。

    ⚠️ **要在删资产行之前调。** 行删了就查不到了——而那种"漏删"的表现是
    磁盘上慢慢攒下一堆谁也引用不到的字节块，没有任何症状。
    """
    return list(
        (
            await session.execute(
                select(ImageAsset.storage_path).where(ImageAsset.document_id == doc.id)
            )
        ).scalars()
    )


async def drop_unreferenced_files(
    session: AsyncSession, paths: list[str], *, private: bool
) -> int:
    """删掉这些路径里**已经没有任何资产行引用**的文件。返回删了几个。

    ⚠️⚠️ **必须在资产行删掉之后调，而且必须数引用。** 图是按内容寻址存的：
    同一张截图出现在两篇文档里，磁盘上只有一份、两行资产。不数引用就删的话，
    删掉 A 文档会把 B 文档的图一起弄没——B 的表现是「图裂了」，
    没有任何报错，也再也恢复不回来（原始文件在用户电脑上）。

    ⚠️ **只管私有图**（`private=True`）。公共目录里的语雀镜像是全站共用的，
    一篇文档删了不代表那张图没人用；它本来也能重新下载（`copilot sync-yuque`）。
    """
    if not paths or not private:
        return 0

    still_used = set(
        (
            await session.execute(
                select(ImageAsset.storage_path).where(ImageAsset.storage_path.in_(paths))
            )
        ).scalars()
    )

    removed = 0
    for rel in set(paths) - still_used:
        try:
            absolute_path(rel, private=True).unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            # 文件删不掉不该让删除接口失败：库里的行已经没了，用户看到的是
            # 「删掉了」，剩下的只是一个谁也引用不到的字节块
            logger.warning("私有图片文件没删掉：%s", rel, exc_info=True)
    return removed


async def serving_images(session: AsyncSession, chunks: list[Chunk]) -> dict[uuid.UUID, list[dict]]:
    """块 id → 这一块的图，私有图的地址已换成 `/api/images/{id}`。

    公共图（`owner_id IS NULL`）原样返回 `/images/…`：它本来就人人可见，
    让 nginx 直接发，别把静态图片流量拉进 Python（服务器一共 1.6GB 内存）。

    私有图查不到资产行就**丢掉这一张**，理由见模块文件头。
    """
    private = {c.document_id for c in chunks if c.owner_id is not None and c.images}
    by_key: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
    if private:
        rows = (
            await session.execute(
                select(ImageAsset.document_id, ImageAsset.storage_path, ImageAsset.id).where(
                    ImageAsset.document_id.in_(private)
                )
            )
        ).all()
        by_key = {(doc_id, path): asset_id for doc_id, path, asset_id in rows}

    out: dict[uuid.UUID, list[dict]] = {}
    for chunk in chunks:
        images = list(chunk.images or [])
        if chunk.owner_id is None or not images:
            out[chunk.id] = images
            continue

        resolved: list[dict] = []
        for img in images:
            rel = storage_path_of(str((img or {}).get("url", "")))
            asset_id = by_key.get((chunk.document_id, rel)) if rel else None
            if asset_id is None:
                logger.warning(
                    "私有图没有资产行，丢弃：%s（块 %s）", (img or {}).get("url"), chunk.id
                )
                continue
            resolved.append({**img, "url": f"{API_PREFIX}/{asset_id}"})
        out[chunk.id] = resolved
    return out
