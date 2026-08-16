"""注册 / 登录 / 登出。

注册的两件事必须在同一个事务里：建用户、核销邀请码。
拆开的话，「用户建好了但码没核销」就成了一个可以无限注册的漏洞。
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from copilot.api.schemas import LoginRequest, RegisterRequest, UserOut
from copilot.auth.deps import CurrentUser, SessionDep, clear_auth_cookie, set_auth_cookie
from copilot.auth.invites import redeem_invite_code
from copilot.auth.security import hash_password, verify_password
from copilot.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录失败一律用这一句，不区分「邮箱不存在」和「密码错误」——
# 区分了就等于送人一个用户名枚举接口。
BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确"
)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """邮箱不存在时也拿它比对一次，让两条分支耗时相当。

    否则「不存在」立刻返回、「密码错」要跑一次 bcrypt（几百毫秒），
    响应时间的差异本身就是一个用户名枚举接口。
    """
    return hash_password(secrets.token_urlsafe(16))


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, session: SessionDep) -> User:
    user = User(email=body.email, password_hash=hash_password(body.password))
    session.add(user)
    try:
        # flush 拿到 user.id，但不提交——邀请码核销失败时整个事务一起回滚
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "这个邮箱已经注册过了") from exc

    if not await redeem_invite_code(session, body.invite_code, user.id):
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码无效或已被使用")

    await session.commit()
    set_auth_cookie(response, user)  # 注册完直接是登录态，不用再登一次
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, session: SessionDep) -> User:
    user = (
        await session.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()

    if not verify_password(body.password, user.password_hash if user else _dummy_hash()):
        raise BAD_CREDENTIALS
    if user is None or not user.is_active:
        raise BAD_CREDENTIALS

    set_auth_cookie(response, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout() -> Response:
    """登出。

    JWT 是无状态的，服务端删不掉已经签出去的令牌——这里能做的就是让浏览器
    扔掉 cookie。真要立刻踢人下线，把 `users.is_active` 置 false
    （`get_current_user_optional` 每次请求都会查这个字段）。
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
