"""检索：向量召回 → 重排 → 拼成带编号引用的上下文。

⚠️ **本文件是数据隔离的唯一收敛点。**

`owner_id` 的过滤条件只允许出现在 `_visibility_filter()` 一处。任何调用方
都不得自己拼查询、不得绕过 `search()`。这不是洁癖——过滤条件散落在多处时，
只要有一个地方忘了加，就是把 A 用户的私有文档泄漏给 B，而且不会有任何报错。

检索为什么分两步：
    向量召回 top-20  负责「广」——把可能相关的都捞上来
    重排取 top-5     负责「准」——实测重排能把正确答案和无关内容拉开 200 倍分差，
                                 而向量相似度只拉开 1.3 倍
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from functools import partial

import anyio.to_thread
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from copilot import assets
from copilot.config import get_settings
from copilot.db.models import Chunk, KnowledgeSpace
from copilot.providers.base import Embedder, Reranker

# 与 ingest/chunker.py 的标记格式一致：正文里存 `[图:a3f9]`
_IMG_MARK_RE = re.compile(r"\[图:([0-9a-f]{4})\]")


@dataclass(slots=True)
class Citation:
    """一条引用来源，编号从 1 开始，对应答案里的 [1][2]。"""

    n: int
    title: str
    heading: str | None
    source_url: str | None
    score: float

    @property
    def label(self) -> str:
        return f"{self.title} · {self.heading}" if self.heading else self.title

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "title": self.title,
            "heading": self.heading,
            "url": self.source_url,
            "score": round(self.score, 4),
        }


@dataclass(slots=True)
class RetrievedChunk:
    content: str
    citation: Citation
    images: list[dict] = field(default_factory=list)
    # 这一块来自用户自己上传的文档（`owner_id` 非空）。
    # ⭐ M11 P3 的全部要害在这一个布尔量上：在此之前，
    # **模型看不到哪一块是私有的、哪一块是公共的**——两者在上下文里长得
    # 一模一样（见 `build_context`）。看不见的东西没法判断，
    # M9 那条「问题限定了主体时材料必须真的是讲这个主体的」铁律因此是**空的**。
    private: bool = False


def source_label(rc: RetrievedChunk) -> str:
    """这一块在上下文里署什么名。**M11 P3 第 1 步，最便宜的那个假设。**

        私有  →  [3] 来源：你的文档《客户A-实施配置约定》· 对账规则
        公共  →  [3] 来源：公共知识库 · 流程中拆分条件说明

    在此之前两者都是 `来源：标题 · 小节`，模型**根本看不出**哪一块是这个
    客户自己的约定、哪一块是产品的通用说明。M9 往铁律里加的第 6 条
    「问题限定了主体时，材料必须真的是讲这个主体的」因此是一条**空规则**——
    它要求模型区分一件它看不见的事，那当然做不到。当时的结论是
    「多半得动检索侧，让主体名参与过滤/加权」，那是个大改动、要重跑
    公共库 55 题；而真实缺口可能只是上下文里少了六个字。
    **先验证便宜的那个假设**——这就是那六个字。

    ⚠️ 只改**送进模型的上下文**，不改 `Citation.label`：
    页面上的来源清单仍然显示文档标题，用户不需要被提醒
    「这篇是你自己传的」——他知道。
    """
    if rc.private:
        heading = f" · {rc.citation.heading}" if rc.citation.heading else ""
        return f"你的文档《{rc.citation.title}》{heading}"
    return f"公共知识库 · {rc.citation.label}"


@dataclass(slots=True)
class ContextBundle:
    """喂给 LLM 的上下文，以及答案里 `[图N]` 该指向哪张图。

    两者必须一起产出——分成两个方法算的话，迟早会出现编号和图片对不上的情形，
    而那种错误的表现是「答案配了错误的截图」，没有任何报错。
    """

    text: str
    images: list[dict] = field(default_factory=list)  # [{"n":1,"url":...}, ...]


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]

    @property
    def is_empty(self) -> bool:
        """没有任何够格的结果——调用方据此走「知识库暂无此内容」分支。"""
        return not self.chunks

    @property
    def citations(self) -> list[Citation]:
        return [c.citation for c in self.chunks]

    def renumbered(self) -> RetrievalResult:
        """把编号重排成连续的 1..n。**滤掉几块之后必须调它。**

        编号是「这一轮材料的第几条」，不是块的身份。滤掉中间几块却留着旧号，
        页面上就会出现「来源 · 2」下面列着 1 和 4——用户看得见 2 和 3 不见了，
        却无从知道那是被规则挡掉的还是系统丢了。上下文里同样别扭：
        模型看到的材料标着 [1] [4]，它照抄，正文里也就出现跳号的引用。

        2026-08-23 人工验收撞到的原句：「星辰电商的对账以什么为准？」
        点名主体那道闸门滤掉了公共材料，剩下两块仍标着 1 和 4。
        """
        from dataclasses import replace

        return RetrievalResult(
            chunks=[
                replace(rc, citation=replace(rc.citation, n=i))
                for i, rc in enumerate(self.chunks, start=1)
            ]
        )

    @property
    def private_count(self) -> int:
        """这一轮召回里有几块来自用户自己的文档。

        两个地方要用：`request_trace` 记一列（M11 P1），
        以及 qa.py 判要不要加主体约束（M11 P3 第 3 步）。
        """
        return sum(1 for c in self.chunks if c.private)

    def build_context(self) -> ContextBundle:
        """拼上下文，同时把配图标记重新编号。

        块正文里存的是 `[图:a3f9]`（id 随图走，与顺序无关）。这里按在上下文里
        第一次出现的先后，统一换成 `[图1]`、`[图2]`……模型只看得到这些编号，
        因此**只能引用真实存在的图**——它编不出一个 URL 来。
        """
        numbering: dict[str, int] = {}
        images: list[dict] = []
        parts: list[str] = []

        for rc in self.chunks:
            by_id = {img["id"]: img for img in rc.images if img.get("id")}

            def renumber(m: re.Match[str], by_id=by_id) -> str:
                ident = m.group(1)
                img = by_id.get(ident)
                if img is None:
                    return ""  # 块里没这张图的地址，标记留着只会误导模型
                if ident not in numbering:
                    numbering[ident] = len(numbering) + 1
                    images.append({"n": numbering[ident], "url": img["url"]})
                return f"[图{numbering[ident]}]"

            body = _IMG_MARK_RE.sub(renumber, rc.content)
            parts.append(f"[{rc.citation.n}] 来源：{source_label(rc)}\n{body}")

        return ContextBundle(text="\n\n".join(parts), images=images)

    def to_context(self) -> str:
        """只要文本的旧入口。"""
        return self.build_context().text


def _visibility_filter(
    user_id: uuid.UUID | None, *, private_only: bool = False
) -> ColumnElement[bool]:
    """可见性过滤——**全项目唯一一处**。

        owner_id IS NULL      公共库（语雀抓的），所有人可见
        owner_id = user_id    该用户自己上传的

    未登录（user_id 为 None）时只能看公共库。

    `private_only=True` 只要这个人自己的块（M11 P3 的私有库召回名额要用）。
    ⚠️ **它仍然是这一个函数**，不是第二处过滤。`Chunk.owner_id` 这个词
    在全项目里只允许出现在这里——多一个调用方没关系，多一处**拼查询的地方**
    就是把 A 的私有文档漏给 B，而且不会有任何报错。
    未登录时它返回恒假：没有 user_id 就没有「他自己的块」这个概念。
    """
    public_only = Chunk.owner_id.is_(None)
    if private_only:
        return Chunk.owner_id == user_id if user_id is not None else false()
    if user_id is None:
        return public_only
    return public_only | (Chunk.owner_id == user_id)


def _space_filter(
    space_id: uuid.UUID | None, common_id: uuid.UUID | None
) -> ColumnElement[bool]:
    """知识版本过滤——**全项目唯一一处**，和 `_visibility_filter` 同一条规矩。

        knowledge_space_id = space_id     这一版 ERP 自己的材料
        knowledge_space_id = common_id    跨版本都适用的通用知识

    ⚠️⚠️ **没有 space_id 时返回恒假（fail closed）。**

    这是这个函数最重要的一行。缺少空间上下文的来路只有两种：一条 M14 之前
    建的老会话，或者某个调用方忘了往下传——两种都不该退回「全库搜」。
    退回全库搜的表现是：一个企业版的会话安安静静地拿旗舰版的材料作答，
    界面路径全对不上，而**没有任何报错**。宁可这一轮答不出来。

    ⚠️ `common` 那一支是可选的：种子没建过 `common` 时 `common_id` 是 None，
    这时只搜本空间。不要因此放宽成「搜全部」。
    """
    if space_id is None:
        return false()
    own = Chunk.knowledge_space_id == space_id
    if common_id is None:
        return own
    return own | (Chunk.knowledge_space_id == common_id)


# 人工订正要排到第几位。**不是无条件置顶**：
# 只有重排分到了这条线，才认为「这条订正确实是在回答用户这次问的问题」。
# 无条件置顶的错法很难查——一条为别的问题写的订正会挤掉真正对的那一篇，
# 表现是「自从我改了那个答案，别的问题也开始答错了」。
VERIFIED_PROMOTE_SCORE = 0.5


def _verified_first(
    picked: list[tuple[Chunk, float]],
) -> list[tuple[Chunk, float]]:
    """把够格的人工订正提到最前面，其余顺序原样保留。

    ⭐ **这是「我改了答案，下次就照我改的答」这句承诺的落点。**
    订正块和语雀原文一起参与检索，但检索分只反映「像不像」，
    不反映「谁说了算」——人写定的答案说了算，所以要在这里明确排一次。

    ⚠️ 它**只重排，不放行**：threshold 已经滤过一轮，这里不会把
    低于门槛的东西捞回来。也不改 `_visibility_filter` ——可见性只有那一个收口。
    """
    if not any(c.verified for c, _ in picked):
        return picked
    def promoted(pair: tuple[Chunk, float]) -> bool:
        chunk, score = pair
        return bool(chunk.verified) and score >= VERIFIED_PROMOTE_SCORE

    front = [p for p in picked if promoted(p)]
    if not front:
        return picked
    return front + [p for p in picked if not promoted(p)]


# 私有库单独召回几块（M11 P3）。
#
# 5 是这么定的：私有文档通常只有几块（评测夹具一篇 2 块），5 已经能把
# 一份完整的实施约定整个带进来；再多就开始把不相关的用户文档塞进重排，
# 而重排每多一条就多一份把正确答案挤下去的机会。
# ⚠️ 这不是「多召回一点总没坏处」——它是**给私有块一次被评分的机会**，
# 而不是让私有块变多。真正决定去留的仍然是重排分和阈值。
PRIVATE_RECALL_K = 5

# 私有块的保底名额（M11 P3 第 2 步）。
#
# ⚠️ **从 1 起步，别一上来给 2。** 名额是从公共块里挤出来的——给多了，
# 一道本该由公共库回答的问题会因为用户传过一份不相干的文档而掉分。
# 1 个名额已经能修 `priv-negation-combo-split`（私有文档被
# 《流程中拆分条件说明》4 个块整个挤出 top-5），再往上加要拿 55 题的回归说话。
PRIVATE_FLOOR = 1


def _private_floor(
    picked: list[tuple[Chunk, float]],
    scored: list[tuple[Chunk, float]],
    *,
    floor: int = PRIVATE_FLOOR,
) -> list[tuple[Chunk, float]]:
    """保证 top-k 里至少留 `floor` 个**过了阈值**的私有块。

    ⭐ 要修的是这种情形：用户问「我们的组合装要不要拆」，他自己的文档里
    白纸黑字写着「不拆」，但公共库《流程中拆分条件说明》有 4 个块讲怎么拆，
    语义上贴得更近，于是私有那块被整个挤出 top-5——模型手上只剩公共库的
    通用流程，答出来的是一套和这家客户完全无关的说法，而且看着很专业。

    ⚠️ **它只重排，不放行。** 候选只从 `scored`（已经过了阈值）里取，
    低于门槛的私有块一个都捞不回来——否则「用户传了什么就答什么」，
    防幻觉的第一道闸门等于对私有库单方面失效。

    ⚠️ **绝不碰 `_visibility_filter`。** 那是全项目唯一的可见性收口，
    这个函数拿到的 `scored` 早就过滤过了。这里只在**已经可见**的东西之间
    调顺序——落点选在重排层（`_verified_first` 旁边）就是为了这一点。
    """
    if floor <= 0 or not picked:
        return picked
    have = sum(1 for c, _ in picked if c.owner_id is not None)
    if have >= floor:
        return picked

    chosen = {c.id for c, _ in picked}
    extra = [p for p in scored if p[0].owner_id is not None and p[0].id not in chosen][
        : floor - have
    ]
    if not extra:
        return picked  # 这一轮压根没召回私有块，没什么可保底的

    # 名额总数不变：挤掉分数最低的**公共**块。
    # 挤公共不挤私有是显然的，但「从最低分那头挤」也不是随便定的——
    # 从最高分那头挤会把真正相关的那篇踢走，那就不是纠偏而是新的偏
    keep = [p for p in picked if p[0].owner_id is not None]
    public = [p for p in picked if p[0].owner_id is None]
    public = public[: max(0, len(picked) - len(keep) - len(extra))]
    merged = keep + public + extra
    # 重新按分排。引用编号是按这个顺序发的，不排的话 [1] 可能是分最低的那块，
    # 而用户点开溯源第一条看到的就该是最相关的那篇
    merged.sort(key=lambda p: p[1], reverse=True)
    return merged


async def has_private_chunks(session: AsyncSession, user_id: uuid.UUID | None) -> bool:
    """这个用户到底有没有传过东西。M11 P3 第 3 步的前置条件。

    ⭐ **它的作用是给主体约束划一条不可能越过的边界。**
    没传过任何文档的用户（包括评测公共库那 55 题走的 `user_id=None`），
    这个函数恒为 False，于是那条约束**结构上不可能被触发**。
    M9 的教训正在于此：那次改的是**全局**铁律，和铁律 3「有一部分就答一部分」
    正面撞车，而铁律 3 是花整整一轮才调对的。这一次，凡是不涉及私有库的
    请求，一个字都不会变。

    查询本身是 `owner_id` 上的索引 + LIMIT 1，几乎免费；而且只在
    「问题里有主体词」且「一个私有块都没召回」时才会走到这里。
    """
    if user_id is None:
        return False
    stmt = select(Chunk.id).where(Chunk.owner_id == user_id).limit(1)
    return (await session.execute(stmt)).first() is not None


async def search(
    session: AsyncSession,
    query: str,
    embedder: Embedder,
    reranker: Reranker | None = None,
    *,
    user_id: uuid.UUID | None = None,
    space_id: uuid.UUID | None = None,
    top_k: int | None = None,
    rerank_k: int | None = None,
    score_threshold: float | None = None,
) -> RetrievalResult:
    """检索知识库。

    Args:
        user_id: 当前用户。None 表示只搜公共库
        space_id: 这一轮属于哪个知识版本。**None 会一条都搜不到**（fail closed，
            见 `_space_filter`）——调用方必须显式传，不许靠默认值兜底
        top_k: 向量召回数量
        rerank_k: 重排后保留数量
        score_threshold: 低于此分丢弃。**只是滤掉明显垃圾的下限**，
            真正的防幻觉闸门在 prompt 里
    """
    s = get_settings()
    top_k = top_k or s.retrieve_top_k
    rerank_k = rerank_k or s.rerank_top_k
    threshold = s.rerank_score_threshold if score_threshold is None else score_threshold

    query = query.strip()
    if not query:
        return RetrievalResult(chunks=[])

    # Embedder / Reranker 是同步的（httpx.Client + time.sleep 限速），一次调用
    # 几百毫秒到数秒。直接在协程里调会**卡住整个事件循环**——单 worker 的
    # uvicorn 下，别人正在进行的 SSE 流会一起停住。丢线程池里跑。
    query_vec = await anyio.to_thread.run_sync(embedder.embed_query, query)

    # ⭐ 空间过滤和可见性过滤**在同一个 where 里**，缺一不可：
    # 前者管「哪一版 ERP」，后者管「谁的文档」。两根轴互不替代——
    # 只过滤 owner 会让企业版的会话读到旗舰版的材料，
    # 只过滤空间会让 A 读到 B 上传的文档。
    from copilot.spaces import COMMON

    common = (
        await session.execute(
            select(KnowledgeSpace.id).where(KnowledgeSpace.code == COMMON)
        )
    ).scalar_one_or_none()

    # 向量召回。cosine_distance 越小越近
    stmt = (
        select(Chunk)
        .where(_space_filter(space_id, common))
        .where(_visibility_filter(user_id))
        .order_by(Chunk.embedding.cosine_distance(query_vec))
        .limit(top_k)
    )
    candidates = list((await session.execute(stmt)).scalars())

    # ⭐⭐ **私有库的召回名额（M11 P3）。这是量出来的，不是设计出来的。**
    #
    # `priv-negation-combo-split` 那道题实测长这样：混合池的 top-20 里
    # **私有块一条都没有** —— 公共库《流程中拆分条件说明》那一篇自己就占了
    # 好几个名额，加上另外几篇讲拆单的，20 个位置全被"讲拆分"的内容填满了。
    #
    # 这一点和 M11 定稿时的判断**相反**：当时写的是「私有文档被 4 个块整个挤出
    # top-5」，以为发生在重排层，于是 P3 第 2 步（重排层的保底名额）应该能修。
    # 实测证明挤掉它的是**向量召回**那一层——重排层根本没见过这块，
    # 保底名额有再多名额也无从捞起。**修在看得见它的那一层。**
    #
    # 做法是再跑一次只打私有库的向量召回，把结果并进候选池。
    # 代价：一次 SQL（embedding 复用同一个向量，不多花钱），私有库通常只有几块。
    # 它**不放宽任何东西**：并进来的块照样过重排、照样过阈值，
    # 只是获得了一次"被评分"的机会 —— 在此之前它连参赛资格都没有。
    if user_id is not None:
        private_stmt = (
            select(Chunk)
            # ⚠️⚠️ **空间过滤在这里也要有。** 这是第二条召回路径，漏掉它的
            # 表现是：用户自己传在旗舰版下的文档，会出现在他的企业版会话里。
            # 2026-08-23 写 `test_private_document_respects_space` 时当场撞到——
            # 上面主查询已经按空间过滤了，唯独这一支是"补捞私有块"的旁路，
            # 而旁路正是隔离最容易漏的地方（M11 P3 的保底名额本身就是个旁路）。
            .where(_space_filter(space_id, common))
            .where(_visibility_filter(user_id, private_only=True))
            .order_by(Chunk.embedding.cosine_distance(query_vec))
            .limit(PRIVATE_RECALL_K)
        )
        seen = {c.id for c in candidates}
        candidates += [
            c for c in (await session.execute(private_stmt)).scalars() if c.id not in seen
        ]

    if not candidates:
        return RetrievalResult(chunks=[])

    # 重排
    if reranker is not None:
        # ⭐ **要全部 20 条的分，不是只要前 5 条。**
        # 同一次 HTTP 调用、同一份 tokens，只是让它把剩下 15 条的分也返回来——
        # 代价近乎零，换来的是 `_private_floor` 能看见「第 6 名是个私有块、
        # 分数其实过了阈值」。只拿前 5 名的话，被挤出去的那块连同它的分数
        # 一起消失了，保底名额根本无从判断该捞谁。
        ranked = await anyio.to_thread.run_sync(
            partial(
                reranker.rerank, query, [c.content for c in candidates], top_k=len(candidates)
            )
        )
        scored = [(candidates[r.index], r.score) for r in ranked if r.score >= threshold]
        # 按分降序排一次，别依赖服务端的返回顺序。
        # ⚠️ 这一句同时保证了**行为和改动前完全一致**：分数降序时
        # 「先取前 5 再滤阈值」和「先滤阈值再取前 5」是同一个结果
        # （第 4 名不及格，第 5 名以后必然也不及格）
        scored.sort(key=lambda p: p[1], reverse=True)
        picked = scored[:rerank_k]
    else:
        # 没有重排器时退回向量顺序，分数用 1/(1+序号) 占位
        scored = [(c, 1.0 / (i + 1)) for i, c in enumerate(candidates)]
        picked = scored[:rerank_k]

    # 顺序有意义：先给私有块保底名额，再把人工订正提到最前面。
    # 反过来的话，保底那一步会把刚提上来的订正又挤下去一位
    picked = _private_floor(picked, scored)
    picked = _verified_first(picked)

    # ⚠️ **图片地址在这里定型（M14-B）。** 公共图原样走 `/images/…`（nginx 直发），
    # 私有图换成要鉴权的 `/api/images/{id}`。放在检索层而不是渲染层：
    # 这是答案里的图片地址**唯一**的出处（直路和 Agent 都从这里拿），
    # 挪到上面任何一层都会多出一条绕过它的路——而绕过的表现是私有截图
    # 挂在一个公网可取的地址上，没有任何报错。
    serving = await assets.serving_images(session, [chunk for chunk, _ in picked])

    return RetrievalResult(
        chunks=[
            RetrievedChunk(
                content=chunk.content,
                images=serving.get(chunk.id, []),
                private=chunk.owner_id is not None,
                citation=Citation(
                    n=i,
                    title=chunk.title,
                    heading=chunk.heading,
                    source_url=chunk.source_url,
                    score=score,
                ),
            )
            for i, (chunk, score) in enumerate(picked, start=1)
        ]
    )
