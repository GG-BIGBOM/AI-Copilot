"""评测：跑一遍评测集，出指标；换参数再跑，出对比表。

    uv run python ../eval/run.py --check                 # 只验检索，不调 LLM（免费、快）
    uv run python ../eval/run.py --tag baseline          # 跑全量，存成 results/baseline.json
    uv run python ../eval/run.py --tag topk40 --top-k 40
    uv run python ../eval/run.py --compare baseline topk40

（在 `backend/` 下用 `uv run` 执行，这样才能 import 到 copilot 包。）

设计上的几个决定：

1. **能用规则判的，绝不交给 LLM。** 「有没有说不知道」「关键事实字串在不在」
   「引用有没有指向期望的那篇」全是确定性判定——可复跑、零成本、不会今天一个
   结论明天另一个。LLM 只用来判语义层面的「这答案对不对」。
   这条很重要：判分器本身要是随机的，那调参时看到的指标变化就分不清是
   参数起了作用还是判分器抽了风。

2. **判分模型和答题模型分开配**（`EVAL_JUDGE_MODEL`）。同一个模型判自己的答案
   会偏心（self-preference bias，学界有定论）。手上只有 DeepSeek 一家的 key，
   所以默认用 `deepseek-reasoner` 判 `deepseek-chat` 的答案——同厂不同模型，
   不算干净，但比自己判自己好。**这是本评测已知的最大方法学缺陷**，报告里明写。

3. **检索串行、生成并行。** SiliconFlow 的限速器是实例级的裸变量
   （`self._last_at`，见 providers/siliconflow.py），多线程会互相踩，
   两个线程可能同时通过等待检查、一起打过去吃 429。而 DeepSeek 这边
   httpx.Client 本身线程安全、又没有共享限速状态，可以放开跑。

4. **一次运行的原始输出全存下来**（答案、引用、判分理由）。指标只是摘要，
   调参时真正有用的是「这道题上一版答对了、这一版怎么错的」——
   没有原始输出就只能重跑。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
DATASET = EVAL_DIR / "dataset.yaml"

sys.path.insert(0, str(EVAL_DIR.parent / "backend" / "src"))

# ⚠️ **Windows 控制台默认是 GBK，报告里的 ⚠️ / ⛔ / ✓ 一律编不出来。**
# 症状很坏：指标全算完了、json 也写好了，然后 `print_report` 打到一半
# 抛 UnicodeEncodeError，终端上留下半张报告和一段堆栈——看起来像评测崩了。
# 只放宽 errors、不改 encoding：中文在 GBK 下本来就打得出来，
# 换成 utf-8 反而会把整篇变成乱码；编不出的符号退化成 `?` 就够了。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

# ---------- 判分器的 prompt ----------

JUDGE_SYSTEM = """你是严格的评测判分员。给你一个问题、检索到的「参考材料」、
以及被评测系统给出的「答案」。

只依据参考材料判断，不要用你自己的知识补充。输出**纯 JSON**，不要代码块围栏，字段如下：

{
  "verdict": "correct" | "partial" | "wrong" | "no_answer",
  "grounded": true | false,
  "unsupported": "答案里材料不支持的具体说法，没有就空字符串",
  "reason": "一句话理由"
}

判定标准：
- correct：答案正确回答了问题，且与材料一致。
- partial：答对了一部分，或漏掉了问题问到的关键部分。
- wrong：答错了，或答的是别的事。
- no_answer：答案表示「知识库暂无此内容」这类不知道。
- grounded：答案里**每一条具体说法**（数字、路径、字段名、规则）都能在材料里找到依据。
  只要有一处是材料里没有、模型自己补的，就是 false，并把它写进 unsupported。
  注意：把材料的话换个说法、做合理概括，不算无依据。"""

JUDGE_USER = """问题：{q}

===== 参考材料 =====
{context}

===== 被评测的答案 =====
{answer}
"""

# 答案里的引用标记 [1]、[2][3]
_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def wanted_sources(case: dict) -> list[str]:
    """期望来源。允许写成列表——同一个问题常有好几篇文档都能正当地回答，
    只认一篇会把「检索对了但不是我预设那篇」误判成失败。"""
    want = case.get("source")
    if not want:
        return []
    return [want] if isinstance(want, str) else list(want)


# 否定词。`must_not_include` 是裸子串匹配，而中文的否定在**前面**，
# 于是「不支持指定员工」里含着被禁的「支持指定员工」
_NEGATORS = "不非无未没别勿"
# 往前看几个字。「不是平台结算单」隔 1 个字，「不使用二联」隔 2 个
_NEG_WINDOW = 3


def banned_hits(answer: str, banned: list[str]) -> list[str]:
    """答案里真的出现了被禁内容的那几条。

    ⚠️ **必须绕开否定句，否则这条规则是反的。** 2026-08-21 核对三轮历史结果，
    `must_not_include` 命中的**三条全是假阳性**，且三条都是同一个形状——
    被禁串前面正好有个否定词：

        禁 '支持指定员工'   答案「群消息通知**不**支持指定员工」   ← 这正是标准答案
        禁 '二联'          答案「统一 76×130，**不**使用二联」
        禁 '平台结算单'     答案「以 ERP 出库单为准，**不是**平台结算单为准」

    裸 `in` 判定会把这三句正确答案判成「串了平台/串了客户」。而这条规则
    存在的理由恰恰是抓串台（M13 P1 的 `cross_platform_contamination_rate`），
    抓错了比不抓更糟。

    ⚠️ 这仍然是个近似：「不仅支持指定员工」会被误放行。出题时把被禁串写得
    具体一点（带数字、带平台名）比在这里堆规则可靠——**这个函数的职责是
    别把否定句判成违规，不是理解中文**。
    """
    hits: list[str] = []
    low = answer.lower()
    for term in banned:
        t = term.lower()
        if not t:
            continue
        start = 0
        while (i := low.find(t, start)) >= 0:
            if not set(answer[max(0, i - _NEG_WINDOW) : i]) & set(_NEGATORS):
                hits.append(term)
                break
            start = i + 1
    return hits


# ---------- 数据结构 ----------


def _guard_suffix(mode: str, general: bool | None = None) -> str:
    """主体约束那一段（M11 P3 第 3 步）追加在 system prompt 后面的文本。

    ⚠️ **拿 `system_prompt_for` 算差值，不要自己抄一份。**
    那段话只该有一个出处；抄一份的话，改了线上那份而评测还在用旧的，
    评测就会一直报告一个早就不存在的系统。
    """
    from copilot.qa import system_prompt_for

    base = system_prompt_for(mode, general=general)
    return system_prompt_for(mode, subject_guard=True, general=general)[len(base) :]


@dataclass
class Config:
    top_k: int = 0  # 0 = 用 settings 里的默认值
    rerank_k: int = 0
    threshold: float = -1.0  # <0 = 用默认
    prompt: str = "current"  # current = 线上那版；其余取 eval/prompts.py 的存档
    agent: bool = False  # True = 评 M7 的 Agent 路径（检索由 Agent 自己决定）
    # 回答档位：fast 简答（DeepSeek）/ deep 详解（Kimi）。
    # 两档的**铁律完全一样**，差的是写法和模型——所以这才是一次干净的 A/B
    mode: str = "fast"
    # 常识兜底（M12）。None = 读 .env；True/False = 这一轮强制用哪一版铁律 1。
    # ⚠️ 它**同时影响两处**：prompt 里的铁律 1 和 3，以及「一条都没召回」时
    # 还调不调模型。只改 prompt 不改闸门的话，放开版会在所有 no_answer 题上
    # 拿到和严格版一模一样的兜底话术——A/B 会显示「毫无变化」，而那是假的
    general: bool | None = None

    def system_prompt(self) -> str | None:
        if self.prompt == "current":
            from copilot.qa import DEFAULT_MODE, system_prompt_for

            # 档位不是默认那档时，要用那一档的 prompt，否则 A/B 只换了模型没换写法。
            # `general` 显式指定时也必须自己拼——`SYSTEM_PROMPT` 那个模块常量是
            # 按 .env 在 import 时算好的，拿它做 A/B 等于两轮都用同一版铁律 1
            if self.mode == DEFAULT_MODE and self.general is None:
                return None
            return system_prompt_for(self.mode, general=self.general)
        from prompts import ARCHIVE

        if self.prompt not in ARCHIVE:
            sys.exit(f"没有这版 prompt：{self.prompt}（可选 current、{'、'.join(ARCHIVE)}）")
        return ARCHIVE[self.prompt]

    def resolved(self) -> dict:
        import hashlib

        from copilot.config import get_settings
        from copilot.qa import SYSTEM_PROMPT

        s = get_settings()
        prompt_text = self.system_prompt() or SYSTEM_PROMPT
        return {
            "prompt": self.prompt,
            "path": "agent" if self.agent else "direct",
            "top_k": self.top_k or s.retrieve_top_k,
            "rerank_k": self.rerank_k or s.rerank_top_k,
            "threshold": s.rerank_score_threshold if self.threshold < 0 else self.threshold,
            "chunk_size": s.chunk_size,
            "chunk_overlap": s.chunk_overlap,
            "embedding_model": s.embedding_model,
            "rerank_model": s.rerank_model,
            "mode": self.mode,
            "answer_model": s.llm_deep_model if self.mode == "deep" else s.llm_model,
            # ⭐ prompt 指纹。没有它，「改了 prompt 重跑」和「什么都没改重跑」
            # 存出来的 config 一模一样——半年后看对比表根本分不清哪轮是哪轮。
            #
            # ⚠️ **要把主体约束那一段也算进去**（M11 P3）。它是追加在 system prompt
            # 后面的，不在 `prompt_text` 里——2026-08-20 那天连跑四轮私有库，
            # v3 和 v4 只差主体约束里的一句话，结果两轮存出来的 sha 一模一样。
            # 指纹漏掉了唯一变过的那个东西，等于没有指纹。
            "prompt_sha": hashlib.sha256(
                (prompt_text + _guard_suffix(self.mode, self.general)).encode()
            ).hexdigest()[:8],
        }


@dataclass
class CaseResult:
    id: str
    kind: str
    q: str
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    context: str = ""
    retrieved_titles: list[str] = field(default_factory=list)
    top_score: float = 0.0
    # 这一轮召回里有几块来自私有库，以及要不要追加主体约束（M11 P3）。
    # `private_hits` 是纯诊断用的一列：私有题答错时，先看它是 0（检索根本没捞到）
    # 还是非 0（捞到了但模型没用）——两种失败的修法完全不同，
    # 而只看答案文本，它们长得一模一样
    private_hits: int = 0
    subject_guard: bool = False

    # 确定性判定
    source_hit: bool | None = None  # 期望源是否出现在引用里（无期望源时为 None）
    cited_source: bool | None = None  # 答案的 [n] 是否指向期望源
    missing_facts: list[str] = field(default_factory=list)
    # `must_not_include` 里真的出现了的那几条（已绕开否定句，见 `banned_hits`）。
    # ⚠️ 原来这个判定的结果只写进 `unsupported` 那句说明里，**没有任何地方
    # 拿它判过分**——dataset.yaml 的注释写着「出现即算错」，而代码里不算。
    # M13 P0 一并补上：它现在是确定性失败的一种
    banned_hits: list[str] = field(default_factory=list)
    said_no_answer: bool = False

    # LLM 判定
    verdict: str = ""
    grounded: bool | None = None
    unsupported: str = ""
    reason: str = ""
    # ⭐ 判分器**自己**挂了（网络断、限流、吐不出 JSON），不是模型答错了。
    # 见 `judge_all` 和 `score` 里那两段长注释——这一列是 M13 P0 的全部要点
    judge_error: bool = False

    # 汇总
    # correct / incorrect / invalid 三态。**不是 `passed` 的同义反复**：
    # `passed=False` 混了「答错了」和「没判成」两件事，而后者不该算进准确率
    status: str = ""
    passed: bool = False
    fail_why: str = ""


# ---------- 载入 ----------


# 答案 / 材料里的配图标记。和 qa.py 里模型被要求写的格式一致
_PIC_RE = re.compile(r"\[图\s*\d{1,2}\]")


def _count_pics(text: str) -> int:
    return len(_PIC_RE.findall(text or ""))


def load_cases(only: str | None = None, scope: str = "public") -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    # ⭐ scope 默认只取公共库的题。**这条不能省**：私有库的题打的是别人的
    # 文档集，混进来会让历史 tag 之间的 --compare 变成拿两个不同的题集比大小，
    # 而报告上完全看不出来
    cases = [c for c in data["cases"] if c.get("scope", "public") == scope]
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["kind"] in keys]
    return data.get("meta", {}), cases


def resolve_user(email: str):
    """把邮箱换成 user_id。私有库评测用。"""
    import asyncio

    from sqlalchemy import select

    from copilot.db.models import User
    from copilot.db.session import SessionLocal

    async def go():
        from copilot.db.session import engine

        try:
            async with SessionLocal() as s:
                return (
                    await s.execute(select(User).where(User.email == email))
                ).scalar_one_or_none()
        finally:
            # ⭐ 必须 dispose。这个函数自己 `asyncio.run()` 起了一个事件循环，
            # 用完就关；但连接池里那条 asyncpg 连接还绑在已经死掉的循环上。
            # 后面 retrieve_all 再 `asyncio.run()` 时会复用它，报出来的是
            # `AttributeError: 'NoneType' object has no attribute 'send'`——
            # 一个和「用户查询」八竿子打不着的错误，排查方向全歪。
            await engine.dispose()

    user = asyncio.run(go())
    if user is None:
        raise SystemExit(f"库里没有这个用户：{email}")
    return user.id


# ---------- 阶段一：检索（串行） ----------


CORPUS_STATS: dict = {}  # 检索时顺手记下当时的块数，换 chunk 参数重灌后能看出规模变化


def retrieve_all(
    cases: list[dict], cfg: Config, quiet: bool = False, user_id=None
) -> list[CaseResult]:
    import asyncio

    from sqlalchemy import func, or_, select

    from copilot.db.models import Chunk
    from copilot.db.session import SessionLocal
    from copilot.providers.siliconflow import (
        SiliconFlowClient,
        SiliconFlowEmbedder,
        SiliconFlowReranker,
    )
    from copilot.qa import needs_subject_guard
    from copilot.retrieve import search

    r = cfg.resolved()

    async def main() -> list[CaseResult]:
        client = SiliconFlowClient()
        emb, rr = SiliconFlowEmbedder(client=client), SiliconFlowReranker(client=client)
        out: list[CaseResult] = []
        async with SessionLocal() as session:
            # 可见范围 = 公共库 +（指定用户时）他的私有库，和线上完全一致
            scope_filter = (
                Chunk.owner_id.is_(None)
                if user_id is None
                else or_(Chunk.owner_id.is_(None), Chunk.owner_id == user_id)
            )
            CORPUS_STATS["chunk_count"] = await session.scalar(
                select(func.count(Chunk.id)).where(scope_filter)
            )
            for i, case in enumerate(cases, 1):
                res = await search(
                    session,
                    case["q"],
                    emb,
                    rr,
                    user_id=user_id,  # None = 只打公共库
                    top_k=r["top_k"],
                    rerank_k=r["rerank_k"],
                    score_threshold=r["threshold"],
                )
                bundle = res.build_context()
                cr = CaseResult(
                    id=case["id"],
                    kind=case["kind"],
                    q=case["q"],
                    citations=[c.to_dict() for c in res.citations],
                    context=bundle.text,
                    retrieved_titles=[c.title for c in res.citations],
                    top_score=res.citations[0].score if res.citations else 0.0,
                    private_hits=res.private_count,
                )
                # M11 P3 第 3 步。**调的是线上那个函数**，不是抄一份判定逻辑——
                # 抄一份的话，改了那边忘了改这边，评测会一直在报告一个
                # 早就不存在的系统，而私有库这组题量的恰恰就是这条规则
                cr.subject_guard = await needs_subject_guard(session, case["q"], user_id)
                if wants := wanted_sources(case):
                    cr.source_hit = any(w in t for w in wants for t in cr.retrieved_titles)
                out.append(cr)
                if not quiet:
                    flag = "" if cr.source_hit is None else ("命中" if cr.source_hit else "未中")
                    print(f"  [{i:2}/{len(cases)}] {case['id']:34} {flag}")
        client.close()
        return out

    return asyncio.run(main())


# ---------- 阶段二：生成答案（并行） ----------


def answer_all(
    results: list[CaseResult],
    workers: int = 5,
    quiet: bool = False,
    system_prompt: str | None = None,
    mode: str = "fast",
    general: bool | None = None,
) -> None:
    from copilot.config import get_settings
    from copilot.providers.llm import ChatLLM
    from copilot.qa import EMPTY_CONTEXT, NO_ANSWER, SYSTEM_PROMPT, USER_TEMPLATE, is_no_answer

    # 常识兜底开着时，「一条都没召回」不再是免费的兜底话术——那正是最该问
    # 一次模型的时候。留 None 就读 .env，和线上一致
    if general is None:
        general = get_settings().allow_general_knowledge

    # 允许换 prompt：A/B 时必须拿**同一份评测集**跑两个 prompt，
    # 否则指标的变化归不了因（见 eval/prompts.py 的说明）
    prompt = system_prompt or SYSTEM_PROMPT
    # M11 P3 第 3 步的那一段附加约束（只加在标了 subject_guard 的题上）
    guarded_suffix = _guard_suffix(mode)

    # httpx.Client 线程安全，一个实例够用
    if mode == "deep":
        s = get_settings()
        llm = ChatLLM(
            api_key=s.llm_deep_api_key or s.vision_api_key,
            base_url=s.llm_deep_base_url,
            model=s.llm_deep_model,
            # ⚠️ kimi-k* 只认 temperature=1，下面那句 temperature=0.0 会被它顶掉。
            # 也就是说详解档**没法压到 0**，两轮之间会有抖动——看对比表时要记着这点
            forced_temperature=s.llm_deep_temperature,
        )
    else:
        llm = ChatLLM()

    def one(cr: CaseResult) -> None:
        if not cr.citations and not general:
            # 第一道闸门：什么都没召回，线上会直接返回兜底话术，不调 LLM。
            # ⚠️ **常识兜底开着时这道闸门要让路**，否则所有 no_answer 题都会
            # 拿到一模一样的兜底话术，A/B 显示「毫无变化」——而那是假的：
            # 线上那条路会去问模型。评测和线上在这里必须是同一个行为
            cr.answer = NO_ANSWER
        else:
            user_msg = USER_TEMPLATE.format(
                context=cr.context or EMPTY_CONTEXT, question=cr.q
            )
            system = prompt + guarded_suffix if cr.subject_guard else prompt
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
            try:
                # ⚠️ 评测里把温度压到 0，线上是 0.1。
                # 实测同一份配置连跑三轮，41 题里有 2 题会翻来翻去（≈5% 抖动）——
                # 那比「改一处 prompt」带来的提升还大，指标就没法归因了。
                # 代价是评的不完全是线上那个温度，但**可复现**比「完全一致」更值。
                cr.answer = llm.complete(messages, temperature=0.0)
            except Exception as e:  # noqa: BLE001 - 单题失败不该毁掉整轮
                cr.answer = f"__ERROR__ {type(e).__name__}: {e}"
        cr.said_no_answer = is_no_answer(cr.answer)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(one, results), 1):
            if not quiet and i % 5 == 0:
                print(f"  已生成 {i}/{len(results)}")
    llm.close()


# ---------- Agent 路径（M7）----------


def run_agent_cases(
    cases: list[dict], cfg: Config, *, user_id: uuid.UUID | None = None
) -> list[CaseResult]:
    """让 Agent 自己跑每一道题。

    **和直路的评测不是同一件事**：直路是「先检索固定 top-k，再答」，Agent 是
    「自己决定检索几次、用什么词」。所以这里量到的检索命中率含义也不同——
    它衡量的是 Agent **会不会问对问题**，而不是检索器准不准。

    串行跑。Agent 一轮里要打好几次 SiliconFlow，并发会把那个非线程安全的
    限速器踩坏（见文件头第 3 条）。代价是慢，41 题大约十几分钟。
    """
    import asyncio

    from sqlalchemy import func, select

    from copilot.agent.deps import AgentDeps
    from copilot.agent.runner import run_agent_stream
    from copilot.db.models import Chunk
    from copilot.db.session import SessionLocal
    from copilot.providers.llm import ChatLLM
    from copilot.providers.siliconflow import (
        SiliconFlowClient,
        SiliconFlowEmbedder,
        SiliconFlowReranker,
    )
    from copilot.qa import is_no_answer

    async def main() -> list[CaseResult]:
        client = SiliconFlowClient()
        emb, rr = SiliconFlowEmbedder(client=client), SiliconFlowReranker(client=client)
        # ⚠️ M10 起 `answer_kb` 要用它跑直路。不接的话工具直接返回
        # 「回答功能暂时不可用」，整份报告会是一堆空答案——**而且不报错**。
        # `forced_temperature=0`：同直路评测的理由，可复现比"和线上完全一致"更值
        answer_llm = ChatLLM(forced_temperature=0.0)
        out: list[CaseResult] = []
        async with SessionLocal() as session:
            CORPUS_STATS["chunk_count"] = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.owner_id.is_(None))
            )
            for i, case in enumerate(cases, 1):
                deps = AgentDeps(
                    session=session,
                    # 公共库用随机用户保持可见范围为公共库；私有评测必须沿用
                    # `--as-user` 解析出的 ID，否则私有文档永远不会被检索到。
                    user_id=user_id or uuid.uuid4(),
                    conversation_id=uuid.uuid4(),
                    embedder=emb,
                    reranker=rr,
                    llm=answer_llm,
                )
                answer = ""
                try:
                    async for _part, so_far in run_agent_stream(case["q"], deps):
                        answer = so_far
                except Exception as e:  # noqa: BLE001 - 单题失败不该毁掉整轮
                    answer = f"__ERROR__ {type(e).__name__}: {e}"

                cr = CaseResult(
                    id=case["id"],
                    kind=case["kind"],
                    q=case["q"],
                    answer=answer,
                    citations=deps.citations,
                    context=deps.context_text,
                    retrieved_titles=[c.get("title", "") for c in deps.citations],
                    top_score=deps.citations[0].get("score", 0.0) if deps.citations else 0.0,
                    said_no_answer=is_no_answer(answer),
                )
                if wants := wanted_sources(case):
                    cr.source_hit = any(w in t for w in wants for t in cr.retrieved_titles)
                out.append(cr)
                flag = "" if cr.source_hit is None else ("命中" if cr.source_hit else "未命中")
                print(f"  [{i:2}/{len(cases)}] {case['id']:34} {flag}")
        client.close()
        answer_llm.close()
        return out

    return asyncio.run(main())


# ---------- 阶段三：判分（并行） ----------


# 判分重试次数。跨境网络抖动是常态，不重试的话指标会被网络污染。
# 指数退避 1s / 2s / 4s：**不是无限重试**——判分器真挂了就该如实报成
# INVALID，把一轮拖成半小时换不来一个更真的数字
JUDGE_RETRIES = 3
JUDGE_BACKOFF = (1.0, 2.0, 4.0)
# 判分器出的是一小段 JSON，正常两三秒回来。给 60 秒是宽限，不是等待预算——
# 用答题那条路的 180 秒会让一条卡住的连接吃掉三次退避的时间（见 ChatLLM.timeout）
JUDGE_TIMEOUT = 60.0

# 判分失效率超过这条线，整轮结果标 UNRELIABLE：可以看，但不能拿来
# 判断「哪版 prompt 更好」。理由见 `score()` 里那段注释
JUDGE_ERROR_LIMIT = 5.0


def judge_all(
    results: list[CaseResult], cases: list[dict], workers: int = 5, quiet: bool = False
) -> str:
    from copilot.config import get_settings
    from copilot.providers.llm import ChatLLM

    s = get_settings()
    model = s.eval_judge_model or s.llm_model
    judge = ChatLLM(
        api_key=s.eval_judge_api_key or s.llm_api_key,
        base_url=s.eval_judge_base_url or s.llm_base_url,
        model=model,
        timeout=JUDGE_TIMEOUT,
    )
    by_id = {c["id"]: c for c in cases}

    def one(cr: CaseResult) -> None:
        case = by_id[cr.id]
        # 确定性判定先做完，判分器不参与这部分
        cr.missing_facts = [
            f for f in (case.get("must_include") or []) if f.lower() not in cr.answer.lower()
        ]
        cr.banned_hits = banned_hits(cr.answer, case.get("must_not_include") or [])
        if cr.banned_hits:
            cr.unsupported = f"出现了禁止内容：{cr.banned_hits}"

        if cr.said_no_answer:
            # 说了不知道就不必判语义了，省一次调用
            cr.verdict, cr.grounded, cr.reason = "no_answer", True, "答案是兜底话术"
            return

        messages = [
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": JUDGE_USER.format(
                    q=cr.q, context=cr.context[:6000], answer=cr.answer[:3000]
                ),
            },
        ]
        # ⭐ 判分必须重试。国内连 Gemini 会随机 SSL 断流——2026-08-18 第一轮
        # 55 题里 11 题挂在 `UNEXPECTED_EOF_WHILE_READING`，报告上显示成
        # 「12 题没过」，看起来像模型答错了，**而它们根本没被判过**。
        # 判分器的网络抖动伪装成模型退化，是这套指标最坏的一种失真。
        #
        # ⭐⭐ **M13 P0：重试用完之后的那一行不是「答错」，是「没判成」。**
        # 2026-08-20 的 `m12-general-on` 那一轮，61 题里 5 题挂在这里，
        # 报告上显示成准确率 88.5%（严格版是 95.1%）——看起来像放开常识
        # 把系统打退化了 6.6 个点，而其中 4 个点纯粹是 Gemini 那边的 SSL 断连。
        # 差一点就据此把一个正确的产品决定回滚掉。
        #
        # 所以这里只负责如实标记，判不判得进准确率交给 `score()`。
        raw = ""
        last: Exception | None = None
        for attempt in range(JUDGE_RETRIES):
            try:
                raw = judge.complete(messages, temperature=0.0)
                payload = json.loads(_strip_fence(raw))
                cr.verdict = str(payload.get("verdict", ""))
                cr.grounded = bool(payload.get("grounded"))
                cr.unsupported = cr.unsupported or str(payload.get("unsupported") or "")
                cr.reason = str(payload.get("reason") or "")
                return
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < JUDGE_RETRIES - 1:
                    # 指数退避。限流（429）和跨境抖动都要一点时间才缓过来，
                    # 固定间隔重试等于三次撞同一堵墙
                    time.sleep(JUDGE_BACKOFF[min(attempt, len(JUDGE_BACKOFF) - 1)])
        cr.verdict = "judge_error"
        cr.judge_error = True
        cr.reason = f"{type(last).__name__}: {last} | 原始输出：{raw[:160]}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(one, results), 1):
            if not quiet and i % 5 == 0:
                print(f"  已判分 {i}/{len(results)}")
    judge.close()
    return model


def _strip_fence(text: str) -> str:
    """判分器有时会把 JSON 包在 ```json 里，即使明确要求过不要。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        t = t.rsplit("```", 1)[0]
    # 再兜一层：只取第一个 { 到最后一个 }
    if (i := t.find("{")) >= 0 and (j := t.rfind("}")) > i:
        t = t[i : j + 1]
    return t.strip()


# ---------- 汇总 ----------


def score(results: list[CaseResult], cases: list[dict]) -> dict:
    """把逐题结果压成指标。每个指标的定义都写在这里，别散到别处去。"""
    by_id = {c["id"]: c for c in cases}

    for cr in results:
        case = by_id[cr.id]
        wants = wanted_sources(case)

        # 答案里 [n] 标注的编号，是否有一个指向期望的那篇
        if wants and cr.citations:
            cited = {int(n) for n in _CITE_RE.findall(cr.answer)}
            cr.cited_source = any(
                c["n"] in cited and any(w in (c["title"] or "") for w in wants)
                for c in cr.citations
            )

        # ⭐⭐ **确定性判定排在判分器前面，顺序不能反**（M13 P0）。
        # 「该说不知道却答了」「材料里有却答不知道」「漏了关键事实」「串了别家的
        # 数字」这四种，看答案文本就能定，判分器挂不挂都不影响结论——
        # 所以它们**永远不会是 INVALID**。只有真正要靠判分器给语义结论的那几题，
        # 判分器挂了才算「这题没评上」。
        #
        # 反过来做（先看 judge_error 再看确定性）会把一批本来铁板钉钉的失败
        # 洗成 INVALID，那是另一个方向的失真：分母越洗越小，准确率越洗越高。
        if cr.kind == "no_answer":
            cr.status = "correct" if cr.said_no_answer else "incorrect"
            cr.fail_why = "" if cr.said_no_answer else "该说不知道，却给了实质答案（幻觉）"
        elif cr.said_no_answer:
            cr.status = "incorrect"
            cr.fail_why = "材料里有答案，却答了「暂无此内容」（假阴性）"
        elif cr.missing_facts:
            cr.status = "incorrect"
            cr.fail_why = f"漏掉关键事实：{cr.missing_facts}"
        elif cr.banned_hits:
            cr.status = "incorrect"
            cr.fail_why = f"出现了禁止内容：{cr.banned_hits}"
        elif cr.judge_error:
            cr.status = "invalid"
            cr.fail_why = f"判分器没判成（不计入准确率）：{cr.reason[:70]}"
        elif cr.kind == "partial":
            # 部分覆盖的题，要的是「答已有的 + 点明缺的」。判分器给 correct 或
            # partial 都算过——它看到的是同一份材料，能确认答案没超出材料
            ok = cr.verdict in ("correct", "partial") and cr.grounded is not False
            cr.status = "correct" if ok else "incorrect"
            cr.fail_why = "" if ok else f"判分：{cr.verdict}／{cr.reason}"
        else:
            ok = cr.verdict == "correct" and cr.grounded is not False
            cr.status = "correct" if ok else "incorrect"
            cr.fail_why = "" if ok else f"判分：{cr.verdict}／{cr.reason}"
        # `passed` 留着：历史 json、`compare()` 的逐题变化、报告里的「没过的 N 题」
        # 都在读它。**注意它把 invalid 也算成没过**——凡是要算比例的地方
        # 一律用 `status`，别用 `passed`
        cr.passed = cr.status == "correct"

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    positives = [r for r in results if r.kind != "no_answer"]  # 材料里有答案的题
    negatives = [r for r in results if r.kind == "no_answer"]
    with_source = [r for r in results if r.source_hit is not None]
    answered_with_source = [r for r in with_source if not r.said_no_answer]

    # ⭐ 分母。**准确率的分母是「评上了的题」，不是「跑了的题」**（M13 P0）。
    # 判分器断线的那几题既不能算对也不能算错，只能算没评上——把它们塞进分母，
    # 等于用国内到 Gemini 的网络质量给模型打分。
    valid = [r for r in results if r.status != "invalid"]
    invalid = [r for r in results if r.status == "invalid"]

    m = {
        "题数": len(results),
        "有效题数": len(valid),
        "判分失效": len(invalid),
        "判对": sum(r.status == "correct" for r in results),
        "判错": sum(r.status == "incorrect" for r in results),
        "准确率": pct(sum(r.status == "correct" for r in valid), len(valid)),
        "判分失效率": pct(len(invalid), len(results)),
        "检索命中率": pct(sum(bool(r.source_hit) for r in with_source), len(with_source)),
        "引用正确率": pct(
            sum(bool(r.cited_source) for r in answered_with_source), len(answered_with_source)
        ),
        # ⚠️ 幻觉率 / 假阴性率 / 检索命中率 / 引用正确率 / 配图带出率的分母**不剔除
        # invalid**——它们全是看答案文本就能定的确定性判定，判分器挂了也照样算得出。
        # 剔了反而会让这几个最要紧的指标跟着网络质量抖
        "幻觉率": pct(sum(not r.said_no_answer for r in negatives), len(negatives)),
        "假阴性率": pct(sum(r.said_no_answer for r in positives), len(positives)),
        # 「无据陈述」是判分器给的结论，所以这一条的分母要剔掉 invalid
        "无据陈述率": pct(
            sum(r.grounded is False for r in valid if not r.said_no_answer),
            len([r for r in valid if not r.said_no_answer]),
        ),
    }
    # ⭐ 这一轮的结论能不能用来比较。超过 5% 的题没评上时，
    # 「这版 prompt 比那版高 2 个点」这句话没有意义——差值可能整个落在噪声里
    m["可信"] = m["判分失效率"] <= JUDGE_ERROR_LIMIT

    # ⭐ 配图带出率：**该带截图的题**，答案真的把 [图N] 带出来了吗。
    #
    # 加这条是因为线上反馈「回答没有图片了」。查下来图片链路整条都是好的，
    # 真正发生的是：检索选中了正确文档里**没有截图的那一块**，
    # 而模型（正确地）不肯去借它没引用的那一块的图。
    # 没有这个数字的话，这类退化在所有现有指标上都是隐形的——
    # 准确率、命中率、幻觉率全都不会动一下。
    #
    # ⚠️ **分母是标了 `procedural` 的题，不是"材料里有图的题"。**
    # 第一版就是按后者算的，结果算出来 15.6%——因为 55 题里绝大多数是事实查询
    # （「极兔的平台编码是什么」这种），一句话答完，配图本来就不合适，
    # 模型不写图是对的。拿它们当分母，这个数纯粹是噪声。
    # 分母必须由出题人显式标注，不能靠问题里的关键词去猜：猜出来的分母
    # 会随着有人换个问法而变，指标就没法跨轮比了。
    procedural_ids = {c["id"] for c in cases if c.get("procedural")}
    procedural = [r for r in results if r.id in procedural_ids and not r.said_no_answer]
    if procedural:
        m["配图带出率"] = pct(
            sum(_count_pics(r.answer) > 0 for r in procedural), len(procedural)
        )
        m["操作类题数"] = len(procedural)
    # ⭐ 难题单独一条。总准确率会被 41 道已经饱和的老题稀释——
    # 14 道新题全错，总数也才掉 25 个点，看着像「小幅波动」
    hard_ids = {c["id"] for c in cases if c.get("hard")}
    hard = [r for r in valid if r.id in hard_ids]
    if hard:
        m["难题准确率"] = pct(sum(r.passed for r in hard), len(hard))
        m["难题数"] = len(hard)

    # 分类准确率同样按有效题算分母
    m["分类准确率"] = {
        kind: pct(
            sum(r.passed for r in valid if r.kind == kind),
            len([r for r in valid if r.kind == kind]),
        )
        for kind in ("fact", "probe", "partial", "no_answer")
    }
    return m


METRIC_HELP = {
    "准确率": "全部题里判对的比例（各类的判对标准见 score()）",
    "检索命中率": "有期望来源的题里，期望那篇出现在引用中的比例。它和准确率分开看："
    "检索命中而答错 = 生成的问题；检索没命中 = 检索的问题",
    "引用正确率": "答了的题里，答案的 [n] 真的指向期望来源的比例。挂了一堆来源"
    "但正文没引 = 用户没法溯源",
    "幻觉率": "该说「暂无此内容」却给了实质答案的比例。**这个数字要压到 0**",
    "假阴性率": "材料里有答案、却答「暂无此内容」的比例。它和幻觉率是一对："
    "prompt 闸门收紧一分，幻觉降一点、假阴性涨一点",
    "配图带出率": "标了 procedural 的题中，答案真的写出了 [图N] 的比例。"
    "低不一定是模型的错——更常见的是检索选中了同一篇文档里没有截图的那一块。"
    "**分母只算操作类题**：事实查询本来就不该配图",
    "难题准确率": "标了 hard 的题（多跳/跨文档/否定/条件）的判对比例。"
    "**改动的效果先看这个数**——老题在 v3 上已经饱和，看总准确率会被稀释成噪声",
    "无据陈述率": "答了的题里，判分器发现「有材料不支持的具体说法」的比例。"
    "比幻觉率更细：答案整体方向对，但夹了一句编的",
    "判分失效率": "判分器**自己**没跑成（断线/限流/吐不出 JSON）的题占比。"
    "**这不是模型答错**——这些题不进准确率的分母。超过 5% 整轮标 UNRELIABLE",
    "有效题数": "真正评上了的题数 = 判对 + 判错。准确率的分母是它，不是题数",
}


# ---------- 输出 ----------


def _slim(case: dict) -> dict:
    """存档时剥掉 `context`，只留长度。

    上下文是每条结果里最大的一块（5 块材料约 2500 字），41 题存下来 300KB，
    而它的内容是语雀语料的拷贝——把它提交进 git 等于把知识库复制一份进版本库
    （见「七、约定 1」的同一类顾虑）。
    排查需要的东西都留着：答案、引用清单、判分理由、命中与否。
    真要看当时的上下文，把同一个 tag 重跑一遍就有——检索是确定性的。
    """
    out = dict(case)
    out["context_chars"] = len(out.pop("context", "") or "")
    return out


def save(tag: str, meta: dict, cfg: Config, metrics: dict, results: list[CaseResult], judge: str):
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {
        "tag": tag,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus": meta.get("corpus", ""),
        "config": {**cfg.resolved(), **CORPUS_STATS},
        "judge_model": judge,
        # ⭐ 顶层也存一份。`compare()` 要在读 metrics 之前就知道这一轮能不能比，
        # 而老结果里没有这个字段——那边会从 cases 现算，见 `_reliability`
        "reliable": bool(metrics.get("可信", True)),
        "metrics": metrics,
        "cases": [_slim(asdict(r)) for r in results],
    }
    path = RESULTS_DIR / f"{tag}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_report(
    tag: str, metrics: dict, results: list[CaseResult], judge: str, cfg: Config
) -> None:
    r = cfg.resolved()
    cfg_line = f"top_k={r['top_k']} rerank_k={r['rerank_k']} threshold={r['threshold']}"
    print()
    print("=" * 78)
    print(f"  {tag}    判分模型 {judge}    {cfg_line}")
    print("=" * 78)
    # ⭐ 判分口径先打出来，再打指标。**顺序是刻意的**：先让人看见这一轮
    # 有几题根本没评上，再去看准确率——反过来的话，第一眼看到的是
    # 一个被网络污染过的数字，而修正信息在下面第五行
    if metrics.get("有效题数") is not None:
        print(f"  {'题数':<12} {metrics['题数']}")
        print(f"  {'有效题数':<11} {metrics['有效题数']}    ← 准确率的分母")
        print(f"  {'判分失效':<11} {metrics['判分失效']}")
        print(f"  {'判对':<12} {metrics['判对']}")
        print(f"  {'判错':<12} {metrics['判错']}")
        print()
        if not metrics.get("可信", True):
            print(
                f"  【UNRELIABLE】：判分失效率 {metrics['判分失效率']}% > "
                f"{JUDGE_ERROR_LIMIT}%，这一轮**不能用来比较 prompt / 架构好坏**。"
            )
            print("     重跑一遍（判分是确定性的，答案已经存下来了，只是判分器当时连不上）。")
            print()
    for k in (
        "准确率",
        "判分失效率",
        "检索命中率",
        "引用正确率",
        "幻觉率",
        "假阴性率",
        "无据陈述率",
        "配图带出率",
    ):
        # ⚠️ 不是每一轮都有每一个指标。「配图带出率」的分母是标了 `procedural`
        # 的题，而**私有库那组一道都没有**——照着固定清单直接取值会 KeyError，
        # 而且是在指标都算完、报告打印到一半的时候崩，那一轮的 json 白跑。
        # 2026-08-20 私有库跑到 19 题时撞上的。
        if (v := metrics.get(k)) is None:
            continue
        unit = "" if k == "题数" else "%"
        print(f"  {k:<12} {v}{unit}")
    # 难题单独打一行。**改动的效果先看这个数**：老题在 v3 上已经饱和，
    # 总准确率会把 14 道难题的变化稀释成看不见的小数
    if "难题准确率" in metrics:
        print(f"  {'难题准确率':<11} {metrics['难题准确率']}%（{metrics['难题数']} 题）")
    print()
    print("  分类准确率：", "  ".join(f"{k} {v}%" for k, v in metrics["分类准确率"].items()))

    # ⚠️ **答错的和没判成的分开列。** 混在一张「没过的题」清单里，就是
    # 2026-08-20 那次误读的来源：5 条断线躺在失败列表里，读起来全像模型答错
    bad = [r for r in results if r.status == "incorrect"]
    print()
    print(f"  答错的 {len(bad)} 题：")
    for r in bad:
        print(f"    [{r.kind:9}] {r.id:32} {r.fail_why[:88]}")
    if stuck := [r for r in results if r.status == "invalid"]:
        print()
        print(f"  判分器没判成的 {len(stuck)} 题（**不是答错**，不计入准确率）：")
        for r in stuck:
            print(f"    [{r.kind:9}] {r.id:32} {r.reason[:88]}")
    ungrounded = [r for r in results if r.grounded is False]
    if ungrounded:
        print()
        print("  夹了材料不支持的说法：")
        for r in ungrounded:
            print(f"    {r.id:32} {r.unsupported[:100]}")


def _reliability(run: dict) -> tuple[bool, float, int]:
    """这一轮能不能拿来比较，判分失效率多少，几题没评上。

    ⚠️ **老结果里没有 `reliable` 这个字段，必须从 `cases` 现算。**
    M13 之前存下来的每一轮都是「judge_error 算答错」的口径，而那正是要防的
    误读——`m12-general-on` 那轮 61 题里 5 题断线（8.2%），照字面读会得出
    「放开常识让准确率掉了 6.6 个点」，其中 4 个点是网络。
    不现算的话，历史结果会永远以那个错误口径参与对比。
    """
    cases = run.get("cases") or []
    if not cases:
        return bool(run.get("reliable", True)), 0.0, 0
    stuck = sum(1 for c in cases if c.get("verdict") == "judge_error")
    rate = round(100.0 * stuck / len(cases), 1)
    return rate <= JUDGE_ERROR_LIMIT, rate, stuck


def compare(tags: list[str], allow_unreliable: bool = False) -> None:
    runs = []
    for t in tags:
        p = RESULTS_DIR / f"{t}.json"
        if not p.exists():
            sys.exit(f"没有这轮结果：{p}")
        runs.append(json.loads(p.read_text(encoding="utf-8")))

    # ⛔ 有一轮判分失效超线就不出对比表。**这是刻意挡掉一件事**：拿一轮
    # 被网络污染过的结果去判断「哪版更好」。差值可能整个落在那几题噪声里，
    # 而对比表长得一本正经，看的人不会去核每一题的 verdict
    if bad := [(r, _reliability(r)) for r in runs if not _reliability(r)[0]]:
        print()
        print("【UNRELIABLE】 —— 这几轮的判分失效率超过 5%：")
        for r, (_, rate, stuck) in bad:
            print(f"     {r['tag']:<22} {rate}%（{stuck} 题没评上）")
        print()
        print("  判分器断线不是模型答错。拿这样的结果比较 prompt / 架构，")
        print("  差出来的那几个点可能整个是网络。**先重跑，再比。**")
        if not allow_unreliable:
            print("  （真要看，加 --allow-unreliable。）")
            return
        print("  ⚠️ --allow-unreliable：下面的数字不作数，只当原始记录看。")

    keys = ["准确率", "判分失效率", "检索命中率", "引用正确率", "幻觉率", "假阴性率", "无据陈述率"]
    w = max(len(t) for t in tags) + 2

    print()
    print("| 指标 | " + " | ".join(f"{r['tag']}" for r in runs) + " |")
    print("|---|" + "---|" * len(runs))
    for k in keys:
        # ⚠️ 老结果里没有 M13 新加的那几个指标（判分失效率），打成「—」，
        # 别 KeyError 掉整张表——历史对比是这个命令唯一的用途
        base = runs[0]["metrics"].get(k)
        cells = []
        for r in runs:
            v = r["metrics"].get(k)
            if v is None:
                cells.append("—")
            elif r is runs[0] or base is None:
                cells.append(f"{v}%")
            else:
                cells.append(f"{v}% ({v - base:+.1f})")
        print(f"| {k} | " + " | ".join(cells) + " |")
    print()
    print("参数：")
    for r in runs:
        c = r["config"]
        print(
            f"  {r['tag']:<{w}} prompt={c.get('prompt', '?')}／{c.get('prompt_sha', '?')} "
            f"top_k={c['top_k']} rerank_k={c['rerank_k']} threshold={c['threshold']} "
            f"chunk={c['chunk_size']}/{c['chunk_overlap']} 块数={c.get('chunk_count', '?')}"
        )

    # 逐题变化：调参时真正有用的信息
    base_pass = {c["id"]: c["passed"] for c in runs[0]["cases"]}
    for r in runs[1:]:
        flips = [
            (c["id"], base_pass.get(c["id"]), c["passed"], c["fail_why"])
            for c in r["cases"]
            if base_pass.get(c["id"]) != c["passed"]
        ]
        if flips:
            print()
            print(f"相对 {runs[0]['tag']}，{r['tag']} 变化的题：")
            for cid, was, _now, why in flips:
                arrow = "过 → 没过" if was else "没过 → 过"
                print(f"  {cid:32} {arrow}  {why[:70]}")


# ---------- 只验检索 ----------


def check(cases: list[dict], cfg: Config, user_id=None) -> None:
    """不调 LLM，只看检索。用来验「评测集本身立不立得住」。

    两件事必须在这里发现，否则整套指标都是错的：
      1. fact 题的期望来源根本检索不到 → 那题在考检索，不是在考生成
      2. no_answer 题其实检索得到答案 → 模型答出来反而被判成幻觉
    """
    results = retrieve_all(cases, cfg, quiet=True, user_id=user_id)
    by_id = {c["id"]: c for c in cases}

    print()
    print("── 有期望来源的题：期望那篇在不在引用里 ──")
    miss = 0
    for r in results:
        if r.source_hit is None:
            continue
        wants = "／".join(wanted_sources(by_id[r.id]))
        if r.source_hit:
            print(f"  ✓ {r.id:32} 命中「{wants}」")
        else:
            miss += 1
            print(f"  ✗ {r.id:32} 想要「{wants}」，实际召回：")
            for t in r.retrieved_titles[:5]:
                print(f"        {t}")

    print()
    print("── no_answer 题：召回了什么、分数多高（人工过一眼有没有真答案）──")
    for r in results:
        if r.kind != "no_answer":
            continue
        top = f"{r.top_score:.4f}" if r.citations else "  —  "
        print(f"  {r.id:26} 最高分 {top}  {r.retrieved_titles[:3]}")

    print()
    print(f"期望来源未命中 {miss} 题。no_answer 题若最高分明显偏高，就要人工确认一下。")


# ---------- CLI ----------


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库 Agent 评测")
    ap.add_argument("--tag", default="", help="这轮的名字，结果存 results/<tag>.json")
    ap.add_argument("--check", action="store_true", help="只验检索，不调 LLM")
    ap.add_argument("--compare", nargs="+", metavar="TAG", help="对比若干轮结果")
    ap.add_argument(
        "--allow-unreliable",
        action="store_true",
        help="判分失效率超线时仍然打印对比表（默认拒绝，见 compare()）",
    )
    ap.add_argument("--only", default="", help="只跑指定 id 或 kind，逗号分隔")
    # ⭐ 常识兜底的 A/B（M12）。**必须能在同一次运行里指定**，
    # 而不是靠改 .env 再跑一遍：改 .env 那种做法下，两轮之间除了这个开关
    # 还可能悄悄差别的东西（谁记得中途有没有动过别的？），
    # 而这一改动推翻的是铁律 1，值得一次干净的 A/B。
    ap.add_argument(
        "--general",
        choices=("on", "off"),
        default="",
        help="常识兜底开/关。不传则读 .env 的 ALLOW_GENERAL_KNOWLEDGE",
    )
    ap.add_argument("--top-k", type=int, default=0)
    ap.add_argument("--rerank-k", type=int, default=0)
    ap.add_argument("--threshold", type=float, default=-1.0)
    ap.add_argument(
        "--prompt", default="current", help="用哪版 system prompt（见 eval/prompts.py）"
    )
    ap.add_argument(
        "--agent", action="store_true", help="评 M7 的 Agent 路径而不是直路"
    )
    ap.add_argument(
        "--mode",
        default="fast",
        choices=["fast", "deep"],
        help="回答档位：fast 简答（DeepSeek）/ deep 详解（Kimi）",
    )
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument(
        "--as-user",
        default="",
        metavar="EMAIL",
        help="按这个用户的可见范围跑私有库那组题（见 eval/private/README.md）",
    )
    args = ap.parse_args()

    if args.compare:
        compare(args.compare, allow_unreliable=args.allow_unreliable)
        return

    # 指定了用户就跑 private 那组题，否则跑 public。两组题不混跑——
    # 混跑等于把两个不同的题集算进同一个准确率
    user_id = resolve_user(args.as_user) if args.as_user else None
    meta, cases = load_cases(args.only or None, scope="private" if user_id else "public")
    if not cases:
        raise SystemExit("这个范围里一道题都没有，检查 --only / --as-user")
    cfg = Config(
        top_k=args.top_k,
        rerank_k=args.rerank_k,
        threshold=args.threshold,
        prompt=args.prompt,
        agent=args.agent,
        mode=args.mode,
        general={"on": True, "off": False}.get(args.general),
    )

    if args.check:
        check(cases, cfg, user_id=user_id)
        return

    tag = args.tag or datetime.now().strftime("run-%m%d-%H%M")
    t0 = time.monotonic()
    print(f"评测 {len(cases)} 题　参数 {cfg.resolved()}")
    if user_id:
        print(f"可见范围：公共库 + {args.as_user} 的私有库")
    if cfg.agent:
        # Agent 自己决定检索几次，所以「检索」和「生成」分不开，只能一起跑
        print("── Agent 逐题跑（检索由它自己决定）──")
        results = run_agent_cases(cases, cfg, user_id=user_id)
    else:
        print("── 检索（串行，受 SiliconFlow 限速）──")
        results = retrieve_all(cases, cfg, user_id=user_id)
        print("── 生成答案 ──")
        answer_all(
            results,
            workers=args.workers,
            system_prompt=cfg.system_prompt(),
            mode=cfg.mode,
            general=cfg.general,
        )
    print("── 判分 ──")
    judge = judge_all(results, cases, workers=args.workers)

    metrics = score(results, cases)
    path = save(tag, meta, cfg, metrics, results, judge)
    print_report(tag, metrics, results, judge, cfg)
    print()
    print(f"耗时 {time.monotonic() - t0:.0f}s　结果存在 {path}")


if __name__ == "__main__":
    main()
