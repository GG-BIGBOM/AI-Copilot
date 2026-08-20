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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
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
    # 管理员：能生成邀请码。**没有自助升级的路**——第一个管理员由命令行
    # `copilot admin <邮箱>` 指定。留一个网页上的「升级自己」入口，
    # 等于邀请制形同虚设：任何注册用户都能给自己发无限邀请码
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
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
    # 这一块来自人工订正（`VerifiedAnswer`）。检索里靠它把「人写定的标准答案」
    # 排到语雀原文前面——见 `retrieve.py` 的 `_verified_first`。
    # 冗余在这里而不是连 Document 表：检索是热路径，为一个布尔量多一次 join 不值当
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

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


class RequestTrace(Base):
    """一次问答的全链路记录，一条请求一行。M11 P1。

    ⭐ **它存在的理由是「灰度观察」这四个字今天是空的。**
    `.env.example` 里写的观察项是「journal 里 Agent 路径的报错，以及答非所问 /
    该查没查的反馈」——后半句在 journal 里**根本查不到**：没有任何地方记录
    这一轮调了哪些工具、检索到几块、rerank 最高分多少。观察手段不存在的话，
    灰度跑一周得到的只有一句「好像没报错」，那不叫观察，那叫等。

    所以监控是灰度的**硬依赖**，不是配套的锦上添花，它必须先于 P4 上线。

    ⚠️ **👍👎 也写在这张表上，不另建 feedback 表**（M11 P2，这一节唯一一个
    真正的设计决定）。分两张表且不关联的话，一个 👎 就只是个计数器——
    你复现不了当时检索到了什么、调了什么工具、rerank 打了多少分。
    合成一张表，点开一条差评能直接看到全链路，
    「用户差评 → 找失败原因 → 加进评测集」这个闭环才转得起来。
    分表的代价不是多写一次 join，是**这个闭环根本转不动**。

    ⚠️ **写入失败绝不能影响回答。** 见 `api/trace.py`：整条落库包在
    try 里，失败只记一行日志。台账记漏一次的代价，远小于「答案已经生成好了、
    却因为写台账报错而在用户面前变成一句报错」。
    """

    __tablename__ = "request_trace"

    # ⭐ id 在**流开始之前**就生成好，随 `data-trace` 片段发给前端，
    # 行本身到这一轮结束才写。前端点 👎 时手上已经有这个 id 了——
    # 否则前端要等到流结束才知道该给哪一行打分，而用户恰恰是
    # 看到烂答案的第一秒就想点那个按钮。
    id: Mapped[uuid.UUID] = _uuid_pk()
    # journal 里那串 X-Request-Id。用户截图报错时，凭它能把这一行和
    # `journalctl` 里的完整堆栈对上——两边各存一半信息，靠这个字段缝合
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # 人走了台账要留着（SET NULL）：统计和复盘看的是「系统那天表现如何」，
    # 不该因为某个账号被删就凭空少掉一段历史
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 这一行对应的那条回答。
    # ⚠️ **不加外键**：消息删了 trace 要留着（同 user_id 的理由），
    # 而 ondelete=SET NULL 会让「哪条回答被踩了」这个信息也跟着没掉。
    # 代价是这里可能指向一条已经不存在的消息，读的时候当它不存在即可
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # direct | agent | canned —— 这一轮走的哪条路。灰度期间最该看的一列
    route: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(8), default="fast")  # fast | deep
    question: Mapped[str] = mapped_column(Text)
    # Agent 这一轮调过的工具，中文标签：["检索知识库", "收集需求"]。
    # 直路恒为空数组——**空数组和 NULL 在这里意义不同**：
    # 空数组 = 这条路本来就不调工具；NULL = 不知道（老数据）
    tools: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)

    # 检索到几块、rerank 最高分多少。
    # ⭐ 这两列合起来才是「该查没查 / 答非所问」的判据：
    # chunk_count=0 说明压根没召回；top_score 很低说明召回了但都不相关。
    # 只看答案文本是分不出这两种失败的，而它们的修法完全不同
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 检索结果里有没有私有块。M11 P3 的主体纠偏就是奔着这一列去的
    private_hits: Mapped[int] = mapped_column(Integer, default=0)

    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 首字时间。**和总时间必须分开记**：详解档的推理模型首字要 8~60 秒，
    # 而用户感知到的「卡」几乎全在首字之前。只记总时间的话，
    # 「答得慢」和「等得久」这两件事在表里长得一模一样
    ttfb_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    answer_chars: Mapped[int] = mapped_column(Integer, default=0)
    # 模型说了「知识库暂无此内容」。拒答率是幻觉率的对偶指标，
    # 只看幻觉率会把「什么都不敢答」的退化调成满分
    no_answer: Mapped[bool] = mapped_column(Boolean, default=False)

    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ===== 👍👎（M11 P2）=====
    # up | down。NULL = 没人点过——三个字段都可空是刻意的：
    # 绝大多数行永远不会有反馈，默认值不该假装「有一个中性评价」
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    # 点👎时选的原因：wrong | incomplete | should_know | bad_source | unclear | no_image
    feedback_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        # 「最近的差评」是这张表最主要的查法，给它一个专门的索引。
        # 部分索引：99% 的行 feedback 是 NULL，全量索引等于白占空间
        Index(
            "ix_request_trace_feedback_recent",
            "created_at",
            postgresql_where=text("feedback IS NOT NULL"),
        ),
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


class VerifiedAnswer(Base):
    """答案订正：某个问题的**标准答案**，由人写定。

    和 `Correction` 的区别，一句话：

        Correction     改的是「哪一篇文档」    要选文档、重写整篇正文
        VerifiedAnswer 改的是「哪一个问题」    看到答案不对，当场改成对的

    用户真正想做的是后者。为了改一句话去重写一整篇语雀原文，太重了，
    重到没人会用——而没人用的订正功能等于没有。

    ⚠️ **它不是检索之外的另一条路。** 保存时会写成一篇
    `source_type="verified"` 的公共文档 + 若干块，照常向量化、照常参与检索、
    照常被引用（见 `api/routes/verified.py`）。**别为它单开一套召回**：
    单开就意味着两条召回路径、两套 owner_id 隔离规则，而隔离是这个项目里
    唯一一条错了就不可挽回的规则。

    `question` unique：同一个问题只能有一条标准答案。有两条的话，
    检索会随机命中其中一条，而这种错的样子是「答案时好时坏」，最难查。
    """

    __tablename__ = "verified_answers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 作者删号了订正要留着——知识还在生效，不能因为人走了就悄悄失效
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    answer: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
