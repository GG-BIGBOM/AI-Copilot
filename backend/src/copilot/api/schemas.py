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
    # 回答档位：fast 简答（DeepSeek）/ deep 详解（Kimi）。
    # 认不出来的值一律当 fast——老前端不带这个字段，不能因此 422
    mode: Literal["fast", "deep"] = "fast"

    def last_user_text(self) -> str:
        for msg in reversed(self.messages):
            if msg.role == "user" and (t := msg.text()):
                return t
        return ""


class BulkDeleteRequest(BaseModel):
    """批量删除会话。

    上限是防呆用的：正常界面一次最多勾几十条，几千个 id 一次打进来
    多半是脚本写错了，与其让数据库吃一条巨大的 IN，不如直接拒掉。
    """

    ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class BulkDeleteResult(BaseModel):
    """真的删掉了几条。**不逐条汇报**——那会变成探测别人会话是否存在的接口。"""

    deleted: int


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


class DocumentOut(BaseModel):
    """「我的文档」列表里的一行。

    ⚠️ **不含 `stored_path`。** 那是服务器上的落盘路径，对用户毫无用处，
    却会把目录结构和 uuid 命名规则透出去。用户要看的是自己传上来时那个文件名。
    """

    id: uuid.UUID
    title: str
    original_filename: str | None = None
    size_bytes: int | None = None
    status: str  # pending | running | done | failed
    error: str | None = None
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadResult(BaseModel):
    """上传的回执。

    `duplicate` 为真表示这份文件之前就传过了，服务端沿用了原来那一篇——
    前端据此提示「这份文件已经在库里」，而不是让用户对着列表里数不出的
    第二条同名记录发愣。
    """

    document: DocumentOut
    duplicate: bool = False
