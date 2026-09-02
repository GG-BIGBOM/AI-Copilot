"""Agent 的依赖注入包。

Pydantic AI 把这个对象透传给每个工具（`ctx.deps`）。放进来的都是**工具自己
拿不到、也绝不能让模型指定**的东西：

    user_id   ⚠️ 隔离的命门。它只能从 cookie 里的登录态来。
              一旦让它变成工具的入参，模型就可以（被诱导着）填别人的 id，
              而那意味着一句 prompt injection 就能读到别人的私有文档。
    session   数据库会话
    embedder / reranker  检索用的 provider

`citations` 和 `images` 是**出参**：工具在跑的过程中往里累积，
一轮结束后由路由层一次性发给前端。不这么做的话，每次工具调用都得往流里插一次
引用，而模型完全可能在最后说「知识库暂无此内容」——那时页面上就是一句
「不知道」底下挂着五条来源（M1 踩过的坑，见 routes/chat.py 文件头第 1 条）。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from copilot.agent.checklist import Checklist, Requirement
from copilot.providers.base import Embedder, Reranker
from copilot.providers.llm import ChatLLM
from copilot.qa import DEFAULT_MODE

if TYPE_CHECKING:  # 运行时不导入，否则成环
    from copilot.session_facts import SessionFacts

# ⚠️ **`SessionFacts` 只能是类型注解，不能在运行时 import。**
# `session_facts` 要读 `agent.checklist.REQUIREMENT_FIELDS`（字段清单只许有一份），
# 而 `import copilot.agent.checklist` 会先跑 `agent/__init__.py`，那里面
# 一路 import 到这个文件——真导入的话就是
# `session_facts → agent → agent.deps → session_facts` 一个死环。
# 文件头已经有 `from __future__ import annotations`，注解本来就是字符串，
# dataclass 也不会去解析它，所以这里什么都不缺。

# 终结工具往外吐东西的出口。`(kind, payload)`：
#     ("text", "旺店")   正文增量，直接进用户看到的那条消息
#     ("images", None)   「现在把 deps.images 发出去」
# 为什么是元组而不是编好的 SSE：协议编码是 runner 的活（见 runner.py 文件头），
# agent 这一层不该知道 `text-delta` 这个字段名长什么样。
Emitter = Callable[[str, object], Awaitable[None]]


@dataclass
class AgentDeps:
    session: AsyncSession
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    embedder: Embedder
    reranker: Reranker | None = None
    # 直路要用的两样。`answer_kb` 里跑的就是直路整条，缺一样它就退化成
    # 「单轮 + 简答档」，而那正是 M10 要消灭的双路差异
    llm: ChatLLM | None = None
    history: list[tuple[str, str]] = field(default_factory=list)
    # True 表示更早的消息因 HISTORY_TURNS 上限被省略。模型必须知道这件事，
    # 否则问「第一个问题是什么」时会把当前窗口第一条冒充整段会话第一条。
    #
    # ⚠️ W2.2 之后它**仍然不能删**，但读法要跟着变：事实表能答出来的那几项
    # （哪一版 ERP、几个仓、哪家客户）已经不受窗口限制了，此时再回一句
    # 「我无法确认」就是明明知道却说不知道。判据见 runner 里那道闸门
    #
    # ⚠️⚠️ W2.1 之后**又要跟着变一次**，而且这一次是判据本身变了：
    # 「窗口裁了」不再等于「那段内容没了」——挤出窗口的轮次现在被压成一段
    # 摘要跟着进上下文。所以 `runner._beyond_window` 的第三个条件是
    # 「摘要里也没有」。这是 W2.1 唯一一处**必须改语义**的地方，
    # 漏了它的表现是：摘要里白纸黑字写着第一轮问的什么，而 Agent 照旧
    # 短路回一句「我无法确认你最开始问的是什么」——比 W2.2 之前更糟，
    # 因为这次系统手里真的有答案。
    history_truncated: bool = False
    # ⭐ 会话级已确认事实（W2.2）。**None = 这一轮不注入**。
    #
    # ⚠️ 「开关关着」和「表是空的」都表现为不注入，但它们不是一回事：
    # 关关着的时候路由层**照样记录**（写库、跨轮累积），只是不把这个对象交进来。
    # 这个顺序是刻意的——真开的那天，存量会话手里已经有账本了。
    # 谁决定开不开，就由谁传：那个人是路由层，`ask_stream` 自己不读配置
    # （评测要能在同一次运行里 A/B 两版 prompt）
    facts: SessionFacts | None = None
    # True 表示本轮属于实施方案收集流程。不能只看 `profile.filled()`：用户可能在
    # 第一轮一句话给齐 7 项，此时模型尚未调用工具，profile 仍是空的；若仍套用
    # 普通问答的 3 次工具上限，会在保存到第 4 项时整轮失败。
    plan_flow: bool = False
    mode: str = DEFAULT_MODE
    # 评测时可显式覆盖常识兜底；线上默认读取 settings。
    general: bool | None = None
    # ⭐ 用户这一轮的**原话**。`answer_kb` 拿它去检索，而不是让模型自己写检索词。
    # M7 的准确率掉 12 个点，第一条成因就是「检索词漂移」——模型把
    # 「京东电子面单怎么配」改写成「电子面单 模板 设置」，然后召回了得物那篇。
    # 让它成为工具入参就等于把漂移的机会还回去，所以它在 deps 里。
    question: str = ""
    # ⭐ 这条会话钉死的知识版本。**和 user_id 同级的隔离输入**：只能从会话记录来，
    # 绝不能变成工具的入参——让模型能指定它，等于一句 prompt injection
    # 就能把提问切到另一版 ERP 的材料上。
    # None 时检索一条都不返回（fail closed，见 retrieve._space_filter）
    space_id: uuid.UUID | None = None

    # 多轮收集的状态。进来时从 conversations 表读，出去时写回
    profile: Requirement = field(default_factory=Requirement)
    checklist: Checklist | None = None

    # 本轮累积的引用与配图，路由层在正文流完之后统一发
    citations: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    # 本轮召回的块里有几块来自用户自己的文档。给 request_trace 记一列（M11 P1）。
    # 一轮可能检索多次，取**最大值**而不是累加：这一列要回答的是
    # 「私有文档到底冒没冒头」，累加会把同一份文档数好几遍
    private_hits: int = 0
    # 本轮检索到的材料原文。两个用处，都不是可选的：
    #   1. 记 token 账要算它——**上下文才是大头**，只算问题和答案会漏掉八成
    #   2. 评测的判分器要拿它当「参考材料」，否则没法判答案有没有依据
    retrieved: list[str] = field(default_factory=list)
    # 本轮生成的下载地址（xlsx）
    download_url: str | None = None
    # ⭐ 本轮调过哪些工具（原始工具名，不是中文标签）。
    # **两个消费者共用这一份**：
    #   1. runner 的硬防线——一个工具都没调却写出像知识库答案的东西，就拦下来
    #   2. `request_trace.tools`——M11 验收标准第 8 条要能查出
    #      「trace 里 0 条『一个工具都没调却答了 ERP 问题』」
    # 原来它是 runner 里的一个局部变量，第 2 个消费者拿不到；
    # 挪到 deps 上之后两边读的是同一份，不会出现「防线看到调了、台账写着没调」
    used_tools: set[str] = field(default_factory=set)

    # ---------- 终结工具（M10）----------
    #
    # 「终结工具」= 它的返回**就是**给用户的最终答案，Agent 不再复述、不再加工。
    # 今天只有 `answer_kb` 一个。这个区分是 M10 的全部要点：Agent 永远拿不到
    # 原始材料，也就没有机会用它自由发挥（M7 掉 12 个点的四条成因都在这里）。
    emit: Emitter | None = None  # runner 注入，见文件头的 Emitter
    final_answer: str | None = None  # 非 None = 本轮已经有终结答案
    # 这一轮 answer_kb 直接返回了人写定的标准答案（M16）。
    # 路由层靠它把 `answer_source` 记成 verified
    verified_hit: bool = False
    # 命中的那条标准答案和它的纠错来源（M19-B 的台账列）。
    # ⚠️ 和 `verified_hit` 分开存：那个布尔量是 `answer_source` 用的，
    # 这两个 id 是给「这条标准答案被命中过几次」那种查法用的
    verified_answer_id: uuid.UUID | None = None
    verified_correction_id: uuid.UUID | None = None
    images_sent: bool = False  # 配图已经在正文之前发过了，路由层别再发一次
    # 本轮 `save_requirement` 空转了几次（字段已填、用户又没要求改）。
    # 连着空转说明模型把一句普通问题当成了需求，见 tools.save_requirement
    noop_saves: int = 0

    async def emit_text(self, chunk: str) -> None:
        """终结工具吐正文。没接 emitter 时（评测、单测）静默丢弃。"""
        if self.emit is not None and chunk:
            await self.emit("text", chunk)

    async def emit_images(self) -> None:
        """让 runner 现在就把 `images` 发出去。

        ⚠️ **必须在正文之前**，和直路一致：前端要边流边把 `[图1]` 换成真图，
        拿不到对照表就只能干等，用户看到的是一个裸的 `[图1]` 停在那里。
        """
        if self.emit is not None and self.images and not self.images_sent:
            await self.emit("images", None)

    @property
    def facts_prompt(self) -> str:
        """事实表渲染成的那一段，没有就是空串。两条路注入的是同一份文本。"""
        return self.facts.human() if self.facts is not None else ""

    @property
    def context_text(self) -> str:
        """本轮所有检索材料拼起来。给记账和判分用。"""
        return "\n\n".join(self.retrieved)

    def merge_citations(self, new: list[dict]) -> list[dict]:
        """把一次检索的引用并进本轮总表，返回**重新编号后**的那批。

        为什么要重编号：Agent 一轮里可能检索好几次，每次的引用都从 [1] 开始。
        直接拼起来的话会出现两个 [1]，答案里的 [1] 就指向不明——
        用户点开溯源看到的是另一篇文档。这里按「本轮全局」重新编号，
        同一篇（title+heading 相同）复用同一个号，不重复占位。
        """
        out: list[dict] = []
        for c in new:
            key = (c.get("title"), c.get("heading"))
            hit = next(
                (e for e in self.citations if (e.get("title"), e.get("heading")) == key), None
            )
            if hit is None:
                hit = {**c, "n": len(self.citations) + 1}
                self.citations.append(hit)
            out.append(hit)
        return out
