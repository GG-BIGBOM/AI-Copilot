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
    # 前端据此决定要不要显示「邀请码」入口。摆一个点了就 403 的菜单项
    # 比不摆更糟——用户会以为自己该有这个权限
    is_admin: bool = False

    model_config = {"from_attributes": True}


class InviteCreate(BaseModel):
    """一次最多 20 个。要更多多点几次——比手滑打成 1000 好"""

    count: int = Field(default=1, ge=1, le=20)


class InviteOut(BaseModel):
    codes: list[str]
    unused: int


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


class CorrectionIn(BaseModel):
    """网页上写的一条勘误。

    `reason` 必填不是形式主义：半年后回来看，没有它就不知道当初为什么改，
    而勘误是**覆盖公共知识库**的东西，说不清理由的覆盖比不覆盖更危险。
    """

    target_url: str = Field(min_length=8, max_length=1024)
    title: str = Field(default="", max_length=512)
    reason: str = Field(min_length=2, max_length=500)
    body: str = Field(min_length=1)

    @field_validator("target_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("要填被勘误那篇文档的完整链接")
        return v


class CorrectionOut(BaseModel):
    id: uuid.UUID
    target_url: str
    title: str
    reason: str
    body: str
    retired: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CorrectionSaved(BaseModel):
    """保存的回执。

    `applied` 是关键字段：勘误落库了不等于生效了（找不到对应的语雀原文、
    或者重新入库挂了）。不把这个区分告诉前端的话，用户改完看到"已保存"，
    再问同一个问题却发现答案没变——他只会认为这个功能是假的。
    """

    correction: CorrectionOut
    applied: bool
    chunks: int
    note: str


# 提问至少要这么长才当得了订正的键。**前后端要一致**——前端靠它在打开
# 对话框那一刻就把话说清楚，后端靠它兜底。
MIN_VERIFIED_QUESTION = 2


class VerifiedIn(BaseModel):
    """一条答案订正：这个问题，以后照这个答。

    没有 `reason`——和勘误层不同，这里改的是**答案本身**，改成什么样一眼就看得见，
    再要一段理由只会让人放弃填写。多一个必填框，就少一半的人会用。
    """

    question: str = Field(max_length=1024)
    answer: str = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def _check_question(cls, v: str) -> str:
        # ⚠️ 报错文案必须是**人话**。这里用 min_length= 的话，用户看到的是
        # Pydantic 的英文默认文案 `String should have at least 2 characters`,
        # 而且是在他写完整段答案、点了保存之后才看到——线上真踩过。
        v = " ".join(v.split())
        if len(v) < MIN_VERIFIED_QUESTION:
            raise ValueError("这句提问太短，当不了订正的依据。换一句问清楚点的再改。")
        return v


class VerifiedOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VerifiedSaved(BaseModel):
    """保存的回执。`applied` 同 `CorrectionSaved`：落库了不等于进索引了。"""

    verified: VerifiedOut
    applied: bool
    note: str
