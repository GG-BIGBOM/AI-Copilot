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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
DATASET = EVAL_DIR / "dataset.yaml"

sys.path.insert(0, str(EVAL_DIR.parent / "backend" / "src"))

# ---------- 判分器的 prompt ----------

JUDGE_SYSTEM = """你是严格的评测判分员。给你一个问题、检索到的「参考材料」、以及被评测系统给出的「答案」。

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


# ---------- 数据结构 ----------


@dataclass
class Config:
    top_k: int = 0  # 0 = 用 settings 里的默认值
    rerank_k: int = 0
    threshold: float = -1.0  # <0 = 用默认
    prompt: str = "current"  # current = 线上那版；其余取 eval/prompts.py 的存档

    def system_prompt(self) -> str | None:
        if self.prompt == "current":
            return None
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
            "top_k": self.top_k or s.retrieve_top_k,
            "rerank_k": self.rerank_k or s.rerank_top_k,
            "threshold": s.rerank_score_threshold if self.threshold < 0 else self.threshold,
            "chunk_size": s.chunk_size,
            "chunk_overlap": s.chunk_overlap,
            "embedding_model": s.embedding_model,
            "rerank_model": s.rerank_model,
            "answer_model": s.llm_model,
            # ⭐ prompt 指纹。没有它，「改了 prompt 重跑」和「什么都没改重跑」
            # 存出来的 config 一模一样——半年后看对比表根本分不清哪轮是哪轮
            "prompt_sha": hashlib.sha256(prompt_text.encode()).hexdigest()[:8],
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

    # 确定性判定
    source_hit: bool | None = None  # 期望源是否出现在引用里（无期望源时为 None）
    cited_source: bool | None = None  # 答案的 [n] 是否指向期望源
    missing_facts: list[str] = field(default_factory=list)
    said_no_answer: bool = False

    # LLM 判定
    verdict: str = ""
    grounded: bool | None = None
    unsupported: str = ""
    reason: str = ""

    # 汇总
    passed: bool = False
    fail_why: str = ""


# ---------- 载入 ----------


def load_cases(only: str | None = None) -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["kind"] in keys]
    return data.get("meta", {}), cases


# ---------- 阶段一：检索（串行） ----------


CORPUS_STATS: dict = {}  # 检索时顺手记下当时的块数，换 chunk 参数重灌后能看出规模变化


def retrieve_all(cases: list[dict], cfg: Config, quiet: bool = False) -> list[CaseResult]:
    import asyncio

    from sqlalchemy import func, select

    from copilot.db.models import Chunk
    from copilot.db.session import SessionLocal
    from copilot.providers.siliconflow import (
        SiliconFlowClient,
        SiliconFlowEmbedder,
        SiliconFlowReranker,
    )
    from copilot.retrieve import search

    r = cfg.resolved()

    async def main() -> list[CaseResult]:
        client = SiliconFlowClient()
        emb, rr = SiliconFlowEmbedder(client=client), SiliconFlowReranker(client=client)
        out: list[CaseResult] = []
        async with SessionLocal() as session:
            CORPUS_STATS["chunk_count"] = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.owner_id.is_(None))
            )
            for i, case in enumerate(cases, 1):
                res = await search(
                    session,
                    case["q"],
                    emb,
                    rr,
                    user_id=None,  # 评测只打公共库
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
                )
                if wants := wanted_sources(case):
                    cr.source_hit = any(w in t for w in wants for t in cr.retrieved_titles)
                out.append(cr)
                if not quiet:
                    flag = "" if cr.source_hit is None else ("命中" if cr.source_hit else "未命中")
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
) -> None:
    from copilot.providers.llm import ChatLLM
    from copilot.qa import NO_ANSWER, SYSTEM_PROMPT, USER_TEMPLATE, is_no_answer

    # 允许换 prompt：A/B 时必须拿**同一份评测集**跑两个 prompt，
    # 否则指标的变化归不了因（见 eval/prompts.py 的说明）
    prompt = system_prompt or SYSTEM_PROMPT
    llm = ChatLLM()  # httpx.Client 线程安全，一个实例够用

    def one(cr: CaseResult) -> None:
        if not cr.citations:
            # 第一道闸门：什么都没召回，线上会直接返回兜底话术，不调 LLM
            cr.answer = NO_ANSWER
        else:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": USER_TEMPLATE.format(context=cr.context, question=cr.q)},
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


# ---------- 阶段三：判分（并行） ----------


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
    )
    by_id = {c["id"]: c for c in cases}

    def one(cr: CaseResult) -> None:
        case = by_id[cr.id]
        # 确定性判定先做完，判分器不参与这部分
        cr.missing_facts = [
            f for f in (case.get("must_include") or []) if f.lower() not in cr.answer.lower()
        ]
        banned = [f for f in (case.get("must_not_include") or []) if f.lower() in cr.answer.lower()]
        if banned:
            cr.unsupported = f"出现了禁止内容：{banned}"

        if cr.said_no_answer:
            # 说了不知道就不必判语义了，省一次调用
            cr.verdict, cr.grounded, cr.reason = "no_answer", True, "答案是兜底话术"
            return

        raw = ""
        try:
            raw = judge.complete(
                [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {
                        "role": "user",
                        "content": JUDGE_USER.format(
                            q=cr.q, context=cr.context[:6000], answer=cr.answer[:3000]
                        ),
                    },
                ],
                temperature=0.0,
            )
            payload = json.loads(_strip_fence(raw))
            cr.verdict = str(payload.get("verdict", ""))
            cr.grounded = bool(payload.get("grounded"))
            cr.unsupported = cr.unsupported or str(payload.get("unsupported") or "")
            cr.reason = str(payload.get("reason") or "")
        except Exception as e:  # noqa: BLE001
            cr.verdict = "judge_error"
            cr.reason = f"{type(e).__name__}: {e} | 原始输出：{raw[:160]}"

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

        if cr.kind == "no_answer":
            cr.passed = cr.said_no_answer
            cr.fail_why = "" if cr.passed else "该说不知道，却给了实质答案（幻觉）"
        elif cr.said_no_answer:
            cr.passed = False
            cr.fail_why = "材料里有答案，却答了「暂无此内容」（假阴性）"
        elif cr.missing_facts:
            cr.passed = False
            cr.fail_why = f"漏掉关键事实：{cr.missing_facts}"
        elif cr.kind == "partial":
            # 部分覆盖的题，要的是「答已有的 + 点明缺的」。判分器给 correct 或
            # partial 都算过——它看到的是同一份材料，能确认答案没超出材料
            cr.passed = cr.verdict in ("correct", "partial") and cr.grounded is not False
            cr.fail_why = "" if cr.passed else f"判分：{cr.verdict}／{cr.reason}"
        else:
            cr.passed = cr.verdict == "correct" and cr.grounded is not False
            cr.fail_why = "" if cr.passed else f"判分：{cr.verdict}／{cr.reason}"

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    positives = [r for r in results if r.kind != "no_answer"]  # 材料里有答案的题
    negatives = [r for r in results if r.kind == "no_answer"]
    with_source = [r for r in results if r.source_hit is not None]
    answered_with_source = [r for r in with_source if not r.said_no_answer]

    m = {
        "题数": len(results),
        "准确率": pct(sum(r.passed for r in results), len(results)),
        "检索命中率": pct(sum(bool(r.source_hit) for r in with_source), len(with_source)),
        "引用正确率": pct(
            sum(bool(r.cited_source) for r in answered_with_source), len(answered_with_source)
        ),
        "幻觉率": pct(sum(not r.said_no_answer for r in negatives), len(negatives)),
        "假阴性率": pct(sum(r.said_no_answer for r in positives), len(positives)),
        "无据陈述率": pct(
            sum(r.grounded is False for r in results if not r.said_no_answer),
            len([r for r in results if not r.said_no_answer]),
        ),
    }
    m["分类准确率"] = {
        kind: pct(
            sum(r.passed for r in results if r.kind == kind),
            len([r for r in results if r.kind == kind]),
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
    "无据陈述率": "答了的题里，判分器发现「有材料不支持的具体说法」的比例。"
    "比幻觉率更细：答案整体方向对，但夹了一句编的",
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
    for k in ("题数", "准确率", "检索命中率", "引用正确率", "幻觉率", "假阴性率", "无据陈述率"):
        v = metrics[k]
        unit = "" if k == "题数" else "%"
        print(f"  {k:<12} {v}{unit}")
    print()
    print("  分类准确率：", "  ".join(f"{k} {v}%" for k, v in metrics["分类准确率"].items()))

    bad = [r for r in results if not r.passed]
    print()
    print(f"  没过的 {len(bad)} 题：")
    for r in bad:
        print(f"    [{r.kind:9}] {r.id:32} {r.fail_why[:88]}")
    ungrounded = [r for r in results if r.grounded is False]
    if ungrounded:
        print()
        print("  夹了材料不支持的说法：")
        for r in ungrounded:
            print(f"    {r.id:32} {r.unsupported[:100]}")


def compare(tags: list[str]) -> None:
    runs = []
    for t in tags:
        p = RESULTS_DIR / f"{t}.json"
        if not p.exists():
            sys.exit(f"没有这轮结果：{p}")
        runs.append(json.loads(p.read_text(encoding="utf-8")))

    keys = ["准确率", "检索命中率", "引用正确率", "幻觉率", "假阴性率", "无据陈述率"]
    w = max(len(t) for t in tags) + 2

    print()
    print("| 指标 | " + " | ".join(f"{r['tag']}" for r in runs) + " |")
    print("|---|" + "---|" * len(runs))
    for k in keys:
        cells = []
        for r in runs:
            v = r["metrics"][k]
            base = runs[0]["metrics"][k]
            d = v - base
            cells.append(f"{v}%" if r is runs[0] else f"{v}% ({d:+.1f})")
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
            for cid, was, now, why in flips:
                arrow = "过 → 没过" if was else "没过 → 过"
                print(f"  {cid:32} {arrow}  {why[:70]}")


# ---------- 只验检索 ----------


def check(cases: list[dict], cfg: Config) -> None:
    """不调 LLM，只看检索。用来验「评测集本身立不立得住」。

    两件事必须在这里发现，否则整套指标都是错的：
      1. fact 题的期望来源根本检索不到 → 那题在考检索，不是在考生成
      2. no_answer 题其实检索得到答案 → 模型答出来反而被判成幻觉
    """
    results = retrieve_all(cases, cfg, quiet=True)
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
    ap.add_argument("--only", default="", help="只跑指定 id 或 kind，逗号分隔")
    ap.add_argument("--top-k", type=int, default=0)
    ap.add_argument("--rerank-k", type=int, default=0)
    ap.add_argument("--threshold", type=float, default=-1.0)
    ap.add_argument("--prompt", default="current", help="用哪版 system prompt（见 eval/prompts.py）")
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    meta, cases = load_cases(args.only or None)
    cfg = Config(
        top_k=args.top_k, rerank_k=args.rerank_k, threshold=args.threshold, prompt=args.prompt
    )

    if args.check:
        check(cases, cfg)
        return

    tag = args.tag or datetime.now().strftime("run-%m%d-%H%M")
    t0 = time.monotonic()
    print(f"评测 {len(cases)} 题　参数 {cfg.resolved()}")
    print("── 检索（串行，受 SiliconFlow 限速）──")
    results = retrieve_all(cases, cfg)
    print("── 生成答案 ──")
    answer_all(results, workers=args.workers, system_prompt=cfg.system_prompt())
    print("── 判分 ──")
    judge = judge_all(results, cases, workers=args.workers)

    metrics = score(results, cases)
    path = save(tag, meta, cfg, metrics, results, judge)
    print_report(tag, metrics, results, judge, cfg)
    print()
    print(f"耗时 {time.monotonic() - t0:.0f}s　结果存在 {path}")


if __name__ == "__main__":
    main()
