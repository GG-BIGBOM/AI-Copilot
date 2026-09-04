"""Selective Hybrid 的**免费**检索 A/B：dense / hybrid-all / selective 三臂对比。

⭐⭐ **为什么单独一个入口，而不是三次 `run.py --check` 再 `--compare`。**

`run.py --check` 走的是 `check()` 那条分支，它**只打印、不落 `results/<tag>.json`**
（见 `run.main` 里 `if args.check: check(...); return`）。所以
`--check --tag X` 后面接 `--compare X Y` 是接不上的——EVALUATION.md 里那段
命令块 2026-09-03 写错过一次，就是这个原因。

而且分三次跑还有一个真问题：**三臂之间的语料快照不保证一样**。
一次 `copilot sync-yuque` 插在中间，后两臂的分母就变了，而报告上一个字都不提。
这里三臂在**同一个进程、同一份语料**上跑完。

⚠️ **判据一个都不新造，全部复用 `run.py`：**

    检索         `run.retrieve_all`      —— 和 `--check` 是同一个函数
    命中的定义   `run.wanted_sources` + 「期望标题是召回标题的子串」
                 —— 和 `CaseResult.source_hit` 逐字同一套
    MRR@5        由 `retrieved_titles` 的**次序**算，用的还是上面那个匹配

    这里只多做一件事：**按题集自己的 id 前缀分组**（`kw-paste-*` 是裸粘贴，
    其余是完整问句）。那正是这次决策唯一缺的那一维——`--check` 把 45 题
    混在一起报一个数，而收益和伤害恰好分别落在这两组上。

⚠️ **它不调 LLM，一次生成都不花。** embedding / rerank 走 SiliconFlow 免费额度。
放行之后要跑的付费那一档在 EVALUATION.md 四·五·一，是另一套命令。

    cd backend
    .venv/Scripts/python.exe ../eval/selective.py          # Windows
    .venv/bin/python ../eval/selective.py                  # Linux
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import run as base  # noqa: E402  —— 检索、判据、Config 一律复用，不另写一套

DATASET = EVAL_DIR / "keyword.yaml"
# 裸粘贴那一组的 id 前缀。**分组依据是题集自己的 id**，不是我另判一次——
# 判据和 `tests/test_selective_hybrid.py` 完全一致
PASTE_PREFIX = "kw-paste-"
# MRR 只看前 5：进不了 top-5 的块不会进上下文，排第 8 和排第 20 对答案没区别
MRR_AT = 5


def _rank(result, case) -> int:
    """期望来源排第几（1 起）。没进前 `MRR_AT` 名就是 0。

    ⚠️ 匹配规则和 `run.py` 里算 `source_hit` 的那一行**逐字相同**
    （`any(w in t for w in wants)`）。另写一套的话，同一份数据会算出
    两个不一样的"命中"，而报告上看不出该信哪个。
    """
    wants = base.wanted_sources(case)
    if not wants:
        return 0
    for i, title in enumerate(result.retrieved_titles[:MRR_AT], start=1):
        if any(w in title for w in wants):
            return i
    return 0


def _arm(cases: list[dict], label: str, *, hybrid: bool, selective: bool) -> dict:
    """跑一臂。开关通过 `get_settings()` 的缓存对象直接改——

    ⚠️ 不走环境变量：`Settings` 是 `lru_cache` 的，进程里改 env 不生效，
    而三臂必须在同一个进程里（见文件头）。改完当场跑完，不留给下一臂。
    """
    from copilot.config import get_settings

    s = get_settings()
    old = (s.hybrid_enabled, s.selective_hybrid_enabled)
    s.hybrid_enabled, s.selective_hybrid_enabled = hybrid, selective
    try:
        cfg = base.Config(dataset=DATASET.name, hybrid=hybrid)
        results = base.retrieve_all(cases, cfg, quiet=True)
    finally:
        s.hybrid_enabled, s.selective_hybrid_enabled = old

    by_id = {c["id"]: c for c in cases}
    groups: dict[str, dict] = {}
    for r in results:
        case = by_id[r.id]
        if not base.wanted_sources(case):
            continue
        g = "identifier" if r.id.startswith(PASTE_PREFIX) else "full-question"
        bucket = groups.setdefault(g, {"n": 0, "hit": 0, "rr": 0.0, "missed": []})
        bucket["n"] += 1
        rank = _rank(r, case)
        if rank:
            bucket["hit"] += 1
            bucket["rr"] += 1.0 / rank
        else:
            bucket["missed"].append(r.id)
    return {"label": label, "groups": groups}


def _classifier_confusion(cases: list[dict]) -> dict:
    """分类器在这 45 条上的误判。

    以题集自己的分组为真值：`kw-paste-*` 应该判成 identifier，其余不应该。

        FP  完整问句被判成 identifier  → 它会去走词法，正是 ADR-16 打回来的那个配置
        FN  裸粘贴被判成完整问句      → 丢掉一份 hybrid 的收益
    """
    from copilot.query_shape import is_identifier_query

    fp, fn = [], []
    for c in cases:
        want_identifier = str(c["id"]).startswith(PASTE_PREFIX)
        got = is_identifier_query(str(c["q"]))
        if got and not want_identifier:
            fp.append(c["id"])
        if want_identifier and not got:
            fn.append(c["id"])
    return {"fp": fp, "fn": fn}


def main() -> None:
    cases = yaml.safe_load(DATASET.read_text(encoding="utf-8"))["cases"]
    print(f"Selective Hybrid 免费检索 A/B　题集 {DATASET.name}　{len(cases)} 题")
    print("⚠️ 只量检索，不调 LLM。三臂在同一个进程、同一份语料上跑\n")

    arms = [
        _arm(cases, "dense", hybrid=False, selective=False),
        _arm(cases, "hybrid-all", hybrid=True, selective=False),
        _arm(cases, "selective", hybrid=False, selective=True),
    ]

    order = ["full-question", "identifier"]
    header = f"{'Metric':<24}" + "".join(f"{a['label']:>14}" for a in arms)
    print(header)
    print("-" * len(header))
    for g in order:
        for metric in ("hit", "mrr"):
            cells = []
            for a in arms:
                b = a["groups"].get(g, {"n": 0, "hit": 0, "rr": 0.0})
                if metric == "hit":
                    cells.append(f"{b['hit']}/{b['n']}")
                else:
                    cells.append(f"{(b['rr'] / b['n']) if b['n'] else 0:.3f}")
            name = f"{g} {'hit' if metric == 'hit' else f'MRR@{MRR_AT}'}"
            print(f"{name:<24}" + "".join(f"{c:>14}" for c in cells))

    conf = _classifier_confusion(cases)
    print(f"\n{'classifier FP':<24}{len(conf['fp']):>14}　（完整问句被判成 identifier）")
    print(f"{'classifier FN':<24}{len(conf['fn']):>14}　（裸粘贴被判成完整问句）")
    if conf["fp"]:
        print("  FP:", ", ".join(conf["fp"]))
    if conf["fn"]:
        print("  FN:", ", ".join(conf["fn"]))

    print("\n── 各臂没命中的题 ──")
    for a in arms:
        for g in order:
            missed = a["groups"].get(g, {}).get("missed", [])
            if missed:
                print(f"  {a['label']:<12}{g:<16}{', '.join(missed)}")

    # ⭐ 放行判据直接打在报告里，免得看的人自己去翻 EVALUATION.md
    dense_fq = arms[0]["groups"]["full-question"]
    sel_fq = arms[2]["groups"]["full-question"]
    sel_id = arms[2]["groups"]["identifier"]
    hyb_id = arms[1]["groups"]["identifier"]
    print("\n── 放行判据（EVALUATION.md 四·五·一）──")
    ok_fq = sel_fq["hit"] >= dense_fq["hit"]
    ok_id = sel_id["hit"] >= hyb_id["hit"]
    print(f"  完整问句不退化：{'PASS' if ok_fq else 'FAIL'}"
          f"（selective {sel_fq['hit']}/{sel_fq['n']} vs dense {dense_fq['hit']}/{dense_fq['n']}）")
    sel_txt = f"{sel_id['hit']}/{sel_id['n']}"
    hyb_txt = f"{hyb_id['hit']}/{hyb_id['n']}"
    print(f"  identifier 追平 hybrid-all：{'PASS' if ok_id else 'FAIL'}"
          f"（selective {sel_txt} vs hybrid-all {hyb_txt}）")
    print(f"  分类器零误判：{'PASS' if not conf['fp'] and not conf['fn'] else 'FAIL'}")


if __name__ == "__main__":
    main()
