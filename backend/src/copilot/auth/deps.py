"""FastAPI 依赖：从 cookie 里取出当前用户。

主路径是 **HttpOnly cookie**（浏览器用）；另外认 `Authorization: Bearer`，
只为了 curl / 脚本 / 测试方便。后者不削弱前者——前端始终不碰 token，
localStorage 里没有可偷的东西。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.auth.security import AuthError, create_access_token, decode_access_token
from copilot.config import get_settings
from copilot.db.models import User
from copilot.db.session import get_session

UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def extract_token(request: Request) -> str | None:
    s = get_settings()
    if token := request.cookies.get(s.cookie_name):
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


async def get_current_user_optional(request: Request, session: SessionDep) -> User | None:
    """没登录返回 None，不抛异常。给「登录与否都能访问」的接口用。"""
    token = extract_token(request)
    if not token:
        return None
    try:
        user_id = decode_access_token(token)
    except AuthError:
        return None
    user = await session.get(User, user_id)
    # 用户被删或被停用后，手里的旧 token 立刻失效
    return user if user is not None and user.is_active else None


async def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    """未登录直接 401。"""
    if user is None:
        raise UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    """管理员专用。**服务端唯一的判据是 `users.is_admin`。**

    ⚠️ **前端的 `/admin` guard 只负责体验，不是安全边界。** 它挡的是
    「点进去看到一片报错」，挡不住任何一个会开控制台的人。所有
    `/api/admin/*` 必须在服务端过这一关。

    ⚠️ **不新增 `role` 字段。** 现在 `is_admin` 就是唯一的真源；再加一个
    `role` 意味着两个字段长期并存，而它们迟早会漂移——漂移之后
    「谁是管理员」这个问题有两个互相矛盾的答案，而生效的是哪一个取决于
    这段代码读了哪一个。真要多角色时，单独设计一次性迁移和回滚方案（见 plan.md）。

    停用判定不在这里重复：`get_current_user_optional` 已经卡了 `is_active`，
    被停用的账号连 `CurrentUser` 都拿不到。**两处都写等于两处都要维护**，
    而漏掉一处的表现是「停用了还能用管理台」。

    ⚠️ 越权返回 **403 不是 404**：这条路径上「有没有这个接口」不是秘密，
    藏起来只会让排查变难。私有**数据**的越权才用 404（见 `/api/images/`），
    那里 404 和 403 的区别本身就会泄露「这个 id 存在」。
    """
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]


# ---------- cookie 读写 ----------


def set_auth_cookie(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.cookie_name,
        value=create_access_token(user.id),
        max_age=s.jwt_expire_minutes * 60,
        httponly=True,  # JS 读不到，XSS 也偷不走
        secure=s.cookie_secure,  # 线上 HTTPS 必须 true
        samesite="lax",  # 挡掉跨站表单发起的写请求（CSRF）
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    s = get_settings()
    # 删 cookie 的属性必须和当初 set 的一致，否则浏览器认为是另一个 cookie，删不掉
    response.delete_cookie(
        key=s.cookie_name,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
