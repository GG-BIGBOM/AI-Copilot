"""邀请码：管理员在网页上自助生成。

原来只能命令行 `copilot invite -n 3` —— 要登服务器。要给同事开账号时
（多半正在客户现场），那条路用不上。

⚠️ **没有「把自己升级成管理员」的接口。** 第一个管理员由命令行指定
（`copilot admin <邮箱>`）。留一个自助升级的口子等于邀请制形同虚设：
任何注册用户都能给自己发无限邀请码，然后这个「内部工具」就对外开放了。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from copilot.api.schemas import InviteCreate, InviteOut
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.auth.invites import count_unused_codes, create_invite_codes, list_unused_codes

router = APIRouter(prefix="/api/invites", tags=["invites"])


def _require_admin(user: CurrentUser) -> None:
    """非管理员一律 403。

    这里用 403 而不是 404：邀请码不是"某个人的资源"，接口存在与否本来就是
    公开信息，藏起来没有意义。和会话/文档那些用 404 的地方不冲突——
    那边藏的是「这个 id 存不存在」，那才是真正会泄漏东西的探针。
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员能管理邀请码")


@router.get("", response_model=InviteOut)
async def list_invites(user: CurrentUser, session: SessionDep) -> InviteOut:
    """还没被用掉的邀请码。"""
    _require_admin(user)
    return InviteOut(
        codes=await list_unused_codes(session, limit=50),
        unused=await count_unused_codes(session),
    )


@router.post("", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invites(
    body: InviteCreate, user: CurrentUser, session: SessionDep
) -> InviteOut:
    """生成 N 个邀请码，连同现存未用的一起返回。"""
    _require_admin(user)
    await create_invite_codes(session, body.count)
    return InviteOut(
        codes=await list_unused_codes(session, limit=50),
        unused=await count_unused_codes(session),
    )
