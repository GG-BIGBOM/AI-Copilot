"""私有图片：`GET /api/images/{id}`。**唯一一条要鉴权的图片出口。**

公共图不走这里——它们由 nginx 直接 alias 到 `data/images/`（`/images/…`），
一台 1.6GB 内存的机器不该让 Python 去发静态图。这个接口只为一件事存在：
**用户上传的文档里解出来的图，得有人问一句「这是谁的」再发。**

⚠️ **越权返回 404，不是 403。** 图片 id 是 uuid，这是个能枚举的公网接口；
403 等于告诉对方「这个 id 是真的」，而这里连「存不存在」都是私有信息。
管理接口那边相反（403），理由见 `auth/deps.require_admin`。

⚠️ **不检查知识空间。** 不是漏了：一次图片请求只有一个 id，没有「当前在哪个
空间聊天」这回事——浏览器加载 `<img>` 时不带会话上下文。空间隔离在**检索层**
就已经做完了（`retrieve._space_filter`），一个用户拿不到别的空间的图片地址。
在这里再编一个空间出来判断，判的是假的。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from copilot import assets
from copilot.auth.deps import SessionDep, get_current_user_optional
from copilot.db.models import ImageAsset, User

router = APIRouter(prefix="/api/images", tags=["images"])

OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]

# 一律 404，连措辞都一样：任何区别（「不存在」vs「无权访问」）都是在回答
# 「这个 id 是不是真的」
NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")


@router.get("/{image_id}")
async def get_image(image_id: str, user: OptionalUser, session: SessionDep) -> FileResponse:
    """取一张图。公共图人人可取，私有图只有本人可取。

    id 不是合法 uuid 也走 404，不走 422：一个畸形的 id 和一个不存在的 id
    在调用方看来该是同一件事。
    """
    try:
        ident = uuid.UUID(image_id)
    except ValueError:
        raise NOT_FOUND from None

    asset = await session.get(ImageAsset, ident)
    if asset is None:
        raise NOT_FOUND

    # ⭐ 鉴权就这一句。`owner_id` 是从所属文档冗余下来的，只有
    # `assets.sync_document_assets()` 一处写值（见 db/models.ImageAsset）
    if asset.owner_id is not None and (user is None or asset.owner_id != user.id):
        raise NOT_FOUND

    try:
        path = assets.absolute_path(asset.storage_path)
    except ValueError:
        # 库里的路径跑出了 image_dir。正常情况下不可能——真出现了就是
        # 有别的写入方绕过了 `sync_document_assets`，那更不能照着去读文件
        raise NOT_FOUND from None
    if not path.is_file():
        # 图片文件没了（镜像失败、部署时数据目录没同步）。这不是权限问题，
        # 但对调用方仍然只是「没有这张图」
        raise NOT_FOUND

    private = asset.owner_id is not None
    return FileResponse(
        path,
        media_type=asset.mime_type,
        headers={
            # 私有图不能进任何共享缓存（CDN、反代）——那等于绕过上面那句鉴权
            "Cache-Control": ("private, max-age=86400" if private else "public, max-age=86400"),
            # 内容类型以库里的为准，别让浏览器自己猜
            "X-Content-Type-Options": "nosniff",
        },
    )
