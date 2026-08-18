"""数据库模型。

隔离设计的核心在 `owner_id`：
    owner_id IS NULL   -> 公共库（语雀抓来的），所有登录用户可见
    owner_id = <uuid>  -> 私有库（用户上传的），仅本人可见

`Chunk.owner_id` 是从 `Document` 冗余下来的一份拷贝，为的是检索时能直接过滤，
不必 join documents 表。写入时必须与所属文档保持一致（见 ingest/pipeline.py）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from copilot.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim

# ⚠️ 可空的 JSONB 一律用这个，别直接用 JSONB。
#
# SQLAlchemy 默认把 Python 的 None 存成 **JSON 的 null**，而不是 SQL 的 NULL。
# 读回来都是 None，功能上看不出区别——但 SQL 侧就分裂成了两种"空"：
#     WHERE images IS NOT NULL   会把 JSON null 也算进来
#     jsonb_array_length(images) 撞上 JSON null 直接报错
# 排查数据时被这个绊过两次，索引和统计也会因此不准。
NullableJSONB = JSONB(none_as_null=True)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # M8 的成本兜底：每日 token 配额，0 表示不限
    daily_token_quota: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    documents: Mapped[list[Document]] = relationship(back_populates="owner")


class InviteCode(Base):
    """邀请码。注册必须带，且一次性作废。"""

    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    used_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL = 公共库；非 NULL = 该用户私有
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(16))  # yuque | upload
    title: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 上传类文档。stored_path 是 uuid 重命名后的落盘路径——原始文件名只进数据库，
    # 不进文件系统，避免路径穿越。
    stored_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 增量判定：正文 hash 没变就跳过 embedding（省钱的关键）
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    # pending | running | done | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User | None] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # 同一来源同一 owner 只留一份，重跑同步靠它做 upsert
        Index("ix_documents_owner_source", "owner_id", "source_url"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # 从 Document 冗余下来，检索时直接过滤，避免 join
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    ordinal: Mapped[int] = mapped_column(Integer)  # 在文档内的序号
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    # 溯源信息：引用要显示这些
    title: Mapped[str] = mapped_column(String(512))
    heading: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 本块正文里出现的配图：[{"id": "a3f9", "url": "/images/a3/....png"}, ...]
    # `id` 对应正文里的 `[图:a3f9]` 标记。检索时会把这些标记重新编号成
    # `[图1][图2]` 再给模型——它只能引用真实存在的编号，编不出不存在的图。
    images: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="chunks")


class Job(Base):
    """后台任务队列。用 Postgres 的 FOR UPDATE SKIP LOCKED 消费，不引入 Redis。"""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(32))  # parse_upload | sync_yuque
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TokenUsage(Base):
    """每人每天的 token 用量。M8 的成本兜底。

    **为什么单独一张表，而不是从 messages 表反推**：token 主要烧在**送进去的
    上下文**上（5 块材料约 2500 字），答案正文往往只占三成。按答案长度反推
    会低估到没有意义——而这张表的唯一目的就是防止有人（或某个脚本）
    把额度刷穿，估不准就等于没做。

    主键是 (user_id, day)：一人一天一行，用 upsert 累加。
    """

    __tablename__ = "token_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # 存 date 而不是 timestamp：配额是按「天」结算的，存时间点还得每次去截断
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(512), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ===== M7 Agent：多轮收集的状态 =====
    #
    # ⚠️ **必须落库，不能只放在内存里。** 一轮追问跨好几个 HTTP 请求，
    # 每个请求都是一次全新的 Agent run；状态放进程内存的话，用户答完第二个问题，
    # 第一个答案就没了——表现是 Agent 反复问同一件事。
    #
    # profile   已收集的需求（Requirement 的 dict）
    # checklist 生成的配置清单（Checklist 的 dict）
    # export_path 落盘的 xlsx，相对 data/exports/。一个会话一份，够用了，
    #             省掉一张 exports 表和一套额外的权限校验
    profile: Mapped[dict | None] = mapped_column(NullableJSONB, nullable=True)
    checklist: Mapped[dict | None] = mapped_column(NullableJSONB, nullable=True)
    export_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    # 引用来源：[{"n":1,"title":...,"url":...,"heading":...}, ...]
    citations: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)
    # 正文里 [图1][图2] 的对照表：[{"n":1,"url":"/images/..."}, ...]
    # 必须跟着消息一起存——否则刷新页面重新载入历史时，编号还在、图没了，
    # 用户看到的是一串意义不明的 [图1]
    images: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Correction(Base):
    """网页上写的勘误：语雀原文写错了，用这条盖掉它。

    ⚠️ **和 `corrections/*.md` 是同一层的两个来源**，不是两套机制。
    ingest 时两边一起读（见 `ingest/corrections.py` 的 `load_corrections`），
    同一个 target_url 以数据库那条为准——它是你刚在网页上写的那一条。

    为什么要有数据库这一路：文件那一路要有仓库、会跑命令、还得等一次上线，
    实施顾问在客户现场发现原文写错时用不了。而**服务器上的 `corrections/`
    目录每次部署都会被仓库版本整个覆盖**（deploy.sh 第 4 步是 `rm -rf` 再解包），
    所以网页版绝不能往那个目录写文件——写了下次上线就没了，而且没有任何提示。

    `copilot corrections export` 会把这里的记录导成 `corrections/*.md`，
    想让它进版本管理、被 review 时导一次、提交即可。
    """

    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 作者删号了也要留着这条勘误——知识还在生效，不能因为人走了就悄悄失效
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 被勘误的那篇语雀文档，也是和原文对齐的唯一键。
    # unique：两条勘误指向同一篇是配置错误，让数据库来挡，别等 ingest 时才抛
    target_url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    # 为什么改。**必填**，半年后你会需要它
    reason: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # 写这条勘误时语雀那篇的 content_updated_at。语雀后来又更新了就算「过期」
    based_on: Mapped[str] = mapped_column(String(64), default="")
    retired: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
