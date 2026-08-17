"""我的文档：上传 / 列表 / 删除。

**上传是这个项目对外暴露的最危险的一个接口**——它让匿名注册来的人往服务器
的磁盘上写文件。所以安全项是逐条对着落的，每条都写清了不做会怎样：

1. **扩展名白名单。** 只认能解析的那几种。不是防病毒（我们不执行这些文件），
   而是防止一堆解析不了的垃圾占着 20MB 的配额。
2. **落盘名用 uuid，原始文件名只进数据库。** 这是防路径穿越的根本手段：
   文件名叫 `../../../../etc/cron.d/x` 也无所谓，因为它压根没被用来拼路径。
   靠「过滤 `..`」那种黑名单迟早漏（`..%2f`、`....//`、UTF-8 变体），
   而这里连过滤都不需要。
3. **大小上限边写边判，不是先读进内存再判。** `await file.read()` 拿一个
   20MB 的文件是 20MB 常驻内存——服务器一共 1.6GB，几个人同时传就没了。
   nginx 那边的 `client_max_body_size 20m` 是第一道闸，但它是可以被绕过的
   （直连 8000 端口），所以应用层必须自己再判一次。
4. **每用户文档数上限。** 没有它，一个注册用户可以慢慢把 40G 磁盘填满。
5. **文档数与配额都按 `owner_id = 当前用户` 算。** 公共库（语雀那 746 篇，
   `owner_id IS NULL`）既不占用户配额，也不能被用户删掉。

删除必须**同时删向量块**。只删 documents 行的话，chunks 还在库里，
那篇「已删除」的文档会继续出现在答案的引用里——用户以为删干净了，其实没有。
（DB 层的 `ondelete CASCADE` 兜着，但这里显式删一遍：这是承诺，不该依赖别处
的配置正确。）
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import delete, desc, func, select

from copilot.api.schemas import DocumentOut, UploadResult
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import Chunk, Document, Job
from copilot.jobs import queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

# 边读边写的块大小。1MB 是「系统调用别太频繁」和「内存别占太多」的折中
_CHUNK_BYTES = 1024 * 1024


def _pick_suffix(filename: str | None) -> str:
    """从原始文件名取出扩展名并校验。

    只取 `Path(...).suffix`，**不用这个名字做任何路径拼接**——见模块 docstring
    第 2 条。`Path("a/../../b.md").suffix` 就是 `.md`，穿越那部分被顺手丢掉了。
    """
    s = get_settings()
    suffix = Path(filename or "").suffix.lower()
    if suffix not in s.upload_allowed_suffixes:
        allowed = "、".join(s.upload_allowed_suffixes)
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"不支持的文件类型{f'（{suffix}）' if suffix else ''}，只收 {allowed}",
        )
    return suffix


def _clean_title(filename: str | None, fallback: str) -> str:
    """用原始文件名当标题（去掉扩展名）。它只用于显示，不碰文件系统。"""
    stem = Path(filename or "").stem.strip()
    return (stem or fallback)[:480]


async def _spool_to_disk(file: UploadFile, dest: Path) -> tuple[int, str]:
    """边收边写，返回 (字节数, sha256)。超限立刻停手并删掉半截文件。

    超限时抛 413。此时 dest 已经写了一部分——**必须删掉**，
    否则一个人反复上传超大文件就能在磁盘上攒下一堆没人认领的碎片。
    """
    limit = get_settings().upload_max_bytes
    digest = hashlib.sha256()
    size = 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while piece := await file.read(_CHUNK_BYTES):
                size += len(piece)
                if size > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        f"文件超过 {limit // 1024 // 1024}MB 上限",
                    )
                digest.update(piece)
                out.write(piece)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件是空的")

    return size, digest.hexdigest()


def _remove_file(stored_path: str | None) -> None:
    """删掉落盘的文件。删不掉只记日志：DB 那边已经删了，接口不该因此报错。"""
    if not stored_path:
        return
    try:
        get_settings().upload_path(stored_path).unlink(missing_ok=True)
    except (OSError, ValueError) as e:
        logger.warning("删除上传文件失败 %s：%s", stored_path, e)


# ---------- 接口 ----------


@router.post("", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile, user: CurrentUser, session: SessionDep
) -> UploadResult:
    """上传一份文档。立刻返回 `pending`，真正的解析由 worker 在后台做。

    **不在请求里解析。** 一份 20MB 的 pptx 解析加向量化要几十秒，
    卡在 HTTP 请求里的话浏览器早超时了，而且那是同步 CPU 活，
    会顶住 API 进程唯一的事件循环——别人的聊天流会一起停住。
    """
    s = get_settings()
    suffix = _pick_suffix(file.filename)

    # 配额只数自己的（公共库 owner_id IS NULL，不算在任何人头上）
    owned = await session.scalar(
        select(func.count(Document.id)).where(Document.owner_id == user.id)
    )
    if (owned or 0) >= s.upload_max_docs_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"文档数已达上限（{s.upload_max_docs_per_user} 份），请先删掉一些",
        )

    # 相对 upload_dir 的路径，库里存的就是这个（见 Settings.upload_path）
    rel = f"{user.id}/{uuid.uuid4().hex}{suffix}"
    size, digest = await _spool_to_disk(file, s.upload_path(rel))

    # 同一个人传同一份文件（手滑双击、或上次失败了重传）
    existing = (
        await session.execute(
            select(Document).where(
                Document.owner_id == user.id, Document.content_hash == digest
            )
        )
    ).scalars().first()

    if existing is not None and existing.status != "failed":
        # 已经有了且没坏，别重复烧一次 embedding 额度
        _remove_file(rel)
        return UploadResult(document=DocumentOut.model_validate(existing), duplicate=True)

    if existing is not None:
        # 上次失败的那篇：换上新文件、清掉错误、重新排队。用同一行，
        # 免得用户的列表里堆着一串同名的失败记录
        _remove_file(existing.stored_path)
        doc = existing
    else:
        doc = Document(owner_id=user.id, source_type="upload")
        session.add(doc)

    doc.title = _clean_title(file.filename, fallback=f"上传文档 {digest[:8]}")
    doc.original_filename = file.filename
    doc.stored_path = rel
    doc.size_bytes = size
    # 上传类文档的 content_hash 是**文件字节**的哈希（语雀那边是正文 Markdown 的）。
    # 两者用途一致：判断「这份东西变了没有」。
    doc.content_hash = digest
    doc.status = "pending"
    doc.error = None
    doc.chunk_count = 0
    await session.flush()

    # ⭐ 文档行和任务行必须一起提交。分开的话，「文档建好了、任务没入队」
    # 就是一篇永远停在「排队中」、永远没人来解析的文档
    await queue.enqueue(session, queue.PARSE_UPLOAD, queue.document_payload(doc.id))
    await session.commit()

    # ⚠️ 必须 refresh。`updated_at` 是 `onupdate=func.now()`——值由数据库算，
    # 提交后这个属性处于过期状态，序列化时一读就触发一次隐式 IO，
    # 在 async 会话里直接抛 MissingGreenlet（复用旧行那条路径才会踩到，
    # 新建行不会——所以这个坑很容易漏过测试）。
    await session.refresh(doc)
    return UploadResult(document=DocumentOut.model_validate(doc), duplicate=False)


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: CurrentUser, session: SessionDep) -> list[Document]:
    """我的文档。**只列自己的**——公共库的 746 篇语雀文档不在这里。"""
    stmt = (
        select(Document)
        .where(Document.owner_id == user.id)
        .order_by(desc(Document.created_at))
        .limit(500)
    )
    return list((await session.execute(stmt)).scalars())


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    """删除自己的文档，连向量块和落盘文件一起。

    别人的文档、公共库的文档一律当**不存在**（404 而不是 403）：
    403 等于告诉对方「这个 id 是真的」，而这是个能枚举 uuid 的公网接口。
    """
    doc = await session.get(Document, document_id)
    if doc is None or doc.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")

    stored_path = doc.stored_path
    # ⭐ 块要显式删。只删 documents 行的话，那篇文档会继续出现在答案的引用里
    await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    # 还没跑的任务一起撤掉：文档都没了，它跑起来只能得出「文档已被删除」。
    # worker 那边扛得住（不会崩），但留着就是让队列攒一堆注定作废的行。
    # ⚠️ 只撤 pending/failed，**不碰 running**——那条正被 worker 拿着，
    # 从它脚下把行删掉会让它提交时炸在一个莫名其妙的地方
    await session.execute(
        delete(Job).where(
            Job.type == queue.PARSE_UPLOAD,
            Job.status.in_(["pending", "failed"]),
            Job.payload["document_id"].astext == str(doc.id),
        )
    )
    await session.execute(delete(Document).where(Document.id == doc.id))
    await session.commit()

    # 文件在**提交之后**才删：反过来的话，一旦提交失败，
    # 库里的记录还在、文件已经没了，那篇文档就永远解析不出来了
    _remove_file(stored_path)
