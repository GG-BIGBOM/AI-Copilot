"""请求追踪：一条问答一行，落进 `request_trace`。M11 P1。

**为什么这件事排在灰度之前。**
`.env.example` 里写的灰度观察项是「journal 里 Agent 路径的报错，以及
答非所问 / 该查没查的反馈」。前半句 journal 里有，**后半句根本查不到**——
今天没有任何地方记录「这一轮调了哪些工具、检索到几块、rerank 最高分多少」。
观察手段不存在的话，灰度跑一周得到的只有一句「好像没报错」，
那不叫观察，那叫等。所以这是硬依赖，不是偏好。

⭐ **和直觉相反的一点：这个文件不是中间件。**
原计划是「中间件写入」，但 `StreamingResponse` 的响应体是在中间件
`call_next` **返回之后**才被消费的——中间件那一层看得到 URL 和状态码，
看不到答案、工具、检索命中，那些全部发生在它之后。真在中间件里写，
写出来的是一张只有「谁在什么时候打了 /api/chat」的表，
而那正是 nginx 日志已经有的东西。

所以分工是：
    中间件（logging_setup）   给一个 request id，仅此而已
    这里（流的生产者调用）     在一轮答完之后，把一整行写进去

三条不能破的规矩：

1. **写失败绝不影响回答。** 整条落库包在 try 里，出错只记一行日志。
   台账记漏一次，远好过「答案生成好了、却因为写台账报错而变成一句报错」。
2. **自己开会话。** 流里那个 session 可能已经因为取消而处在半死状态，
   借它写台账会连累到那边的回滚。
3. **shield 住取消。** 用户点「停止生成」时任务被取消，而**被中断的那一轮
   恰恰是最该留下记录的一轮**。不 shield 的话里面第一个 await 就再抛一次
   CancelledError，等于没写——于是表里永远只有顺利答完的请求，
   而你想查的偏偏是另一半。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import anyio

from copilot.db.models import RequestTrace
from copilot.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 问题原文的截断长度。存原文是为了「点开一条差评能看到当时问的什么」，
# 但有人把整本手册贴进来时没必要连它一起存——那是一份完整的用户文档，
# 属于隐私面更大、价值更小的一段
QUESTION_LIMIT = 2000
ERROR_LIMIT = 500

# 答案里的来源编号 `[1]`。和 qa.py / guard.py 用的是同一个形状
_CITE_RE = re.compile(r"\[\d{1,2}\]")

# 终结工具：它的返回**就是**给用户的最终答案（今天只有这一个）。
# 别的工具跑完之后 Agent 还要自己写一段，那一段的来源另算
_TERMINAL_TOOLS = {"answer_kb"}

KB = "kb"
GENERAL = "general_knowledge"
CANNED = "canned"
TOOL = "tool"
NO_ANSWER = "no_answer"
# M16：这一轮直接返回了一条人写定的标准答案，一次模型调用都没花。
# ⚠️ **不能并进 `kb`**：kb 是「模型看着材料写的」，verified 是「人写的原文」，
# 两者的质量归因完全不同——把订正的功劳算进模型的准确率，
# 那个数就再也说明不了模型好不好
VERIFIED = "verified"


def classify_answer_source(
    *, route: str, tools: list[str], answer: str, no_answer: bool, verified: bool = False
) -> str:
    """这一轮的答案**是从哪来的**（M13 P5）。

    ⭐ **为什么这件事必须当场判定，不能以后从别的列反推。**
    M12 之后「答了但没有出处」第一次变成一件正常且允许的事（行业常识），
    它同时也是最需要盯着的一件事。而现有的每一列都分不出它：

        直路的 `tools` 恒为空数组      → 反推不出
        `chunk_count` 只说检索到了几块  → 检索到了不等于用了
        `answer_kb` 既可能引材料也可能拒答

    判据按可靠性排序，**先定死的先判**：

        1. canned    路由就写在那里，没有歧义
        2. no_answer 兜底话术，调用方已经用 `is_no_answer` 判过了
        3. tool      Agent 这一轮只跑了非终结工具（出方案 / 查文档 / 报时间），
                     那段正文是围着工具结果写的，既不是材料也不是常识
        4. kb        正文里有 `[n]` —— M12 的规矩是「[n] 只属于材料里的内容」，
                     所以有编号就等于它在指着材料说话
        5. general_knowledge  剩下的：答了、没标一个来源编号

    ⚠️ **第 4 条会有边界情形：答案确实来自材料、但模型忘了标 [n]。**
    那种会被记成 general_knowledge。这不是漏洞而是取舍——「没标来源」本身
    就是个问题（用户没法溯源，评测里那一条叫引用正确率），
    与其猜它心里想的是什么，不如让这一列如实反映**用户看到的样子**。
    """
    if verified:
        return VERIFIED
    if route == "canned":
        return CANNED
    if no_answer:
        return NO_ANSWER
    used = set(tools or [])
    if used and not (used & _TERMINAL_TOOLS):
        return TOOL
    return KB if _CITE_RE.search(answer or "") else GENERAL


@dataclass
class TraceDraft:
    """一轮问答的草稿。流在跑的过程中往里填，结束时 `save()` 一次性落库。

    ⭐ **`id` 在流开始之前就定下来**，随 `data-trace` 片段发给前端。
    前端点 👎 时手上已经有这个 id 了——否则它得等流结束才知道该给哪一行打分，
    而用户恰恰是在看到烂答案的第一秒就想点那个按钮。
    """

    user_id: uuid.UUID
    question: str
    route: str  # direct | agent | canned
    mode: str = "fast"
    request_id: str | None = None

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None

    tools: list[str] = field(default_factory=list)
    chunk_count: int = 0
    top_score: float | None = None
    private_hits: int = 0

    model: str | None = None
    tokens: int = 0
    answer_chars: int = 0
    no_answer: bool = False
    # 这一轮直接返回了人写定的标准答案（`verified.lookup` 命中）。
    # 判定在 qa/tools 那边做，这里只负责如实记下来
    verified: bool = False
    # ===== M19-B 的四个观测维度 =====
    # 命中的那条标准答案，以及它是从哪次用户纠错来的。
    # ⭐ `correction_id` 让「用户提的这条纠错，后来真的救到人了吗」
    # 第一次可查——在此之前纠错发布之后就断线了
    verified_answer_id: uuid.UUID | None = None
    correction_id: uuid.UUID | None = None
    # 这一轮**允许**常识兜底吗（`ALLOW_GENERAL_KNOWLEDGE` 或评测显式传的）。
    # ⚠️ 这只是一半：另一半是「答案到底标没标来源」，两者与起来才是
    # `general_knowledge_used`，见 `summary()`
    general_allowed: bool = False
    # 发给前端几张配图。⚠️ 是**发出去的**，不是检索到的
    image_count: int | None = None
    # 答案正文。**只用来判 `answer_source`，不落库**——正文已经在 messages 表里
    # 有一份了，台账再存一份等于把每一条回答存两遍
    answer: str = ""
    ok: bool = True
    error: str | None = None

    _started: float = field(default_factory=time.monotonic)
    _ttfb: float | None = None
    _saved: bool = False

    # ---------- 流在跑的时候往里填 ----------

    def first_token(self) -> None:
        """第一个正文字到了。**只认第一次**，后面每个字都会调到这里。

        ⚠️ 记的是**正文**首字，不是推理草稿的首字。详解档的草稿 1 秒就到、
        正文要 8~60 秒，把草稿算成首字的话，这一列会显示「首字都很快」，
        而用户抱怨的那几十秒等待在表里一点痕迹都没有。
        """
        if self._ttfb is None:
            self._ttfb = time.monotonic() - self._started

    def retrieval(self, citations: list) -> None:
        """记这一轮检索到了什么。收 `Citation` 或它的 dict 形态都行。

        两条路给过来的形状不一样（直路是 dataclass，Agent 是已经 to_dict
        过的字典），在这里收口，省得调用方各写一遍。
        """
        scores = []
        for c in citations:
            score = c.get("score") if isinstance(c, dict) else getattr(c, "score", None)
            if score is not None:
                scores.append(float(score))
        self.chunk_count = len(citations)
        self.top_score = max(scores) if scores else None

    def failed(self, exc: BaseException) -> None:
        self.ok = False
        self.error = f"{type(exc).__name__}: {exc}"[:ERROR_LIMIT]

    # ---------- 对外读数 ----------

    def summary(self) -> dict:
        """这一轮的汇总数字。**台账那一行和 span 上的属性用的是同一份。**

        ⭐ 分开算是这里唯一真正的风险：看板上说 `answer_source=kb`、
        表里那一行写着 `general_knowledge`，两个数都对不上时，
        没有任何办法知道该信哪个——而这正是 admin 控制台立的那条规矩
        「每个数只有一个定义」（M13）。所以这个方法是唯一出处。

        `ttfb_ms` 在流被打断、或者压根没出正文时是 None，照实返回 None，
        不填 0：0 的意思是"快得测不出来"，而那是另一回事。
        """
        source = classify_answer_source(
            route=self.route,
            tools=self.tools,
            answer=self.answer,
            no_answer=self.no_answer,
            verified=self.verified,
        )
        return {
            "route": self.route,
            "mode": self.mode,
            "chunk_count": self.chunk_count,
            "top_score": self.top_score,
            "private_hits": self.private_hits,
            "model": self.model,
            "tokens": self.tokens,
            "answer_chars": self.answer_chars,
            "no_answer": self.no_answer,
            "answer_source": source,
            # ⚠️⚠️ **两个条件与起来，缺一不可。**
            #   允许了 + 没标来源  → true，正常：常识这条路确实产出了这次答案
            #   没允许 + 没标来源  → false，⚠️ 模型漏标 [n]，是引用正确率那条线的病
            #   允许了 + 标了来源  → false，这一句指着材料说话
            # 中间那一行是这一列存在的全部理由：在此之前这两种情形在表里
            # 长得一模一样，只看 answer_source 分不出「允许的常识」和「漏标的引用」
            "general_knowledge_used": self.general_allowed and source == GENERAL,
            "image_count": self.image_count,
            "verified_answer_id": self.verified_answer_id,
            "correction_id": self.correction_id,
            "ttfb_ms": None if self._ttfb is None else int(self._ttfb * 1000),
            "ok": self.ok,
        }

    # ---------- 落库 ----------

    async def save(self) -> None:
        """写这一行。**永远不抛异常。**

        `_saved` 挡重复写：正常路径和异常路径都会调到这里，
        重复写会撞主键，然后在 journal 里刷一堆和真正的错误无关的噪音。
        """
        if self._saved:
            return
        self._saved = True
        snap = self.summary()
        with anyio.CancelScope(shield=True):  # 见文件头第 3 条
            try:
                async with SessionLocal() as session:
                    session.add(
                        RequestTrace(
                            id=self.id,
                            request_id=self.request_id,
                            user_id=self.user_id,
                            conversation_id=self.conversation_id,
                            message_id=self.message_id,
                            route=self.route,
                            mode=self.mode,
                            question=self.question[:QUESTION_LIMIT],
                            tools=self.tools,
                            chunk_count=self.chunk_count,
                            top_score=self.top_score,
                            private_hits=self.private_hits,
                            model=self.model,
                            ttfb_ms=None if self._ttfb is None else int(self._ttfb * 1000),
                            total_ms=int((time.monotonic() - self._started) * 1000),
                            tokens=self.tokens,
                            answer_chars=self.answer_chars,
                            no_answer=self.no_answer,
                            # ⚠️ 从 `summary()` 取，**不在这里重算一遍**。
                            # 这是文件头「每个数只有一个定义」那条规矩：看板上说
                            # `answer_source=kb`、表里那行写 `general_knowledge`，
                            # 两个数对不上时没有任何办法判断哪个是对的
                            answer_source=snap["answer_source"],
                            general_knowledge_used=snap["general_knowledge_used"],
                            image_count=snap["image_count"],
                            verified_answer_id=self.verified_answer_id,
                            correction_id=self.correction_id,
                            ok=self.ok,
                            error=self.error,
                            created_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001 —— 见文件头第 1 条
                logger.warning(
                    "写 request_trace 失败 trace=%s route=%s", self.id, self.route, exc_info=True
                )
