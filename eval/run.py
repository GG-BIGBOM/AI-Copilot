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
import dataclasses
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

# 评测默认量哪个知识版本（M19-A）。字面量而不是 `copilot.spaces.DEFAULT`，
# 是为了让判分口径那一层的纯函数测试不必连库、不必装后端依赖；
# 两者一致由 `tests/test_eval_spaces.py` 钉死——写死一个字符串而没人核对，
# 正是 M14-A 那个 NOT NULL 洞的同一种形状。
DEFAULT_SPACE = "flagship"

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

# ⚠️ 第二种假阳性：**排除句在被禁串的后面**（2026-08-23 风险边界实测）。
# `st-cancel-release-stock` 的答案先照材料讲了 JIT 的释放规则、点明出处，
# 紧接着写「**但普通淘宝订单不适用此规则**」——这正是铁律 8 要的答法
# （把别家的规则摆出来并划清界限），却因为「夜间定时任务」这个禁串出现在
# 排除句之前，被判成串场景。往前看三个字的窗口够不着它。
#
# 所以再看一眼**后面**：被禁串之后不远处出现「不适用 / 并非 / 无关 / 仅适用于」
# 这类划界说法，就认为它是被点名排除的、不是被拿来当答案的。
#
# ⚠️ 这仍然是近似，方向和 `_NEGATORS` 一致：**宁可漏抓一个绕着圈说的违规，
# 也不能把「说清它不适用」这种正确答法判成违规**——那会逼着模型闭嘴，
# 而闭嘴恰恰是这套指标最容易被骗过去的失败形态。
_EXCLUDERS = (
    "不适用",
    "不适用于",
    "并非",
    "并不适用",
    "不是这个",
    "无关",
    "不针对",
    "不涉及",
    "仅适用于",
    "只适用于",
    "规则不同",
    "不同于",
    "此处不适用",
)
# 划界的话可能在被禁串**前**也可能在**后**，两边都要看：
#     后：「……夜间定时任务自动释放。[1] 但普通淘宝订单不适用此规则」
#     前：「JIT 实时订单的库存释放**规则不同**——……由夜间定时任务释放」
# 一句这样的论述大约 30 字，留一倍余量；再远就不是同一处论述了
_EXCLUDE_WINDOW = 60


def missing_facts(answer: str, wanted: list) -> list[str]:
    """`must_include` 里没在答案中出现的那几条。

    一条可以写成**一组同义写法**（列表），命中其中任意一个就算数：

        must_include: ['快速入库', ['批量入库', '批量采购入库', '采购批量入库']]

    ⚠️ 为什么需要它：2026-08-23 `proc-purchase-inbound-ways` 判错，
    要的是「批量入库」，而答案写的是「**批量采购入库**」、语料原文的标题是
    「采购**批量**入库」——三种词序说的是同一件事，裸子串匹配只认一种。
    这道题量的是「有没有漏掉这条入库方式」，不是「词序对不对」。

    ⚠️ 它**不是**用来放宽事实判定的：数字、界面路径这类仍然写死一个字串，
    「答案换个说法也算对」正是这份题集最不想要的松动。只有**同一个功能名的
    不同词序**才该写成一组。
    """
    out: list[str] = []
    low = answer.lower()
    for want in wanted or []:
        variants = [want] if isinstance(want, str) else list(want)
        if not any(v.lower() in low for v in variants if v):
            out.append(variants[0])
    return out


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

    第二种假阳性（排除句在**后面**）见 `_EXCLUDERS` 上面那段。
    ⚠️ 它同样是近似，且**分不开**这一种：编完一个数字，紧跟着写一句
    「另一家的规则不适用」。真遇上了要靠出题——把禁串写得具体一点
    （带数字、带平台名），而不是在这里继续堆规则。
    """
    hits: list[str] = []
    low = answer.lower()
    for term in banned:
        t = term.lower()
        if not t:
            continue
        start = 0
        while (i := low.find(t, start)) >= 0:
            negated_before = bool(set(answer[max(0, i - _NEG_WINDOW) : i]) & set(_NEGATORS))
            near = low[max(0, i - _EXCLUDE_WINDOW) : i + len(t) + _EXCLUDE_WINDOW]
            excluded = any(x in near for x in _EXCLUDERS)
            if not negated_before and not excluded:
                hits.append(term)
                break
            start = i + 1
    return hits


# ---------- 数据结构 ----------


def _guard_suffix(mode: str, general: bool | None = None) -> str:
    """按问题形状追加的那几段文本：主体约束（M11 P3）和定义题追加段（2026-08-23）。

    ⚠️ **拿 `system_prompt_for` 算差值，不要自己抄一份。**
    那段话只该有一个出处；抄一份的话，改了线上那份而评测还在用旧的，
    评测就会一直报告一个早就不存在的系统。

    ⚠️⚠️ **两段都要算进指纹。** 它们不在 `prompt_text` 里（只有命中的那一轮
    才会被拼上去），漏掉哪一段，改了它的两轮就会存出一模一样的 sha——
    2026-08-20 私有库那四轮就是这么丢掉唯一变过的东西的。
    """
    return _subject_suffix(mode, general) + _definition_suffix(mode, general)


def _subject_suffix(mode: str, general: bool | None = None) -> str:
    """主体约束那一段（M11 P3 第 3 步）。"""
    from copilot.qa import system_prompt_for

    base = system_prompt_for(mode, general=general)
    return system_prompt_for(mode, subject_guard=True, general=general)[len(base) :]


def _definition_suffix(mode: str, general: bool | None = None) -> str:
    """定义题追加段（2026-08-23，原铁律 9）。"""
    from copilot.qa import system_prompt_for

    base = system_prompt_for(mode, general=general)
    return system_prompt_for(mode, general=general, definition=True)[len(base) :]


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
    # ⭐ 这一轮在哪个知识版本里问（M19-A）。在它之前，检索那两支都写死
    # `spaces.default_id()`——评测因此**只能**量旗舰版，而"企业版语料导进去
    # 会不会污染旗舰版"这个问题，在导入之前一次都问不出来。
    # 它同时是结果档案的一部分（见 `resolved()`）：两轮结果只有空间不同时，
    # 存出来的 config 必须看得出来，否则 `--compare` 会拿两个题集比大小。
    space: str = DEFAULT_SPACE
    # 这一轮跑的是哪份题集（W1.2）。**必须进 `resolved()`**，
    # 理由和 `space` 一模一样：两轮结果只有题集不同时，存出来的 config
    # 看不出来的话，`--compare` 会拿两个题集比大小而报告上一个字都不提
    dataset: str = "dataset.yaml"
    # 混合检索开关（W1.2）。⚠️ 这是**改了会让检索结果变、但不会报错**的那类开关，
    # 所以它必须出现在结果档案里——不然 hybrid 那一轮和 baseline 那一轮
    # 存出来的 config 完全相同，而两者的数字不一样，没有任何办法解释
    hybrid: bool | None = None

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
            "space": self.space,
            "dataset": self.dataset,
            "hybrid": s.hybrid_enabled if self.hybrid is None else self.hybrid,
            "top_k": self.top_k or s.retrieve_top_k,
            "rerank_k": self.rerank_k or s.rerank_top_k,
            "threshold": s.rerank_score_threshold if self.threshold < 0 else self.threshold,
            "chunk_size": s.chunk_size,
            "chunk_overlap": s.chunk_overlap,
            "embedding_model": s.embedding_model,
            "rerank_model": s.rerank_model,
            "mode": self.mode,
            "answer_model": s.llm_deep_model if self.mode == "deep" else s.llm_model,
            "general": self.general,
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
    # ⭐ 这一轮问的是哪个空间，以及召回里有几块**不属于**这个空间（也不属于
    # `common`）。后者的正确值永远是 0——不是"通常是 0"，是**破了就不能上线**：
    # 一块企业版的步骤混进旗舰版的答案里，用户照着点会把单据做错，
    # 而答案长着有出处的样子，他分辨不出来。见 `eval/cross_space.py`
    space: str = ""
    foreign_space_hits: int = 0
    # 这一轮上下文里真实存在的配图：[{"n": 1, "url": ..., "title": 出自哪篇}]。
    # 有了它，「答案写的 [图3] 到底存不存在」才是**规则判定**——
    # 在此之前只数得出答案里有几个 [图N]（`_count_pics`），
    # 数得出用了几张，却看不出用的是不是真的那几张
    context_images: list[dict] = field(default_factory=list)

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
    # 答案里写了、而上下文里根本没有的配图编号。**这是配图版的"假引用"**：
    # 用户点开一张不存在的图，看到的是坏掉的图标；更糟的情形是编号存在
    # 但指向另一篇文档的截图（下面那一列），那时他看到的是一张
    # **看起来合理、其实是另一个平台**的界面
    bad_image_refs: list[int] = field(default_factory=list)
    # 编号存在，但那张图出自期望来源以外的文档（只在题目声明了 `source` 时判）
    foreign_image_refs: list[int] = field(default_factory=list)

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


# 带编号的版本：`[图3]` -> 3。`_PIC_RE` 只数个数，这里要的是编号本身
_PIC_N_RE = re.compile(r"\[图\s*(\d{1,2})\]")


def cited_pics(answer: str) -> list[int]:
    """答案里引用到的配图编号，按出现顺序、去重。"""
    seen: list[int] = []
    for m in _PIC_N_RE.findall(answer or ""):
        n = int(m)
        if n not in seen:
            seen.append(n)
    return seen


def _image_table(res, bundle) -> list[dict]:
    """把 `bundle.images`（编号 + 地址）配上「这张图出自哪篇」。

    编号是 `build_context()` 现编的（按在上下文里首次出现的顺序），
    出处只有检索结果里才有——两边必须在同一处对上，否则
    `foreign_image_refs` 判的就是一张对不上号的表。
    """
    titles = {
        img["url"]: c.citation.title
        for c in res.chunks
        for img in c.images
        if img.get("url")
    }
    return [{**img, "title": titles.get(img.get("url"), "")} for img in bundle.images]


def bad_image_refs(answer: str, images: list[dict]) -> list[int]:
    """答案里写了、上下文里却没有的配图编号（M19-A）。

    ⚠️ **上下文一张图都没有时，任何 `[图N]` 都是编的。** 第一版把这种情形
    直接返回空列表（"没有图就没什么可比的"），于是最该抓住的那类失败——
    模型在完全没有配图的材料上凭空写出 `[图1]`——反而恒判为通过。
    """
    have = {img.get("n") for img in images or []}
    return [n for n in cited_pics(answer) if n not in have]


def cited_titles(answer: str, citations: list[dict]) -> set[str]:
    """答案正文里 `[n]` 真的引用到的那几篇的标题。

    ⚠️ 是**引用到的**，不是"召回的"。召回 5 篇、正文只引了 2 篇是常态，
    而用户能溯源的只有正文里带编号的那 2 篇。
    """
    used = {int(n) for n in _CITE_RE.findall(answer or "")}
    return {c.get("title") or "" for c in citations or [] if c.get("n") in used} - {""}


def foreign_image_refs(answer: str, images: list[dict], titles: set[str]) -> list[int]:
    """配图出自**答案自己没有引用过**的那几篇的编号（M19-A）。

    量的是「配图串台」：编号真实存在、图也打得开，但用户**无处可考**——
    正文里没有任何一个 `[n]` 指向这张图所在的那篇文档。他看到一张
    界面截图，却查不到它是哪一篇里的哪一步。

    ⚠️⚠️ **第一版的判据是错的，量出来的 40% 是假的。**
    那一版判的是「图出自题目声明的期望来源（`source`）以外的文档」。
    在 75 题上量出 15 题可判、6 题"串台"——逐条看下去**一条真的都没有**：

        proc-purchase-settle   图5 出自《账款 · 应收应付》，而正文写的是
                               「生成一条对应的应付单 [2][图5]」，[2] 正是那一篇

    答案本来就跨文档作答，图出自它引用的另一篇，编号和引用严丝合缝。
    期望来源是**出题人标的"该命中哪一篇"**，从来不是"只许用这一篇的图"。
    按新判据重算，同一批结果是 0/15——而这才是真话。

    留下这段记录是因为：一个天天误报 40% 的指标，比没有这个指标更糟——
    人会学会忽略它，然后连真的那一次也一起忽略。

    `title` 未知的图（Agent 路上 `deps.images` 没有出处；私有图的地址被换成
    `/api/images/{id}`，对不回文档）一律**不算串台**：宁可漏判，
    也不能凭"我不知道它是谁的"给系统记一笔错。
    """
    if not titles:
        return []
    by_n = {img.get("n"): img for img in images or []}
    out: list[int] = []
    for n in cited_pics(answer):
        img = by_n.get(n)
        if img is None:
            continue  # 不存在的编号是 bad_image_refs 的事，不重复计一次
        title = img.get("title")
        if not title:
            continue
        if title not in titles:
            out.append(n)
    return out


def load_cases(
    only: str | None = None,
    scope: str = "public",
    space: str = DEFAULT_SPACE,
    dataset: Path | None = None,
) -> tuple[dict, list[dict]]:
    """读题集。`dataset` 留空就是 `dataset.yaml`（W1.2 起可以换成别的子集）。

    ⚠️ **换题集必须换文件，不能往主题集里加一个新 `kind`。**
    `load_cases` 默认把公共库 flagship 的题全取出来——往 `dataset.yaml` 里
    塞 30 道关键词题，等于把 75 题的基线变成 105 题的基线，
    而历史 tag 之间的 `--compare` 会安安静静地变成"拿两个不同的题集比大小"。
    这正是 `scope` 那一行注释在防的事，只是这次的轴是"哪一批题"。
    """
    import yaml

    data = yaml.safe_load((dataset or DATASET).read_text(encoding="utf-8"))
    # ⭐ scope 默认只取公共库的题。**这条不能省**：私有库的题打的是别人的
    # 文档集，混进来会让历史 tag 之间的 --compare 变成拿两个不同的题集比大小，
    # 而报告上完全看不出来
    cases = [c for c in data["cases"] if c.get("scope", "public") == scope]
    # 同理，空间也是题集的一根轴（M19-A）。题目不写 `space` 就是旗舰版——
    # 现有 89 道题一道都不用改，而 M18 之后新加的企业版题不会混进旗舰版的分母
    cases = [c for c in cases if c.get("space", DEFAULT_SPACE) == space]
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


async def _with_fresh_pool(coro):
    """跑完一轮就把连接池丢掉。

    ⚠️ **一个进程里第二次 `asyncio.run()` 会踩到上一轮留下的连接。**
    连接池里那条 asyncpg 连接还绑在已经关掉的事件循环上，下一轮复用它时
    报的是 `AttributeError: 'NoneType' object has no attribute 'send'`——
    一个和数据库、和评测都八竿子打不着的错误，排查方向全歪。
    `resolve_user` 早就为同样的理由 dispose 过一次；`retrieve_all` 一直漏着，
    直到跨空间评测要按空间分组、连着跑好几轮才撞上来。
    """
    from copilot.db.session import engine

    try:
        return await coro
    finally:
        await engine.dispose()


CORPUS_STATS: dict = {}  # 检索时顺手记下当时的块数，换 chunk 参数重灌后能看出规模变化


async def resolve_space(session, code: str):
    """把空间 code 换成行。找不到就退出，**不回落到默认空间**（M19-A）。

    回落是这里最坏的选项：`--space enterprise_desktp` 拼错一个字母，
    评测会安安静静地又量一遍旗舰版，而报告标题上写着企业版——
    正是 `copilot.spaces.SpaceNotFound` 那段注释说的"没有任何症状的错误"。
    """
    from copilot import spaces

    try:
        return await spaces.by_code(session, code)
    except spaces.SpaceNotFound as e:
        raise SystemExit(f"{e}。可选：{'、'.join(c for c, *_ in spaces.SEED)}") from e


async def corpus_fingerprint(session, space_id, common_id, user_id=None) -> dict:
    """这一轮**实际能检索到**的那批块的指纹（M19-A）。

    结果档案里原来只有一个 `chunk_count`，而它答不了两个问题：
    「这两轮跑的是不是同一份语料」和「这份门禁证据是不是已经过期了」。
    块数相同、内容变了（勘误改了一句话、重灌了一次）时它一动不动。

    ⚠️ **过滤条件直接复用检索自己的 `_space_filter`**，不在这里抄一份。
    指纹要覆盖的是"检索能看见的那批块"，抄一份的话，哪天空间过滤改了规则，
    指纹会继续按老规则算——门禁于是拿着一份**它以为对应、其实不对应**的
    语料快照放行，那比没有指纹更糟。
    """
    from sqlalchemy import String, func, literal, or_, select
    from sqlalchemy.dialects.postgresql import aggregate_order_by

    from copilot.db.models import Chunk
    from copilot.retrieve import _space_filter

    scope = (
        Chunk.owner_id.is_(None)
        if user_id is None
        else or_(Chunk.owner_id.is_(None), Chunk.owner_id == user_id)
    )
    where = _space_filter(space_id, common_id) & scope
    n = await session.scalar(select(func.count(Chunk.id)).where(where))
    # 逐块的 (id, 正文 md5) 排序后再哈希：改一个字、少一块、多一块都会变。
    # 在库里算，不是把 5000 块正文拉回本机——那是一次几十兆的传输
    # ⚠️ **排序必须写在 `string_agg` 里**（`... ORDER BY id`）。
    # 不排的话 Postgres 按它当时高兴的顺序拼，同一份语料能算出两个不同的
    # 指纹——一个会随机报"语料变了"的指纹，比没有指纹更浪费人的时间
    line = func.concat(func.cast(Chunk.id, String), ":", func.md5(Chunk.content))
    digest = await session.scalar(
        select(func.md5(func.string_agg(line, aggregate_order_by(literal(","), Chunk.id)))).where(
            where
        )
    )
    return {"chunk_count": n or 0, "corpus_sha": (digest or "")[:12]}


def retrieve_all(
    cases: list[dict], cfg: Config, quiet: bool = False, user_id=None
) -> list[CaseResult]:
    import asyncio

    from copilot.db.session import SessionLocal
    from copilot.providers.siliconflow import (
        SiliconFlowClient,
        SiliconFlowEmbedder,
        SiliconFlowReranker,
    )
    from copilot.qa import asks_about_named_subject, needs_subject_guard
    from copilot.retrieve import RetrievalResult, search

    r = cfg.resolved()

    async def main() -> list[CaseResult]:
        client = SiliconFlowClient()
        emb, rr = SiliconFlowEmbedder(client=client), SiliconFlowReranker(client=client)
        out: list[CaseResult] = []
        async with SessionLocal() as session:
            # ⚠️ 知识版本（M19-A 起是参数，不再写死旗舰版）。不传的话检索是
            # fail closed 的（一条都不返回），整份题集会全变成
            # 「知识库暂无此内容」，而那看起来像模型退化了。
            from copilot import spaces

            space_id = (await resolve_space(session, cfg.space)).id
            common_id = await spaces.common_id(session)
            # 「这一块算不算串了空间」的判据。**`common` 要算在里面**：
            # 通用知识本来就该在任何版本里被召回（见 `copilot.spaces` 模块头），
            # 漏掉它的话，跨空间污染率会把一批完全正确的召回记成污染
            allowed = {x for x in (space_id, common_id) if x is not None}

            # 可见范围 = 公共库 +（指定用户时）他的私有库，和线上完全一致
            CORPUS_STATS.update(
                await corpus_fingerprint(session, space_id, common_id, user_id)
            )
            for i, case in enumerate(cases, 1):
                res = await search(
                    session,
                    case["q"],
                    emb,
                    rr,
                    user_id=user_id,  # None = 只打公共库
                    space_id=space_id,
                    top_k=r["top_k"],
                    rerank_k=r["rerank_k"],
                    score_threshold=r["threshold"],
                )
                # ⭐ 和 `ask_stream` 里同一道闸门：主体约束触发、且问题点名了
                # 第三方主体时，**公共材料整块拿掉**，只留他自己的文档。
                # 评测这边是两阶段跑（检索一次、生成一次），所以要在这里复现——
                # 漏了它，私有库那组题量的就不是线上那条路（2026-08-23 踩到：
                # 生产改完，评测数字一动不动）。判定函数从 `copilot.qa` 引，
                # 不在这里抄一份。
                guard_now = await needs_subject_guard(session, case["q"], user_id)
                if guard_now and res.private_count and asks_about_named_subject(case["q"]):
                    res = RetrievalResult(chunks=[c for c in res.chunks if c.private])
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
                    space=cfg.space,
                    # ⭐ 逐块核对空间，而不是相信过滤器。见 `RetrievedChunk.space_id`
                    foreign_space_hits=sum(
                        1 for c in res.chunks if c.space_id not in allowed
                    ),
                    context_images=_image_table(res, bundle),
                )
                # M11 P3 第 3 步。**调的是线上那个函数**，不是抄一份判定逻辑——
                # 抄一份的话，改了那边忘了改这边，评测会一直在报告一个
                # 早就不存在的系统，而私有库这组题量的恰恰就是这条规则
                cr.subject_guard = guard_now
                if wants := wanted_sources(case):
                    cr.source_hit = any(w in t for w in wants for t in cr.retrieved_titles)
                out.append(cr)
                if not quiet:
                    flag = "" if cr.source_hit is None else ("命中" if cr.source_hit else "未中")
                    print(f"  [{i:2}/{len(cases)}] {case['id']:34} {flag}")
        client.close()
        return out

    return asyncio.run(_with_fresh_pool(main()))


# ---------- 阶段二：生成答案（并行） ----------


def answer_all(
    results: list[CaseResult],
    workers: int = 5,
    quiet: bool = False,
    system_prompt: str | None = None,
    mode: str = "fast",
    general: bool | None = None,
    fenced: bool = False,
) -> None:
    """生成答案。

    `fenced`（W2.3）：材料区用围栏包起来。⚠️ **调用方必须让它和 system prompt
    里那段注入防线同开同关**——规则里写着「边界只有那两个标记」，
    而不加围栏时那两个标记根本不存在。`risk_boundary.py` 是唯一的调用方，
    它用同一个 `--guard` 决定这两样。
    """
    from copilot.config import get_settings
    from copilot.providers.llm import ChatLLM
    from copilot.qa import (
        EMPTY_CONTEXT,
        FENCED_USER_TEMPLATE,
        NO_ANSWER,
        SYSTEM_PROMPT,
        USER_TEMPLATE,
        is_definition_question,
        is_no_answer,
    )

    # 常识兜底开着时，「一条都没召回」不再是免费的兜底话术——那正是最该问
    # 一次模型的时候。留 None 就读 .env，和线上一致
    if general is None:
        general = get_settings().allow_general_knowledge

    # 允许换 prompt：A/B 时必须拿**同一份评测集**跑两个 prompt，
    # 否则指标的变化归不了因（见 eval/prompts.py 的说明）
    prompt = system_prompt or SYSTEM_PROMPT
    # M11 P3 第 3 步的那一段附加约束（只加在标了 subject_guard 的题上）
    guarded_suffix = _subject_suffix(mode, general)
    # 定义题追加段（2026-08-23）。同样是按问题形状开的，评测漏了它就等于
    # 在定义题上跑了另一版 prompt
    definition_suffix = _definition_suffix(mode, general)

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
            user_msg = (FENCED_USER_TEMPLATE if fenced else USER_TEMPLATE).format(
                context=cr.context or EMPTY_CONTEXT, question=cr.q
            )
            system = prompt
            if cr.subject_guard:
                system += guarded_suffix
            if is_definition_question(cr.q):
                system += definition_suffix
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

    from copilot.agent.deps import AgentDeps
    from copilot.agent.runner import run_agent_stream
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
            # 同直路那一支：空间是参数，不传就是 fail closed
            from copilot import spaces

            space_id = (await resolve_space(session, cfg.space)).id
            common_id = await spaces.common_id(session)

            CORPUS_STATS.update(
                await corpus_fingerprint(session, space_id, common_id, user_id)
            )
            for i, case in enumerate(cases, 1):
                deps = AgentDeps(
                    space_id=space_id,
                    session=session,
                    # 公共库用随机用户保持可见范围为公共库；私有评测必须沿用
                    # `--as-user` 解析出的 ID，否则私有文档永远不会被检索到。
                    user_id=user_id or uuid.uuid4(),
                    conversation_id=uuid.uuid4(),
                    embedder=emb,
                    reranker=rr,
                    llm=answer_llm,
                    general=cfg.general,
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
                    space=cfg.space,
                    # ⚠️ Agent 路上 `deps.images` 只有编号和地址，**没有出处**
                    # （见 `agent/tools.py` 的 answer_kb）。所以这一路能判
                    # 「编号存不存在」，判不了「串没串台」——`title` 留空，
                    # `foreign_image_refs` 会因此跳过它，而不是猜。
                    # 报告里那一行会连分母一起打出来，别把小分母看成干净
                    context_images=[dict(img) for img in deps.images],
                )
                if wants := wanted_sources(case):
                    cr.source_hit = any(w in t for w in wants for t in cr.retrieved_titles)
                out.append(cr)
                flag = "" if cr.source_hit is None else ("命中" if cr.source_hit else "未命中")
                print(f"  [{i:2}/{len(cases)}] {case['id']:34} {flag}")
        client.close()
        answer_llm.close()
        return out

    return asyncio.run(_with_fresh_pool(main()))


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


JUDGE_UNAVAILABLE = "judge_unavailable"


def judge_all(
    results: list[CaseResult],
    cases: list[dict],
    workers: int = 5,
    quiet: bool = False,
    skip: bool = False,
) -> str:
    """判分。`skip=True` 时只做确定性判定，语义判分一律记「没判成」。

    ⭐ **`--no-judge` 不是「不判分也能过」，恰恰相反。** 判分器欠费/断网时，
    准确率、幻觉率这些要看语义的指标本来就量不出来；照旧跑只会得到一堆
    被重试拖慢的 429，再被误读成模型退化（M13 P0 那一段说的就是这件事）。
    所以这里把它们如实标成 `judge_error` —— `score()` 会因此把
    判分失效率顶到 100%、`可信` 打成 false，整轮结果**不能**用来比较好坏。

    留下来的是三条**规则判定**的发布红线：该拒答有没有拒答、有没有编来源
    编号、有没有串别家的规则。它们只看答案文本，判分器在不在场都成立。
    """
    from copilot.config import get_settings
    from copilot.providers.llm import ChatLLM

    if skip:
        by_id = {c["id"]: c for c in cases}
        for cr in results:
            case = by_id[cr.id]
            cr.missing_facts = missing_facts(cr.answer, case.get("must_include") or [])
            cr.banned_hits = banned_hits(cr.answer, case.get("must_not_include") or [])
            if cr.banned_hits:
                cr.unsupported = f"出现了禁止内容：{cr.banned_hits}"
            if cr.said_no_answer:
                cr.verdict, cr.grounded, cr.reason = "no_answer", True, "答案是兜底话术"
                continue
            cr.verdict = JUDGE_UNAVAILABLE
            cr.judge_error = True
            cr.reason = "判分器不可用（--no-judge）：这一轮只有规则判定有效"
        if not quiet:
            print("  ⚠️ --no-judge：语义判分全部记为「没判成」，本轮不可用于比较")
        return ""

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
        # ⭐ 先清掉上一轮的判分结论。判分**是可以重跑的**（同一份答案、同一个
        # prompt），而重跑一次却留着上次的 `judge_error=True`，会让一条判成功的
        # 结果继续被算成"没判成"——分母悄悄少一题，而报告上看不出来
        cr.judge_error = False
        cr.verdict = ""
        # 确定性判定先做完，判分器不参与这部分
        cr.missing_facts = missing_facts(cr.answer, case.get("must_include") or [])
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

        # 配图的两条规则判定（M19-A）。放在三态之前算：下面「无效配图」
        # 要和漏事实、禁止内容一样算**确定性失败**，判分器挂了也照样成立
        cr.bad_image_refs = bad_image_refs(cr.answer, cr.context_images)
        cr.foreign_image_refs = foreign_image_refs(
            cr.answer, cr.context_images, cited_titles(cr.answer, cr.citations)
        )

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
        elif cr.bad_image_refs:
            # ⭐ 编出一个不存在的配图编号 = 配图版的假引用，和「漏事实」同级。
            # 不能只记进指标不判错：那样一道正文全对、配图指向空气的答案
            # 会计进准确率，而用户点开看到的是一张打不开的图
            cr.status = "incorrect"
            cr.fail_why = f"引用了不存在的配图：{cr.bad_image_refs}"
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
    # ⭐ 跨空间污染率（M19-A）。分母是全部题，不剔 invalid——它是逐块核对
    # `knowledge_space_id` 得来的，和判分器一点关系都没有。
    # **目标 0%，破了不能上线**：一块别的 ERP 版本的材料进了答案，
    # 用户照着做会把单据做错，而答案看起来完全正常
    m["跨空间污染率"] = pct(sum(r.foreign_space_hits > 0 for r in results), len(results))
    m["跨空间污染块数"] = sum(r.foreign_space_hits for r in results)

    # 配图的两条负例。分母是**真的写了 [图N] 的答案**——没配图的题
    # 谈不上配错，混进分母只会把这两个数稀释成永远好看的小数
    with_pics = [r for r in results if cited_pics(r.answer)]
    if with_pics:
        m["无效配图率"] = pct(sum(bool(r.bad_image_refs) for r in with_pics), len(with_pics))
        m["带图答案数"] = len(with_pics)
    # 串台只在「答案里有 [n] 引用」且「用到的图对得回出处」时判得了。
    # ⚠️ 分母单独打出来（`可判串台数`）：Agent 路上图片没有出处，
    # 分母会缩到很小甚至 0——那时 0.0% 意味着"没量到"，不是"没串台"
    judgeable = [
        r
        for r in with_pics
        if cited_titles(r.answer, r.citations)
        and any(i.get("title") for i in r.context_images if i.get("n") in cited_pics(r.answer))
    ]
    m["可判串台数"] = len(judgeable)
    if judgeable:
        m["配图串台率"] = pct(sum(bool(r.foreign_image_refs) for r in judgeable), len(judgeable))

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
    "跨空间污染率": "召回里出现了**不属于本轮空间、也不属于 common** 的块的题占比。"
    "逐块核对 `knowledge_space_id` 得来，与判分器无关。**目标 0%，是 M18 导入"
    "企业版语料的门禁**：这个数不是 0，就说明空间过滤有洞，而洞的表现是"
    "另一个 ERP 版本的步骤混进答案，没有任何报错",
    "无效配图率": "写了 [图N] 的答案里，N 在上下文里根本不存在的比例。"
    "**配图版的假引用**，目标 0%。规则判定，判分器挂了也算得出",
    "配图串台率": "配图编号真实存在、但那张图出自**答案自己没引用过**的文档的比例"
    "（用户无处可考）。"
    "这类错误在准确率/引用正确率/配图带出率上**全部隐形**，而用户照着"
    "另一个平台的界面去点。⚠️ 看它必须连 `可判串台数` 一起看："
    "Agent 路上图片没有出处，分母会是 0，那时 0.0% 是「没量到」不是「没串台」",
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


def save(
    tag: str,
    meta: dict,
    cfg: Config,
    metrics: dict,
    results: list[CaseResult],
    judge: str,
    scope: str = "public",
):
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {
        "tag": tag,
        # ⭐ 门禁（`eval/gate.py`）靠这两个字段认出「这份证据是哪一套题跑的」。
        # 在它们之前，只能靠文件名猜——而文件名是人随手起的
        "suite": "dataset",
        "scope": scope,
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


def rescore(tag: str) -> None:
    """拿已经存下来的答案，用**现在这版判分口径**重算一遍指标。

    ⭐ 判分口径会改（`配图串台率` 的判据 2026-08-24 就整个换过一次），而
    重跑一次全量是两百多次付费调用。答案、引用、配图对照表都在结果文件里，
    `score()` 又是纯函数——重算不需要再问任何一次模型。

    ⚠️ **它覆盖同一个 tag 的文件，但不动任何一个"答案"**：改的只有派生指标，
    并写下 `rescored_at`。这不是篡改证据——证据是答案，指标是从证据算出来的
    结论，口径变了结论就该跟着变。原来的数字在 git 里留着。

    ⚠️ 用的是**今天的** `dataset.yaml`。题集删过题的话，那几条结果会被丢掉
    （score 要用题目上的 `must_include` / `procedural` 这些判据），
    丢了几条会打出来——别让它悄悄地把分母变小。

    ⚠️ **没有 `--rejudge`，而且不该有。** 判分器要看「参考材料」，而存档时
    `context` 被 `_slim` 剥掉了（5 块材料约 2500 字 × 75 题，存下来就是把语料
    复制一份进版本库）。拿一份空材料去重判，判分器会一律给出「材料里没有」——
    **一个看起来判过、其实全错的结果**，比标着 UNRELIABLE 糟得多。
    判分器欠费/断线时的正确做法是充值之后**把那一套重跑一遍**（私有库 19 题
    约两分钟），让检索、答案、判分在同一份材料上重新对齐。
    """
    path = RESULTS_DIR / f"{tag}.json"
    if not path.exists():
        raise SystemExit(f"没有这轮结果：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))

    scope = payload.get("scope", "public")
    cfg_saved = payload.get("config", {})
    # ⚠️ 用**这一轮当初跑的那份题集**重算，不是永远用 dataset.yaml。
    # 拿主题集去 rescore 一轮关键词子集，75 道题一道都对不上 id，
    # 结果是"题集里已经没有这些题"然后分母归零——一个看起来跑完了的空报告
    _, cases = load_cases(
        scope=scope,
        space=cfg_saved.get("space", DEFAULT_SPACE),
        dataset=EVAL_DIR / cfg_saved.get("dataset", "dataset.yaml"),
    )
    by_id = {c["id"]: c for c in cases}

    fields = {f.name for f in dataclasses.fields(CaseResult)}
    results, dropped = [], []
    for row in payload["cases"]:
        if row["id"] not in by_id:
            dropped.append(row["id"])
            continue
        results.append(CaseResult(**{k: v for k, v in row.items() if k in fields}))
    if dropped:
        print(f"⚠️ 题集里已经没有这 {len(dropped)} 道题，丢掉不算：{dropped}")

    metrics = score(results, [by_id[r.id] for r in results])
    payload["metrics"] = metrics
    payload["reliable"] = bool(metrics.get("可信", True))
    # ⚠️ `context_chars` 只在第一次存档时算得出来（那时 `context` 还在）。
    # 重算时 `_slim` 会把它抹成 0——一个"重算一下就少掉一列诊断信息"的静默损失，
    # 正是这个项目最不想要的那类改动。按 id 原样搬回来
    chars = {row["id"]: row.get("context_chars", 0) for row in payload["cases"]}
    payload["cases"] = [
        {**_slim(asdict(r)), "context_chars": chars.get(r.id, 0)} for r in results
    ]
    payload["rescored_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{tag}：按现在的口径重算完毕（答案没动，只重算指标）→ {path}")
    for k in ("准确率", "幻觉率", "假阴性率", "无效配图率", "配图串台率", "可判串台数"):
        if (v := metrics.get(k)) is not None:
            print(f"  {k:<12} {v}")


def print_report(
    tag: str, metrics: dict, results: list[CaseResult], judge: str, cfg: Config
) -> None:
    r = cfg.resolved()
    cfg_line = f"top_k={r['top_k']} rerank_k={r['rerank_k']} threshold={r['threshold']}"
    print()
    print("=" * 78)
    print(f"  {tag}    判分模型 {judge}    {cfg_line}")
    # ⭐ 空间和语料指纹打在抬头。半年后回来看两份报告，「为什么这轮低了 3 个点」
    # 第一个要排除的就是"跑的根本不是同一份语料"
    corpus = CORPUS_STATS.get("corpus_sha", "")
    print(
        f"  知识版本 {r['space']}    语料 {CORPUS_STATS.get('chunk_count', '?')} 块"
        + (f" · sha {corpus}" if corpus else "")
    )
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
        "无效配图率",
        "跨空间污染率",
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
    # ⚠️ 串台率必须**连分母一起打**。单独一行 0.0% 会被读成「配图没串台」，
    # 而它同样可能是「这一轮一道题都没判得了」（Agent 路上图片没有出处）
    if (rate := metrics.get("配图串台率")) is not None:
        print(f"  {'配图串台率':<11} {rate}%（可判 {metrics['可判串台数']} 题）")
    elif metrics.get("带图答案数"):
        print(f"  {'配图串台率':<11} 未量到（{metrics['带图答案数']} 题带图，但都对不回出处）")
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
    ap.add_argument(
        "--rescore",
        default="",
        metavar="TAG",
        help="用现在这版判分口径，把已存的那一轮重算一遍指标（不调模型、不花钱）",
    )
    ap.add_argument("--compare", nargs="+", metavar="TAG", help="对比若干轮结果")
    ap.add_argument(
        "--allow-unreliable",
        action="store_true",
        help="判分失效率超线时仍然打印对比表（默认拒绝，见 compare()）",
    )
    ap.add_argument("--only", default="", help="只跑指定 id 或 kind，逗号分隔")
    ap.add_argument(
        "--dataset",
        default="",
        metavar="PATH",
        help="换一份题集（默认 eval/dataset.yaml）。关键词子集：eval/keyword.yaml",
    )
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
    ap.add_argument(
        "--no-judge",
        action="store_true",
        help="判分器不可用时用：只跑规则判定的三条红线，语义指标一律记 UNRELIABLE",
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
        "--space",
        default=DEFAULT_SPACE,
        help=f"在哪个知识版本里跑（默认 {DEFAULT_SPACE}）。题集按题目的 space 字段过滤",
    )
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

    if args.rescore:
        rescore(args.rescore)
        return

    # 指定了用户就跑 private 那组题，否则跑 public。两组题不混跑——
    # 混跑等于把两个不同的题集算进同一个准确率
    user_id = resolve_user(args.as_user) if args.as_user else None
    dataset = Path(args.dataset).resolve() if args.dataset else None
    if dataset is not None and not dataset.exists():
        raise SystemExit(f"没有这个题集：{dataset}")
    meta, cases = load_cases(
        args.only or None,
        scope="private" if user_id else "public",
        space=args.space,
        dataset=dataset,
    )
    if not cases:
        raise SystemExit("这个范围里一道题都没有，检查 --only / --as-user / --space")
    cfg = Config(
        top_k=args.top_k,
        rerank_k=args.rerank_k,
        threshold=args.threshold,
        prompt=args.prompt,
        agent=args.agent,
        mode=args.mode,
        general={"on": True, "off": False}.get(args.general),
        space=args.space,
        dataset=(dataset.name if dataset else "dataset.yaml"),
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
    judge = judge_all(results, cases, workers=args.workers, skip=args.no_judge)

    metrics = score(results, cases)
    path = save(
        tag, meta, cfg, metrics, results, judge, scope="private" if user_id else "public"
    )
    print_report(tag, metrics, results, judge, cfg)
    print()
    print(f"耗时 {time.monotonic() - t0:.0f}s　结果存在 {path}")


if __name__ == "__main__":
    main()
