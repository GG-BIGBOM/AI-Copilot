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
