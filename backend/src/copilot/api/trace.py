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

    # ---------- 落库 ----------

    async def save(self) -> None:
        """写这一行。**永远不抛异常。**

        `_saved` 挡重复写：正常路径和异常路径都会调到这里，
        重复写会撞主键，然后在 journal 里刷一堆和真正的错误无关的噪音。
        """
        if self._saved:
            return
        self._saved = True
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
