"""密码哈希、JWT、邀请码的单元测试。不碰数据库，随便跑。"""

from __future__ import annotations

import uuid

import jwt
import pytest

from copilot.auth.security import (
    AuthError,
    create_access_token,
    decode_access_token,
    generate_invite_code,
    hash_password,
    normalize_invite_code,
    verify_password,
)
from copilot.config import get_settings

# ---------- 密码 ----------


def test_hash_is_salted():
    """同一个密码两次哈希结果不同——没加盐的话，撞库一张彩虹表全破。"""
    assert hash_password("hunter2000") != hash_password("hunter2000")


def test_verify_roundtrip():
    h = hash_password("正确的密码123")
    assert verify_password("正确的密码123", h)
    assert not verify_password("错误的密码123", h)


def test_long_chinese_password_works():
    """⚠️ bcrypt 只认前 72 字节，bcrypt>=4.1 遇到超长密码直接抛 ValueError。
    汉字 UTF-8 占 3 字节，25 个汉字就超了——不做 SHA-256 预摘要的话，
    一个用中文长密码的用户会在注册时撞上 500。"""
    pw = "旺店通旗舰版实施顾问的超长中文密码" * 5  # 远超 72 字节
    h = hash_password(pw)
    assert verify_password(pw, h)


def test_passwords_sharing_first_72_bytes_are_distinguished():
    """裸 bcrypt 会把这两个当成同一个密码——预摘要正是为了堵这个洞。"""
    base = "A" * 72
    h = hash_password(base + "尾巴一")
    assert not verify_password(base + "尾巴二", h)


def test_verify_tolerates_corrupt_hash():
    """数据库里一条脏数据不该让登录接口 500。"""
    assert not verify_password("随便", "这不是一个 bcrypt 哈希")


# ---------- JWT ----------


def test_token_roundtrip():
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid)) == uid


def test_expired_token_rejected():
    token = create_access_token(uuid.uuid4(), expires_minutes=-1)
    with pytest.raises(AuthError):
        decode_access_token(token)


def test_token_signed_with_other_secret_rejected():
    """换个密钥签的令牌必须不认——这条不过，JWT_SECRET 就形同虚设。"""
    s = get_settings()
    forged = jwt.encode({"sub": str(uuid.uuid4())}, "别的密钥", algorithm=s.jwt_algorithm)
    with pytest.raises(AuthError):
        decode_access_token(forged)


def test_unsigned_token_rejected():
    """alg=none 的经典攻击：不带签名也想蒙混过关。
    decode 时写死 algorithms=[配置里的算法]，这类令牌进不来。"""
    forged = jwt.encode({"sub": str(uuid.uuid4())}, None, algorithm="none")
    with pytest.raises(AuthError):
        decode_access_token(forged)


def test_garbage_token_rejected():
    with pytest.raises(AuthError):
        decode_access_token("显然不是令牌")


# ---------- 邀请码 ----------


def test_invite_code_avoids_confusable_characters():
    """邀请码是要在微信里发给人手抄的。0/O、1/I/L 必须不出现。"""
    codes = "".join(generate_invite_code() for _ in range(200))
    assert not (set(codes) & set("01OIL")), "出现了容易看错的字符"


def test_invite_code_shape():
    code = generate_invite_code()
    assert len(code) == 9 and code[4] == "-"


def test_invite_codes_are_unique_enough():
    assert len({generate_invite_code() for _ in range(500)}) == 500


def test_normalize_is_forgiving():
    """用户手抄的码：大小写、空格、连字符缺失，都得认。"""
    assert normalize_invite_code("k7qm3xpd") == "K7QM-3XPD"
    assert normalize_invite_code(" K7QM-3XPD ") == "K7QM-3XPD"
    assert normalize_invite_code("K7QM 3XPD") == "K7QM-3XPD"
    assert normalize_invite_code("") == ""
