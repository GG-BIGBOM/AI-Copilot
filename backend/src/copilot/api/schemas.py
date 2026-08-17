"""请求 / 响应模型。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from copilot.config import get_settings

# 故意写得宽松。严格的邮箱正则是个著名的坑（RFC 5322 的完整语法没法用正则表达），
# 而这里是邀请码制的内部工具，真正的把关在邀请码上，不在邮箱格式上。
# 只挡住明显的手滑（漏了 @、域名没有点）。要更严就装 email-validator，
# 但那是给公开注册的站点用的，现在不值这个依赖。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str
    password: str
    invite_code: str = Field(alias="inviteCode")

    model_config = {"populate_by_name": True}

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        v = v.strip().lower()  # 统一小写，避免 A@x.com 和 a@x.com 注册成两个账号
        if not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不对")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        min_len = get_settings().password_min_length
        if len(v) < min_len:
            raise ValueError(f"密码至少 {min_len} 位")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UIMessage(BaseModel):
    """AI SDK 的 UIMessage。

    v5 之后正文在 `parts` 里，但老版本和手写的 curl 用的是 `content`，
    两种都收——服务端宽容一点，省得联调时对着空白页找半天。
    """

    role: Literal["user", "assistant", "system"] = "user"
    parts: list[dict[str, Any]] | None = None
    content: Any = None

    def text(self) -> str:
        if self.parts:
            return "".join(
                p.get("text", "") for p in self.parts if p.get("type") == "text"
            ).strip()
        if isinstance(self.content, str):
            return self.content.strip()
        if isinstance(self.content, list):
            return "".join(
                p.get("text", "")
                for p in self.content
                if isinstance(p, dict) and p.get("type") == "text"
            ).strip()
        return ""


class ChatRequest(BaseModel):
    """`useChat` 默认 POST 上来的 body。

    `id` 是前端生成的会话 id。前端传 UUID 的话我们就沿用它当 conversation id，
    多轮对话自然落到同一条会话里；传别的（useChat 默认是 nanoid）也不报错，
    服务端另发一个，通过 `data-conversation` 片段告诉前端。
    """

    id: str | None = None
    messages: list[UIMessage] = Field(default_factory=list)

    def last_user_text(self) -> str:
        for msg in reversed(self.messages):
            if msg.role == "user" and (t := msg.text()):
                return t
        return ""


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list | None = None
    images: list | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
