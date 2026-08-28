"""跨空间评测（M19-A）：**M18 导入企业版语料的门禁**。

    uv run python ../eval/cross_space.py --check           # 只验检索，不调 LLM（免费）
    uv run python ../eval/cross_space.py --tag m19a-xspace # 跑全量
    uv run python ../eval/cross_space.py --compare a b

（在 `backend/` 下用 `uv run` 执行，才 import 得到 copilot 包。）

⭐ **为什么它是独立的一份，而不是 dataset.yaml 里的几道题。**

`dataset.yaml` 的每一道题都在同一个空间里问；这一套题的要害恰恰是
**同一个问题换个空间问，答案必须不一样**。题集要按空间分组跑检索
（一组一个 `space_id`），指标也不是准确率而是「有没有串」——
混进去两边都算不清。

⚠️ **这一套一道题都不用判分器。** 拒答与否、召回了几块、块属于哪个空间、
答案里有没有出现别的空间才有的那个数字，全是规则判定。所以它永远不会
UNRELIABLE——判分器欠费的那天，别的评测只能记 UNRELIABLE，它照跑不误。
这是它够格当门禁的原因。

四条硬指标，破一条就不能导入企业版语料：

    cross_space_contamination_rate  = 0%    召回里有别的空间的块
    banned_leak_rate                = 0%    答案里出现了别的空间才有的事实
    refusal_failure_rate            = 0%    该拒答的题给了实质答案
    control_answer_rate             = 100%  对照题必须答得出来

⚠️ **第四条是前三条的前提，不是锦上添花。** 检索整个坏掉（连不上 embedding、
阈值配错、库是空的）时，前三条同样全绿——一片"干净"证明的是"什么都没发生"，
不是"隔离成立"。对照组掉了，这一轮直接判「没量到」，不判通过。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import run as base  # noqa: E402  —— 检索、生成、结果结构一律复用，不另写一套

DATASET = EVAL_DIR / "cross_space.yaml"
RESULTS_DIR = base.RESULTS_DIR

# 破了就不能上线的四条。前三条是 0%，最后一条是 100%——分开列是因为
# 它们的方向不一样，混成一个"通过/不通过"会让报告读起来像在猜
ZERO_METRICS = (
    "cross_space_contamination_rate",
    "banned_leak_rate",
    "refusal_failure_rate",
)
FULL_METRICS = ("control_answer_rate",)


@dataclass
class SpaceResult(base.CaseResult):
    """`CaseResult` 加两列：这一题在哪个空间问的、期望它是答还是不答。"""

    ask_in: str = ""
    expect: str = ""


def load_cases(only: str | None = None) -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]
    for c in cases:
        if c.get("ask_in") is None:
            sys.exit(f"{c['id']}：没写 ask_in，不知道该在哪个空间里问")
        if c.get("expect") not in ("answer", "no_answer"):
            sys.exit(f"{c['id']}：expect 只能是 answer / no_answer，写的是 {c.get('expect')!r}")
        # `retrieve_all` 按 `kind` 取值，这里让两套词汇对上，不在那边加分支
        c["kind"] = "fact" if c["expect"] == "answer" else "no_answer"
        # 拒答题的「禁止出现」用的是同一个规则判定（会绕开否定句），
        # 所以直接落到 base 认识的那个键上
        if c.get("banned"):
            c["must_not_include"] = c["banned"]
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["ask_in"] in keys]
    return data.get("meta", {}), cases


def retrieve_by_space(
    cases: list[dict], cfg: base.Config, quiet: bool = False
) -> list[SpaceResult]:
    """按 `ask_in` 分组检索。**分组是这套评测的全部机制所在**。

    `base.retrieve_all` 一次只认一个空间（一个 `space_id` 贯穿整轮），
    所以这里按空间切开、各跑一次，再把结果拼回原来的顺序。
    """
    out: dict[str, SpaceResult] = {}
    corpus: dict[str, dict] = {}
    for space in sorted({c["ask_in"] for c in cases}):
        group = [c for c in cases if c["ask_in"] == space]
        if not quiet:
            print(f"── 检索：{space}（{len(group)} 题）──")
        sub = base.Config(**{**asdict(cfg), "space": space})
        for cr in base.retrieve_all(group, sub, quiet=quiet):
            res = SpaceResult(**asdict(cr))
            res.ask_in = space
            res.expect = next(c["expect"] for c in group if c["id"] == cr.id)
            out[res.id] = res
        # 每个空间的语料指纹各存一份：探针题"一块都没召回"到底是因为隔离，
        # 还是因为那个空间的语料忘了导——只有这个数分得开
        corpus[space] = dict(base.CORPUS_STATS)
    CORPUS_BY_SPACE.clear()
    CORPUS_BY_SPACE.update(corpus)
    return [out[c["id"]] for c in cases]


CORPUS_BY_SPACE: dict[str, dict] = {}


def score(results: list[SpaceResult], cases: list[dict]) -> dict:
    """压成指标。**全是规则判定**，一个也不经过判分器。"""
    by_id = {c["id"]: c for c in cases}

    for cr in results:
        case = by_id[cr.id]
        cr.banned_hits = base.banned_hits(cr.answer, case.get("banned") or [])
        cr.missing_facts = base.missing_facts(cr.answer, case.get("must_include") or [])
        cr.bad_image_refs = base.bad_image_refs(cr.answer, cr.context_images)

        if cr.foreign_space_hits:
            cr.status = "incorrect"
            cr.fail_why = f"召回里有 {cr.foreign_space_hits} 块不属于 {cr.ask_in}"
        elif cr.banned_hits:
            # ⭐ 排在拒答之前：一段以「知识库暂无此内容」开头、后面却把
            # 另一个版本的编码报出来的答案，按拒答算是通过的，而它正是污染本身
            cr.status = "incorrect"
            cr.fail_why = f"答案里出现了别的空间才有的事实：{cr.banned_hits}"
        elif cr.expect == "no_answer":
            cr.status = "correct" if cr.said_no_answer else "incorrect"
            cr.fail_why = "" if cr.said_no_answer else "空间里没有材料，却给了实质答案"
        elif cr.said_no_answer:
            cr.status = "incorrect"
            cr.fail_why = "对照题拒答了——这一轮的「干净」可能只是检索没工作"
        elif cr.missing_facts:
            cr.status = "incorrect"
            cr.fail_why = f"对照题漏掉关键事实：{cr.missing_facts}"
        elif cr.source_hit is False:
            cr.status = "incorrect"
            cr.fail_why = "对照题没命中期望来源"
        else:
            cr.status = "correct"
        cr.passed = cr.status == "correct"

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    probes = [r for r in results if r.expect == "no_answer"]
    controls = [r for r in results if r.expect == "answer"]

    return {
        "题数": len(results),
        "探针题": len(probes),
        "对照题": len(controls),
        # 逐块核对 knowledge_space_id 得来的。**它和答案文本无关**：
        # 就算模型一个字都没引用，召回里混进别的空间的块也已经是漏洞
        "cross_space_contamination_rate": pct(
            sum(r.foreign_space_hits > 0 for r in results), len(results)
        ),
        "foreign_chunks": sum(r.foreign_space_hits for r in results),
        "banned_leak_rate": pct(sum(bool(r.banned_hits) for r in results), len(results)),
        "refusal_failure_rate": pct(
            sum(not r.said_no_answer for r in probes), len(probes)
        ),
        "control_answer_rate": pct(sum(r.passed for r in controls), len(controls)),
        # 探针题一共召回了几块、带出了几张图。0 是现在该有的样子（企业版空间
        # 还没有语料）；M18 导入之后这两个数会变成非 0，那时要靠上面几条判
        "probe_chunks": sum(len(r.citations) for r in probes),
        "probe_images": sum(len(r.context_images) for r in probes),
        "control_images": sum(len(r.context_images) for r in controls),
    }


def print_report(tag: str, metrics: dict, results: list[SpaceResult]) -> None:
    print()
    print("=" * 78)
    print(f"  跨空间评测 {tag}    （规则判定，不经判分器，永远不会 UNRELIABLE）")
    print("=" * 78)
    for space, stats in sorted(CORPUS_BY_SPACE.items()):
        sha = stats.get("corpus_sha", "")
        tail = f" · sha {sha}" if sha else ""
        print(f"  {space:<20} {stats.get('chunk_count', '?')} 块" + tail)
    print()
    probes = f"（探针 {metrics['探针题']} / 对照 {metrics['对照题']}）"
    print(f"  {'题数':<28} {metrics['题数']}" + probes)
    for k in ZERO_METRICS:
        flag = "" if metrics[k] == 0.0 else "   ⛔ 破线"
        print(f"  {k:<28} {metrics[k]}%{flag}")
    for k in FULL_METRICS:
        flag = "" if metrics[k] == 100.0 else "   ⛔ 这一轮不作数"
        print(f"  {k:<28} {metrics[k]}%{flag}")
    print()
    print(
        f"  探针题召回 {metrics['probe_chunks']} 块 / 配图 {metrics['probe_images']} 张"
        f"　　对照题配图 {metrics['control_images']} 张"
    )
    if bad := [r for r in results if r.status == "incorrect"]:
        print()
        print(f"  没过的 {len(bad)} 题：")
        for r in bad:
            print(f"    [{r.ask_in:18}] {r.id:28} {r.fail_why[:70]}")


def save(tag: str, meta: dict, cfg: base.Config, metrics: dict, results: list[SpaceResult]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {
        "tag": tag,
        "suite": "cross_space",
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus": meta.get("corpus", ""),
        # ⭐ `corpus_sha` 取**对照组那个空间**的指纹（旗舰版）。门禁核对的是
        # 「这份证据是不是这份语料跑的」，而这套题里只有对照组的空间有语料——
        # 空空间的指纹是空字符串，拿它去核对等于什么都没核对
        "config": {
            **cfg.resolved(),
            "corpus_sha": next(
                (
                    stats.get("corpus_sha", "")
                    for space, stats in sorted(CORPUS_BY_SPACE.items())
                    if stats.get("corpus_sha")
                ),
                "",
            ),
            "spaces": CORPUS_BY_SPACE,
        },
        # ⭐ 这一套不经判分器，所以它**永远是可靠结果**。写死 True 不是偷懒：
        # 门禁那边（eval/gate.py）读的就是这个字段，而"判分器掉线"这件事
        # 在这套题上不可能发生
        "reliable": True,
        "judge_model": "",
        "metrics": metrics,
        "cases": [base._slim(asdict(r)) for r in results],
    }
    path = RESULTS_DIR / f"{tag}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def check(cases: list[dict], cfg: base.Config) -> None:
    """不调 LLM，只看检索。**出题之后先跑它**。

    要在这里发现的是「对照题在旗舰版根本检索不到」——那样的对照题
    证明不了检索是活的，整套门禁就落空了。
    """
    results = retrieve_by_space(cases, cfg, quiet=True)
    print()
    for r in results:
        titles = "、".join(r.retrieved_titles[:3]) or "（一块都没召回）"
        mark = {True: "✓", False: "✗", None: " "}[r.source_hit]
        print(f"  {mark} [{r.ask_in:18}] {r.id:28} {len(r.citations)} 块  {titles[:52]}")
    probes = [r for r in results if r.expect == "no_answer"]
    print()
    print(f"探针题共召回 {sum(len(r.citations) for r in probes)} 块（现在应该是 0）")


def compare(tags: list[str]) -> None:
    runs = []
    for t in tags:
        p = RESULTS_DIR / f"{t}.json"
        if not p.exists():
            sys.exit(f"没有这轮结果：{p}")
        runs.append(json.loads(p.read_text(encoding="utf-8")))
    keys = (*ZERO_METRICS, *FULL_METRICS, "probe_chunks", "probe_images")
    width = max(len(k) for k in keys) + 2
    print(f"{'指标':<{width}}" + "".join(f"{r['tag']:>18}" for r in runs))
    for k in keys:
        print(f"{k:<{width}}" + "".join(f"{r['metrics'].get(k, '—'):>18}" for r in runs))


def main() -> None:
    ap = argparse.ArgumentParser(description="跨空间评测（M19-A 门禁）")
    ap.add_argument("--tag", default="")
    ap.add_argument("--check", action="store_true", help="只验检索，不调 LLM")
    ap.add_argument("--compare", nargs="+", metavar="TAG")
    ap.add_argument("--only", default="", help="只跑指定 id 或空间 code，逗号分隔")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--mode", default="fast", choices=["fast", "deep"])
    ap.add_argument(
        "--general",
        choices=("on", "off"),
        default="",
        help="常识兜底开/关。不传则读 .env——**门禁按线上那一版跑**",
    )
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    meta, cases = load_cases(args.only or None)
    if not cases:
        raise SystemExit("一道题都没有，检查 --only")
    cfg = base.Config(mode=args.mode, general={"on": True, "off": False}.get(args.general))

    if args.check:
        check(cases, cfg)
        return

    tag = args.tag or datetime.now(UTC).strftime("xspace-%m%d-%H%M")
    t0 = time.monotonic()
    results = retrieve_by_space(cases, cfg)
    print("── 生成答案 ──")
    base.answer_all(
        results,
        workers=args.workers,
        system_prompt=cfg.system_prompt(),
        mode=cfg.mode,
        general=cfg.general,
    )
    metrics = score(results, cases)
    path = save(tag, meta, cfg, metrics, results)
    print_report(tag, metrics, results)
    print()
    print(f"耗时 {time.monotonic() - t0:.0f}s　结果存在 {path}")

    # ⭐ 破线要让退出码非 0，理由同 risk_boundary.py：红字会被略过，退出码不会
    broken = [k for k in ZERO_METRICS if metrics[k] != 0.0]
    broken += [k for k in FULL_METRICS if metrics[k] != 100.0]
    if broken:
        print()
        print(f"!! 硬指标破线：{'、'.join(broken)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
