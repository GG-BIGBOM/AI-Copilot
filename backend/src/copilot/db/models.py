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


class KnowledgeSpace(Base):
    """知识版本。一个空间 = 一套互不相干的 ERP 知识。

    ⚠️ **这是 M14 起隔离的第二根轴。** 第一根是 `owner_id`（谁的文档），
    这一根是「哪一版 ERP」——旗舰版、客户端企业版、网页版企业版是三套不同的
    产品，同一个问题在三边有三套不同的答案。混在一起答，用户照着点会点不到。

    `code` 是稳定标识，程序里一律用它，不用 id 也不用中文名：
        flagship            旗舰版（现有语雀语料全部属于它）
        enterprise_desktop  客户端企业版
        enterprise_web      网页版企业版
        common              通用知识（跨版本都适用，只作为**检索范围**存在，
                            不是用户能选来聊天的空间）

    `status`：
        active    正常。用户可选（`common` 除外）、可检索、可上传
        inactive  预置但还没导入语料。不出现在用户可选列表里，也不参与检索
        archived  已下线。历史会话仍能读到它的名字，但不再检索、不再可选
    """

    __tablename__ = "knowledge_spaces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ⚠️ 唯一。回填和 `common` 的判定都靠它，重复一个就意味着两套"通用知识"
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    """邀请码。注册必须带，且一次性作废。

    ⚠️⚠️ **「用过没有」的判据是 `used_at`，不是 `used_by`（M13 P8）。**
    `used_by` 是 `ON DELETE SET NULL`，人删号了它会被数据库清空——
    按它判的话，删一个用户就能把他用过的码放回池子里，而邀请制是这个站
    唯一的准入闸门。核销逻辑见 `auth/invites.py` 文件头。
    """

    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 谁用的。**人走了这一列会被清空**，这是刻意的：账号没了，「谁用的」
    # 本来就该跟着消失。要守住的是「这个码不能再用」，那件事由 used_at 守
    used_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # ⭐ 什么时候被消费的。**一旦写上就永不清除**——它是「这个码作废了」的唯一凭据
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL = 公共库；非 NULL = 该用户私有
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 这篇文档属于哪一版 ERP。
    # ⚠️ 先建成可空，回填完再加 NOT NULL——见 alembic 那两个 migration。
    # 建完之后**不允许再出现 NULL**：没有空间的文档在检索里是 fail closed 的，
    # 也就是谁都搜不到它，而那种失败没有任何症状（文档在列表里好好的）。
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
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
    # ⭐ 同上，隔离的第二根轴。**必须等于所属 document 的那一个**——
    # 写成别的值不会报错，只会让这一块出现在错误的 ERP 版本的答案里。
    # 和 owner_id 一样，只允许 `ingest/pipeline.write_chunks` 一处写值。
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
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


class ImageAsset(Base):
    """一篇文档引用的一张配图。**图片的鉴权事实来源（M14-B）。**

    在它之前，图片只以 `chunks.images` / `messages.images` 里的一段 JSON 存在，
    地址是 `/images/ab/xxxx.png`——而那个前缀线上由 nginx 直接发，**不经过
    Python，也就没有任何鉴权**。公共库的语雀截图这样发没问题（本来人人可见），
    但 M17 一旦开始从用户上传的 DOCX/PPTX 里解出嵌图，同一条链路就会把
    别人的私有截图挂在一个只要猜中哈希就能取的公网地址上。

    所以这张表存在的理由只有一个：**让每一张图都有一个能查 owner 的行**，
    私有图走 `GET /api/images/{id}`，由后端逐次校验。

    ⚠️ **它现在是 `chunks.images` 的派生副本，不是替代品。** 双写期内
    正文里的标记、块上的对照表都不变，检索仍然从 `chunks.images` 出发，
    这张表只负责回答「这张图是谁的」。所以 downgrade 直接 drop 是安全的。

    一行 = 一个（文档，磁盘文件）。同一张图出现在两篇文档里就是两行，
    共用同一个 `storage_path`——盘上的文件按 URL 内容寻址，本来就只有一份。
    不能合并成一行：M17 之后两个用户可能上传同一张截图，那时「谁的」
    正是靠所属文档区分的。

    ⚠️ **`owner_id` / `knowledge_space_id` 与 `Chunk` 同理，是从 `Document`
    冗余下来的拷贝**，只允许 `assets.sync_document_assets()` 一处写值，
    且只能取所属文档的那一个。写成别的值不会报错，只会让越权检查放行。

    暂时没有的列：`page_number` / `slide_number` / `sheet_name` / `anchor` /
    `vision_text` / `width` / `height`。路线图里都列了，但今天**没有任何一处
    会往里写值**——解嵌图是 M17 的事。空列会让人以为「解析器忘了填」，
    等 M17 真解出位置信息时和它的写入方同一个 PR 加，理由同「不提前加 `role`」。
    """

    __tablename__ = "image_assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # NULL = 公共库的图（语雀截图），非 NULL = 该用户私有。**鉴权只看这一列。**
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # 相对 `settings.image_dir` 的路径，形如 `ab/abcdef0123456789.png`。
    # ⚠️ 存相对不存绝对，理由同 `documents.stored_path`（见 config.upload_path）：
    # 绝对路径会把开发机的 C:\Users\... 写进一个要跨机器用的库
    storage_path: Mapped[str] = mapped_column(String(512))
    # 正文里 `[图:a3f9]` 的那个短 id。只用来对照排查，不做主键——
    # 它只有 4 位十六进制，作用域仅限一篇文档内部
    marker: Mapped[str | None] = mapped_column(String(8), nullable=True)

    mime_type: Mapped[str] = mapped_column(String(64), default="image/png")
    # 尽力而为：回填那个 migration 不读磁盘（跑 migration 时数据目录可能都不在
    # 同一台机器上），所以历史行是 NULL；此后每次入库由 `assets.py` 补上
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 同一篇文档里同一个文件只该有一行。重新入库靠它做 upsert
        Index("ux_image_assets_document_path", "document_id", "storage_path", unique=True),
    )


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

    # ⭐⭐ 这一轮的答案**是从哪来的**（M13 P5）。
    #     kb                这一句一句都指着材料（正文里有 [n]）
    #     general_knowledge 答了，但一个来源编号都没标 —— M12 放开的那条路
    #     canned            写死的寒暄回复，一次模型调用都没花
    #     tool              Agent 走的是别的工具（出方案、查文档、导出）
    #     no_answer         「知识库暂无此内容」
    #
    # **为什么要单独一列，而不是以后从 `tools` 反推。**
    # M12 把红线从「知识的来源」挪到了「错了会不会伤到人」，于是
    # 「这个答案有没有出处」第一次变成了一件**正常且允许**的事——
    # 而它同时也是最需要盯着的一件事。反推是推不出来的：直路的 `tools`
    # 恒为空数组，Agent 的 `answer_kb` 既可能引材料也可能拒答，
    # 两条路上「常识答的」和「查库答的」在现有每一列上都长得一模一样。
    #
    # 老数据是 NULL —— **不要给它一个默认值**：NULL 表示「那时候还没有这一列」，
    # 填成 'kb' 会让半年前的统计凭空多出一批查库答案
    answer_source: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

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
    # ⭐ 会话创建时**钉死**在一个知识版本上，中途不许换。
    # 换版本 = 新建会话。理由：同一段对话里前三轮讲旗舰版、第四轮改成企业版，
    # 追问「那这个呢」时模型手里是两套互相矛盾的材料，而用户看不出这一点。
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
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

    ⚠️ **M16 起它不再是「谁都能写的东西」。** 在那之前，任何登录用户点
    「答错了，我来改」就能让一条答案对全站立刻生效、无人审核——那是一个
    任何注册用户都能往公共库里塞内容的入口。现在它只能由管理员发布，
    来路是一条走完审核的 `AnswerCorrection`（见下面那个类）。

    唯一键是 **(question, knowledge_space_id)**，不是 question 单独 unique：
    同一个问题在旗舰版和企业版有两套不同的正确答案，这正是知识版本存在的理由。
    """

    __tablename__ = "verified_answers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 作者删号了订正要留着——知识还在生效，不能因为人走了就悄悄失效
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(String(1024), index=True)
    answer: Mapped[str] = mapped_column(Text)

    # 这条标准答案属于哪一版 ERP。**发布时从纠错那边抄过来**，
    # 决定了它进索引时那篇文档落在哪个空间——跨空间命中就是答错产品
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # active | retired。
    # ⚠️ **只有 active 的才有块在索引里**（见 `routes/verified.py` 的 `_sync_index`）。
    # 退役不删行：它得留着，才能回答半年后「当初这条是怎么写的、谁发布的」
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")

    # 来路。发布之后要能一路倒查回「谁在哪一轮问答里提的、谁审的」
    source_correction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answer_corrections.id", ondelete="SET NULL"), nullable=True
    )
    source_trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # 改过几版。每加一版都写一行 `VerifiedAnswerRevision`
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # 被终结命中过几次（`verified.lookup`）。
    # ⚠️ 只统计**直接返回这条答案**的那种命中，不统计「作为材料参与了检索」——
    # 后者每一轮都可能沾边，混在一起这个数就再也说明不了「这条订正有没有用」
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # 同一个空间里同一个问题只能有一条标准答案。有两条的话，检索会随机
        # 命中其中一条，而这种错的样子是「答案时好时坏」，最难查
        Index("ux_verified_question_space", "question", "knowledge_space_id", unique=True),
    )


class VerifiedAnswerRevision(Base):
    """标准答案的每一版。**「修订可追溯」这句话的落点。**

    为什么不靠 `updated_at` 加一句日志：一条标准答案会被改很多次，
    半年后有人问「这一步为什么是这样写的」，需要的不是「最后一次是谁改的」，
    而是**每一次改了什么、谁改的、为什么**。日志会滚掉，这张表不会。

    ⚠️ 写这张表和改 `verified_answers` 必须在**同一个事务**里。
    分开的话会出现「答案已经变了，但没有任何一版记录说它变过」，
    而那种缺口恰恰是在事后追查时才会发现的。
    """

    __tablename__ = "verified_answer_revisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    verified_answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("verified_answers.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(String(1024))
    answer: Mapped[str] = mapped_column(Text)
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    # 谁做的这一版。人删号了也要留着这一版的内容，所以是 SET NULL
    editor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerCorrection(Base):
    """用户提交的答案纠错，**要过审才生效**（M16）。

    ⚠️⚠️ **它和 `Correction` 是两件事，不是同一张表的扩展。**

        Correction        改「哪一篇语雀原文」   立刻重新入库，作者即生效
        AnswerCorrection  改「这一轮的这个答案」 进审核队列，管理员发布才生效

    在 M16 之前，「答错了，我来改」是**提交即公共生效、无人审核**的——
    任何注册用户都能往公共知识库里塞任意内容，而站上没有任何地方看得出来。
    这张表存在的第一目的就是堵掉那个入口。

    ⚠️ **快照字段（`original_*`）写进来之后就不许改。** 审核看的是
    「当时那个答案错在哪」，而原答案所在的 message 随时可能被用户删掉、
    trace 也会被 `prune-traces` 清掉。不存快照的话，审核界面在最需要它的时候
    是空的——而那时已经无从恢复。

    ⚠️ **未审核的纠错一个字都不进 RAG**（路线图第 19 节）。
    「提交后自己先用」听起来友好，实际是让任何用户都能污染自己的检索结果，
    而他分辨不出答案是知识库给的还是自己写的。要做也是以后单独设计
    `personal_draft_override`，不在这一步。
    """

    __tablename__ = "answer_corrections"

    id: Mapped[uuid.UUID] = _uuid_pk()

    # ⚠️ 这三个都**不加外键**：消息可以被用户删、trace 会被清理，
    # 而纠错快照必须活得比它们久（同 `RequestTrace.message_id` 的理由）。
    # 代价是它们可能指向已经不存在的行，读的时候当它不存在即可
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 纠的是哪一版 ERP 的答案。从原会话上抄下来，**发布时直接决定
    # 标准答案落在哪个空间**——抄错了就是把旗舰版的修正发布到企业版
    knowledge_space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # ===== 不可变快照 =====
    original_question: Mapped[str] = mapped_column(Text)
    original_answer: Mapped[str] = mapped_column(Text)
    original_citations: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)
    original_images: Mapped[list | None] = mapped_column(NullableJSONB, nullable=True)

    # ===== 用户写的 =====
    corrected_answer_markdown: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)

    # pending | approved | rejected | withdrawn | published
    # 迁移规则由 `copilot.corrections_flow.STATE_MACHINE` 说了算，不在这里写死
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 乐观锁。两个管理员同时点「通过」和「拒绝」时，后到的那个必须失败，
    # 而不是默默覆盖——审核结论被静默覆盖是查不出来的
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
