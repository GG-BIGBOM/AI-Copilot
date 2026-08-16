"""密码哈希与 JWT 签发/校验。

两个容易踩的坑，这里都提前处理掉了：

1. **bcrypt 只认前 72 字节**，而且 bcrypt>=4.1 遇到超长密码直接抛 ValueError。
   一个 24 个汉字的密码（UTF-8 每字 3 字节）就会炸。所以先用 SHA-256 摘要 +
   base64 压成定长 44 字节 ASCII 再交给 bcrypt——Django 的 `bcrypt_sha256`
   就是这个做法。既没有长度上限，也不会因为前 72 字节相同就把两个不同密码
   判成同一个。

2. **JWT 密钥必须是真随机的**。默认值是占位符，`ensure_production_ready()`
   会在启动时拦下来——线上顶着默认密钥跑，等于任何人都能自己签一个管理员身份。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from copilot.config import get_settings

DEFAULT_JWT_SECRET = "CHANGE-ME-IN-DOTENV"


class AuthError(Exception):
    """令牌无效、过期或者身份对不上。"""


# ---------- 密码 ----------


def _prepare(password: str) -> bytes:
    """把任意长度的密码压成 44 字节 ASCII，绕开 bcrypt 的 72 字节上限。"""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。哈希串本身损坏时返回 False，不抛异常——登录接口不该因为
    数据库里一条脏数据就 500。"""
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------


def create_access_token(user_id: uuid.UUID, expires_minutes: int | None = None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes or s.jwt_expire_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> uuid.UUID:
    """解出用户 id。任何问题（过期、签名不对、sub 不是 uuid）统一抛 AuthError。"""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AuthError(f"令牌无效：{exc}") from exc

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise AuthError("令牌缺少 sub")
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise AuthError("令牌里的 sub 不是合法 uuid") from exc


def ensure_production_ready() -> None:
    """启动自检：顶着占位密钥对外服务 = 任何人都能伪造登录态。"""
    if get_settings().jwt_secret in (DEFAULT_JWT_SECRET, "", "换成随机长字符串"):
        raise RuntimeError(
            "JWT_SECRET 还是占位值。生成一个再写进 backend/.env：\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


# ---------- 邀请码 ----------

# 去掉了 0/O、1/I/L 这些手抄容易看错的字符——邀请码是要用微信发给人念的
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_invite_code(groups: int = 2, group_size: int = 4) -> str:
    """生成形如 `K7QM-3XPD` 的邀请码。"""
    return "-".join(
        "".join(secrets.choice(_ALPHABET) for _ in range(group_size)) for _ in range(groups)
    )


def normalize_invite_code(code: str) -> str:
    """用户输入的邀请码要宽容：大小写、空格、缺不缺连字符都认。"""
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    if not cleaned:
        return ""
    # 按 4 位一组重新加回连字符，和 generate_invite_code 的产物对齐
    return "-".join(cleaned[i : i + 4] for i in range(0, len(cleaned), 4))
