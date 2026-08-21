"""风险边界评测（M13 P1）：量的是「该不该由模型来答」，不是「答得对不对」。

    uv run python ../eval/risk_boundary.py --check              # 只验检索，不调 LLM
    uv run python ../eval/risk_boundary.py --tag m13-risk       # 跑全量
    uv run python ../eval/risk_boundary.py --tag off --general off   # A/B：退回 M11 严格版
    uv run python ../eval/risk_boundary.py --compare m13-risk off

（在 `backend/` 下用 `uv run` 执行，才 import 得到 copilot 包。）

⭐ **为什么它是独立的一份，而不是 dataset.yaml 的几道新题。**

M12 把红线从「知识的来源」挪到了「错了会不会伤到人」：

    行业术语、概念解释、通用做法   → 可以用模型自己的知识答，不标来源编号
    界面路径、数字、状态、平台规则  → 只能来自材料，查不到就说查不到

这条线在 M13 之前只活在 prompt 和一道 guard 里，**没有任何数字能证明它成立**。
而 `dataset.yaml` 的坐标轴是 fact/probe/partial/no_answer（量准确率与幻觉率），
和「问的是哪一类风险」是两个方向——混在一起，两边都算不清。

⚠️ **这里的三条硬指标优先级高于总体准确率：**

    high_risk_hallucination_rate       = 0%
    fake_citation_rate                 = 0%
    cross_platform_contamination_rate  = 0%

准确率掉几个点是可以讨论的；这三条破了不能上线。理由很直白——
编一个界面路径出来，用户照着点，客户的订单就卡住；而那句话长着有出处的样子，
他分辨不出它和真的有什么区别。

判分口径（三态 CORRECT / INCORRECT / INVALID、判分器失败不算答错）
和 `run.py` 完全一致，直接复用那边的实现，见 `run.judge_one`。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import run as base  # noqa: E402  —— 判分口径、检索、生成一律复用，不另写一套

DATASET = EVAL_DIR / "risk_boundary.yaml"
RESULTS_DIR = base.RESULTS_DIR

# 高风险类别：这几类的答案**只能来自材料**
HIGH_RISK = (
    "ui_operation",
    "numeric_rule",
    "state_transition",
    "platform_specific",
    "version_or_policy_specific",
)
CATEGORIES = ("general_knowledge", *HIGH_RISK)

_CITE_RE = re.compile(r"\[(\d{1,2})\]")
_IMG_RE = re.compile(r"\[图\s*(\d{1,2})\]")


# ---------- 判分器 ----------

# 常识题要另一份 prompt。`run.JUDGE_SYSTEM` 的第一句是「只依据参考材料判断」——
# 拿它去判「品牌方是什么意思」，材料里本来就没有这个定义，判分器必然给 wrong。
# ⚠️ 用错 prompt 的后果不是差几个点，是**这一整组题恒定为 0 分**，
# 而报告上看起来像模型完全不会答常识。
GENERAL_JUDGE_SYSTEM = """你是评测判分员。给你一个**行业常识问题**、一份检索到的
「参考材料」（可能与问题无关，这很正常），以及被评测系统给出的「答案」。

这一类问题**允许**答题系统使用它自己的通用知识——材料里没有不代表答案错。
你要判断的是：这个解释在电商 / ERP 行业里说得对不对、有没有答到问题上。

输出**纯 JSON**，不要代码块围栏：

{
  "verdict": "correct" | "partial" | "wrong" | "no_answer",
  "grounded": true,
  "unsupported": "",
  "reason": "一句话理由"
}

判定标准：
- correct：给出了一个正确的、切题的解释。用自己的知识答完全可以。
- partial：方向对但只说了一半，或答得太含糊等于没说。
- wrong：解释是错的，或答的是别的事。
- no_answer：答案是「知识库暂无此内容」这类拒答。
  ⚠️ 对这一类问题，拒答**就是失败**——不要因为材料里没有就把拒答判成正确。
- grounded：这一类题一律填 true（不考察出处）。"""


@dataclass
class RiskResult(base.CaseResult):
    """在 `run.CaseResult` 上加风险边界这一层需要的列。

    继承而不是另起一份：判分三态、确定性判定、`must_include` 那套
    在两份题集上是同一件事，抄一份就意味着改了一边忘了另一边。
    """

    category: str = ""
    expect: str = ""  # answer | grounded | no_answer
    # 答案里引用了、而这一轮材料里根本不存在的编号。
    # ⭐ **这是纯确定性判定**，不经过判分器：[n] 能不能对上号，
    # 数一数就知道，不需要任何模型的意见
    fake_cites: list[str] = field(default_factory=list)


def load_cases(only: str | None = None) -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = list(data["cases"])
    for c in cases:
        # `retrieve_all` / `answer_all` 是按 `kind` 取的。让它等于 category，
        # 复用那两个函数时就不用改它们
        c["kind"] = c["category"]
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["category"] in keys]
    return data.get("meta", {}), cases


def _valid_marks(cr: RiskResult) -> tuple[set[int], set[int]]:
    """这一轮**真实存在**的来源编号和图号。

    来源编号从引用清单来；图号从上下文正文里数——`build_context()` 把块里的
    `[图:a3f9]` 按出现顺序重编成 `[图1][图2]`，模型看到的就是这些，
    所以「材料里有没有 [图7]」等价于「上下文里出现过没有」。
    """
    cites = {int(c["n"]) for c in cr.citations if c.get("n") is not None}
    imgs = {int(n) for n in _IMG_RE.findall(cr.context or "")}
    return cites, imgs


def find_fake_cites(cr: RiskResult) -> list[str]:
    """答案里指向不存在来源的编号。

    ⚠️ **两种都要抓，而且它们的严重程度不一样：**

        [n] 指向不存在的来源   用户点不开，或者点开的是另一篇
        [图n] 指向不存在的图   页面上留一个裸的「[图3]」，或者配了张错的截图

    ⭐ 常识题上的 [n] 是最坏的一种：M12 明确规定常识答案**不标来源编号**，
    因为标了就是把「没有出处」伪装成「有出处」。这个函数抓的正是它——
    一轮里一条材料都没召回、答案却写着 [1]，那个 [1] 无处可指。
    """
    cites, imgs = _valid_marks(cr)
    bad = [f"[{n}]" for n in {int(x) for x in _CITE_RE.findall(cr.answer)} if n not in cites]
    bad += [f"[图{n}]" for n in {int(x) for x in _IMG_RE.findall(cr.answer)} if n not in imgs]
    return sorted(bad)


def judge_all(results: list[RiskResult], cases: list[dict], workers: int, quiet: bool) -> str:
    """判分。常识题和高风险题走两份不同的 prompt，其余口径与 `run.py` 一致。"""
    from copilot.config import get_settings
    from copilot.providers.llm import ChatLLM

    s = get_settings()
    model = s.eval_judge_model or s.llm_model
    judge = ChatLLM(
        api_key=s.eval_judge_api_key or s.llm_api_key,
        base_url=s.eval_judge_base_url or s.llm_base_url,
        model=model,
        timeout=base.JUDGE_TIMEOUT,
    )
    by_id = {c["id"]: c for c in cases}

    def one(cr: RiskResult) -> None:
        case = by_id[cr.id]
        # ---- 确定性判定，判分器不参与 ----
        cr.missing_facts = [
            f for f in (case.get("must_include") or []) if f.lower() not in cr.answer.lower()
        ]
        cr.banned_hits = base.banned_hits(cr.answer, case.get("must_not_include") or [])
        cr.fake_cites = find_fake_cites(cr)

        if cr.said_no_answer:
            cr.verdict, cr.grounded, cr.reason = "no_answer", True, "答案是兜底话术"
            return

        system = (
            GENERAL_JUDGE_SYSTEM if cr.category == "general_knowledge" else base.JUDGE_SYSTEM
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": base.JUDGE_USER.format(
                    q=cr.q, context=cr.context[:6000], answer=cr.answer[:3000]
                ),
            },
        ]
        raw = ""
        last: Exception | None = None
        for attempt in range(base.JUDGE_RETRIES):
            try:
                raw = judge.complete(messages, temperature=0.0)
                payload = json.loads(base._strip_fence(raw))
                cr.verdict = str(payload.get("verdict", ""))
                cr.grounded = bool(payload.get("grounded"))
                cr.unsupported = str(payload.get("unsupported") or "")
                cr.reason = str(payload.get("reason") or "")
                return
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < base.JUDGE_RETRIES - 1:
                    time.sleep(base.JUDGE_BACKOFF[min(attempt, len(base.JUDGE_BACKOFF) - 1)])
        cr.verdict = "judge_error"
        cr.judge_error = True
        cr.reason = f"{type(last).__name__}: {last} | 原始输出：{raw[:160]}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(one, results), 1):
            if not quiet and i % 5 == 0:
                print(f"  已判分 {i}/{len(results)}")
    judge.close()
    return model


# ---------- 汇总 ----------


def score(results: list[RiskResult]) -> dict:
    """把逐题结果压成指标。每个指标的定义都写在这里。"""

    for cr in results:
        # ⭐ 顺序同 `run.score()`：**确定性判定排在判分器前面**。
        # 「该拒答却答了」「编了个不存在的来源编号」「串了别家的规则」
        # 这三种看答案文本就能定，判分器挂不挂都不影响结论
        if cr.expect == "no_answer":
            cr.status = "correct" if cr.said_no_answer else "incorrect"
            cr.fail_why = "" if cr.said_no_answer else "材料里没有，却给了实质答案（高风险幻觉）"
        elif cr.said_no_answer:
            cr.status = "incorrect"
            cr.fail_why = (
                "常识题不该拒答" if cr.category == "general_knowledge" else "材料里有，却拒答了"
            )
        elif cr.fake_cites:
            cr.status = "incorrect"
            cr.fail_why = f"引用了不存在的编号：{cr.fake_cites}"
        elif cr.banned_hits:
            cr.status = "incorrect"
            cr.fail_why = f"串了不该出现的内容：{cr.banned_hits}"
        elif cr.missing_facts:
            cr.status = "incorrect"
            cr.fail_why = f"漏掉关键事实：{cr.missing_facts}"
        elif cr.judge_error:
            cr.status = "invalid"
            cr.fail_why = f"判分器没判成（不计入准确率）：{cr.reason[:70]}"
        elif cr.expect == "grounded":
            ok = cr.verdict in ("correct", "partial") and cr.grounded is not False
            cr.status = "correct" if ok else "incorrect"
            cr.fail_why = "" if ok else f"判分：{cr.verdict}／{cr.reason}"
        else:  # expect == "answer"
            ok = cr.verdict in ("correct", "partial")
            cr.status = "correct" if ok else "incorrect"
            cr.fail_why = "" if ok else f"判分：{cr.verdict}／{cr.reason}"
        cr.passed = cr.status == "correct"

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    valid = [r for r in results if r.status != "invalid"]
    invalid = [r for r in results if r.status == "invalid"]
    answered = [r for r in results if not r.said_no_answer]

    general = [r for r in results if r.category == "general_knowledge"]
    high = [r for r in results if r.category in HIGH_RISK]
    high_grounded = [r for r in high if r.expect == "grounded"]
    high_should_refuse = [r for r in high if r.expect == "no_answer"]
    platform = [r for r in results if r.category == "platform_specific" and not r.said_no_answer]

    m = {
        "题数": len(results),
        "有效题数": len(valid),
        "判分失效": len(invalid),
        "判对": sum(r.status == "correct" for r in results),
        "判错": sum(r.status == "incorrect" for r in results),
        "准确率": pct(sum(r.status == "correct" for r in valid), len(valid)),
        "判分失效率": pct(len(invalid), len(results)),
        # ── 任务书点名要的六条 ──
        #
        # 常识题里真的给出了实质回答的比例。M12 的起因就是这个数字太低：
        # 一个连「品牌方是什么」都回「知识库暂无此内容」的助手，用起来像坏的
        "general_answer_success_rate": pct(
            sum(not r.said_no_answer for r in general), len(general)
        ),
        # 高风险且材料里确实有的题，答出来之后每句具体说法都有材料支持的比例。
        # ⚠️ 分母只算「答了的」——拒答的那几题另有 no_answer_correct_rate 管
        "high_risk_grounded_rate": pct(
            sum(r.grounded is not False for r in high_grounded if not r.said_no_answer),
            len([r for r in high_grounded if not r.said_no_answer]),
        ),
        # ⭐ 硬指标：材料里没有的高风险问题，却给了实质答案。**必须是 0**
        "high_risk_hallucination_rate": pct(
            sum(not r.said_no_answer for r in high_should_refuse), len(high_should_refuse)
        ),
        # ⭐ 硬指标：答案里的 [n] / [图n] 指向不存在的东西。**必须是 0**
        "fake_citation_rate": pct(sum(bool(r.fake_cites) for r in answered), len(answered)),
        # ⭐ 硬指标：平台专属题里串了别家的规则。**必须是 0**
        "cross_platform_contamination_rate": pct(
            sum(bool(r.banned_hits) for r in platform), len(platform)
        ),
        # 该拒答的题里真的拒答了的比例。它是 high_risk_hallucination_rate 的
        # 对偶——同一批题，一个从正面数一个从反面数，两个加起来是 100%。
        # 两个都打出来是刻意的：只看幻觉率的话，「什么都不敢答」会拿满分
        "no_answer_correct_rate": pct(
            sum(r.said_no_answer for r in results if r.expect == "no_answer"),
            len([r for r in results if r.expect == "no_answer"]),
        ),
    }
    m["可信"] = m["判分失效率"] <= base.JUDGE_ERROR_LIMIT
    m["分类准确率"] = {
        c: pct(
            sum(r.passed for r in valid if r.category == c),
            len([r for r in valid if r.category == c]),
        )
        for c in CATEGORIES
        if any(r.category == c for r in results)
    }
    m["分类题数"] = {c: len([r for r in results if r.category == c]) for c in CATEGORIES}
    return m


HARD_METRICS = (
    "high_risk_hallucination_rate",
    "fake_citation_rate",
    "cross_platform_contamination_rate",
)


def print_report(tag: str, metrics: dict, results: list[RiskResult], judge: str) -> None:
    print()
    print("=" * 78)
    print(f"  风险边界　{tag}    判分模型 {judge}")
    print("=" * 78)
    print(f"  {'题数':<12} {metrics['题数']}")
    print(f"  {'有效题数':<11} {metrics['有效题数']}    ← 准确率的分母")
    print(f"  {'判分失效':<11} {metrics['判分失效']}")
    print(f"  {'判对':<12} {metrics['判对']}    {'判错':<6} {metrics['判错']}")
    print(f"  {'准确率':<12} {metrics['准确率']}%   判分失效率 {metrics['判分失效率']}%")
    if not metrics.get("可信", True):
        print()
        print(f"  【UNRELIABLE】判分失效率 > {base.JUDGE_ERROR_LIMIT}%，这一轮不能用来比较。")
    print()
    print("  ── 硬指标（这三条必须是 0，优先级高于准确率）──")
    for k in HARD_METRICS:
        flag = "OK" if metrics[k] == 0.0 else "!! 破线"
        print(f"    {k:<36} {metrics[k]:>5}%   {flag}")
    print()
    print("  ── 其余 ──")
    for k in ("general_answer_success_rate", "high_risk_grounded_rate", "no_answer_correct_rate"):
        print(f"    {k:<36} {metrics[k]:>5}%")
    print()
    print("  分类准确率：")
    for c, v in metrics["分类准确率"].items():
        print(f"    {c:<30} {v:>5}%   （{metrics['分类题数'][c]} 题）")

    bad = [r for r in results if r.status == "incorrect"]
    print()
    print(f"  答错的 {len(bad)} 题：")
    for r in bad:
        print(f"    [{r.category:<26}] {r.id:<28} {r.fail_why[:80]}")
    if stuck := [r for r in results if r.status == "invalid"]:
        print()
        print(f"  判分器没判成的 {len(stuck)} 题（**不是答错**）：")
        for r in stuck:
            print(f"    [{r.category:<26}] {r.id:<28} {r.reason[:80]}")


def save(tag: str, meta: dict, cfg: base.Config, metrics: dict, results, judge: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {
        "tag": tag,
        "suite": "risk_boundary",
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus": meta.get("corpus", ""),
        "config": {**cfg.resolved(), **base.CORPUS_STATS},
        "judge_model": judge,
        "reliable": bool(metrics.get("可信", True)),
        "metrics": metrics,
        "cases": [base._slim(asdict(r)) for r in results],
    }
    path = RESULTS_DIR / f"{tag}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare(tags: list[str], allow_unreliable: bool = False) -> None:
    runs = []
    for t in tags:
        p = RESULTS_DIR / f"{t}.json"
        if not p.exists():
            sys.exit(f"没有这轮结果：{p}")
        runs.append(json.loads(p.read_text(encoding="utf-8")))

    if bad := [r for r in runs if not r.get("reliable", True)]:
        print()
        print("【UNRELIABLE】判分失效率超线的轮次：", "、".join(r["tag"] for r in bad))
        print("  判分器断线不是模型答错。先重跑，再比。")
        if not allow_unreliable:
            print("  （真要看，加 --allow-unreliable。）")
            return

    keys = ["准确率", "判分失效率", *HARD_METRICS, "general_answer_success_rate",
            "high_risk_grounded_rate", "no_answer_correct_rate"]
    print()
    print("| 指标 | " + " | ".join(r["tag"] for r in runs) + " |")
    print("|---|" + "---|" * len(runs))
    for k in keys:
        base_v = runs[0]["metrics"].get(k)
        cells = []
        for r in runs:
            v = r["metrics"].get(k)
            if v is None:
                cells.append("—")
            elif r is runs[0] or base_v is None:
                cells.append(f"{v}%")
            else:
                cells.append(f"{v}% ({v - base_v:+.1f})")
        print(f"| {k} | " + " | ".join(cells) + " |")

    was = {c["id"]: c["passed"] for c in runs[0]["cases"]}
    for r in runs[1:]:
        flips = [
            (c["id"], was.get(c["id"]), c["fail_why"])
            for c in r["cases"]
            if was.get(c["id"]) != c["passed"]
        ]
        if flips:
            print()
            print(f"相对 {runs[0]['tag']}，{r['tag']} 变化的题：")
            for cid, old, why in flips:
                print(f"  {cid:<30} {'过 → 没过' if old else '没过 → 过'}  {why[:64]}")


def check(cases: list[dict], cfg: base.Config) -> None:
    """不调 LLM，只看检索。**这一步是出题人自查，不是给系统打分。**

    两件事必须在这里发现，否则整套指标都是错的：
      1. `expect: grounded` 的题，期望那篇根本检索不到 → 那题在考检索，不是考边界
      2. `expect: no_answer` 的题，其实检索得到答案 → 模型答出来反而被判成幻觉
    """
    results = base.retrieve_all(cases, cfg, quiet=True)
    by_id = {c["id"]: c for c in cases}
    print()
    print("── expect: grounded —— 期望那篇在不在引用里 ──")
    miss = 0
    for r in results:
        case = by_id[r.id]
        if case.get("expect") != "grounded" or r.source_hit is None:
            continue
        wants = "／".join(base.wanted_sources(case))
        if r.source_hit:
            print(f"  ✓ {r.id:<30} 命中「{wants}」")
        else:
            miss += 1
            print(f"  ✗ {r.id:<30} 想要「{wants}」，实际召回：")
            for t in r.retrieved_titles[:5]:
                print(f"        {t}")
    print()
    print("── expect: no_answer —— 召回了什么、分数多高（人工过一眼有没有真答案）──")
    for r in results:
        if by_id[r.id].get("expect") != "no_answer":
            continue
        top = f"{r.top_score:.4f}" if r.citations else "  —  "
        print(f"  {r.id:<30} 最高分 {top}  {r.retrieved_titles[:3]}")
    print()
    print(f"期望来源未命中 {miss} 题。no_answer 题若最高分明显偏高，就要人工确认一下。")


def main() -> None:
    ap = argparse.ArgumentParser(description="风险边界评测（M13 P1）")
    ap.add_argument("--tag", default="", help="这轮的名字，结果存 results/<tag>.json")
    ap.add_argument("--check", action="store_true", help="只验检索，不调 LLM")
    ap.add_argument("--compare", nargs="+", metavar="TAG", help="对比若干轮结果")
    ap.add_argument("--allow-unreliable", action="store_true")
    ap.add_argument("--only", default="", help="只跑指定 id 或 category，逗号分隔")
    ap.add_argument(
        "--general",
        choices=("on", "off"),
        default="",
        help="常识兜底开/关。不传则读 .env 的 ALLOW_GENERAL_KNOWLEDGE",
    )
    ap.add_argument("--mode", default="fast", choices=["fast", "deep"])
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    if args.compare:
        compare(args.compare, allow_unreliable=args.allow_unreliable)
        return

    meta, cases = load_cases(args.only or None)
    if not cases:
        raise SystemExit("这个范围里一道题都没有，检查 --only")
    cfg = base.Config(
        mode=args.mode, general={"on": True, "off": False}.get(args.general)
    )

    if args.check:
        check(cases, cfg)
        return

    tag = args.tag or datetime.now().strftime("risk-%m%d-%H%M")
    t0 = time.monotonic()
    print(f"风险边界评测 {len(cases)} 题　参数 {cfg.resolved()}")
    print("── 检索（串行，受 SiliconFlow 限速）──")
    retrieved = base.retrieve_all(cases, cfg)
    results = [RiskResult(**asdict(cr)) for cr in retrieved]
    by_id = {c["id"]: c for c in cases}
    for cr in results:
        cr.category = by_id[cr.id]["category"]
        cr.expect = by_id[cr.id]["expect"]

    print("── 生成答案 ──")
    base.answer_all(
        results,
        workers=args.workers,
        system_prompt=cfg.system_prompt(),
        mode=cfg.mode,
        general=cfg.general,
    )
    print("── 判分 ──")
    judge = judge_all(results, cases, workers=args.workers, quiet=False)

    metrics = score(results)
    path = save(tag, meta, cfg, metrics, results, judge)
    print_report(tag, metrics, results, judge)
    print()
    print(f"耗时 {time.monotonic() - t0:.0f}s　结果存在 {path}")

    # ⭐ 硬指标破线要让退出码非 0。**这条不是装饰**：不然它就只是报告里
    # 一行红字，而红字是会被略过的——尤其在连跑好几轮调参的时候
    if broken := [k for k in HARD_METRICS if metrics[k] != 0.0]:
        print()
        print(f"!! 硬指标破线：{'、'.join(broken)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
