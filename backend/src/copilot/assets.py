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
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.config import get_settings
from copilot.db.models import Chunk, Document, ImageAsset
from copilot.sources.images import PUBLIC_PREFIX

logger = logging.getLogger(__name__)

# 私有图的访问前缀。公共图继续走 `PUBLIC_PREFIX`（nginx 直发）
API_PREFIX = "/api/images"

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
    """`/images/ab/xxxx.png` → `ab/xxxx.png`。不是镜像地址就返回 None。

    ⚠️ 只认这一种形状。任何别的东西（外链、绝对路径、`asset://`）都不给
    资产行——**不能凭 Markdown 里写的地址决定这张图能不能看**，
    那是把权限判断交给了正文内容。
    """
    prefix = f"{PUBLIC_PREFIX}/"
    if not url.startswith(prefix):
        return None
    rel = url[len(prefix) :].strip()
    # 库里存的本来就是我们自己按 sha256 生成的两级路径，这里只是不给
    # 任何别的写入方留口子：带 `..`、绝对路径、反斜杠的一律不认
    if not rel or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
        return None
    return rel


def mime_of(storage_path: str) -> str:
    return _MIME_BY_SUFFIX.get(Path(storage_path).suffix.lower(), DEFAULT_MIME)


def absolute_path(storage_path: str) -> Path:
    """还原成磁盘路径，顺手挡一道穿越。

    规矩和 `config.upload_path` 一样：库里存相对路径，取用时校验没有跑出
    `image_dir`。`storage_path` 正常情况下是我们自己生成的，但一个会读文件的
    接口不该假设库里的值一定干净。
    """
    base = get_settings().image_dir.resolve()
    p = (base / storage_path).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"图片路径越界：{storage_path!r}")
    return p


def _probe(storage_path: str) -> tuple[str | None, int | None]:
    """读盘量一下大小和 sha256。文件不在就返回 (None, None)。

    尽力而为：镜像失败、部署时数据目录没同步过来，都不该让入库整个失败——
    资产行的用处是鉴权，而鉴权只依赖 owner，不依赖这两个值。
    """
    try:
        path = absolute_path(storage_path)
        data = path.read_bytes()
    except (OSError, ValueError):
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


async def sync_document_assets(
    session: AsyncSession,
    doc: Document,
    images: list[dict],
) -> int:
    """把一篇文档这一轮引用到的图同步成资产行。返回行数。

    `images` 是这篇文档所有块的 `images` 拼起来（同一张图重复出现没关系）。

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
        row = existing.get(rel)
        if row is None:
            sha, size = _probe(rel)
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
                )
            )
            continue
        # 已有行：跟着文档走一遍（文档可能改过 owner 或空间），
        # 但**不重算 sha256**——文件按内容寻址，路径没变内容就没变
        row.owner_id = doc.owner_id
        row.knowledge_space_id = doc.knowledge_space_id
        row.marker = marker
        row.mime_type = mime_of(rel)
        if row.sha256 is None:
            row.sha256, row.file_size = _probe(rel)

    return len(wanted)


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
