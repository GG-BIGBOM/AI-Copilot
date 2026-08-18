"""路由评测（M10 P0）：这句话被送去了哪里。

    uv run python ../eval/routing.py                  # 跑全量，出报表
    uv run python ../eval/routing.py --only 时间      # 只跑某一类
    uv run python ../eval/routing.py --tag before     # 存一份，改完架构再比
    uv run python ../eval/routing.py --compare before after

（在 `backend/` 下用 `uv run` 执行，这样才能 import 到 copilot 包。）

和 `run.py` 的分工：

    run.py       答得对不对    要调模型、要判分、花钱、有抖动
    routing.py   送去了哪里    纯函数判定、零成本、跑一百遍结果一样

为什么要单独有这一份：`dataset.yaml` 那 55 题全是知识库问答，
**全 Agent 化之后新增的失败模式一道都测不到**——路由错、越过工具直答、
多轮丢状态。M8 的教训是「没有评测就没有资格改架构」，M10 要动的正是路由。

⭐ **路由判定直接 import 生产代码**（`small_talk_kind` / `AGENT_TRIGGERS`），
不在这里抄一份。抄一份的话，改了那边忘了改这边，评测会一直在报告一个
早就不存在的系统——那比没有评测更糟。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "routing.yaml"
RESULTS_DIR = EVAL_DIR / "results"

sys.path.insert(0, str(EVAL_DIR.parent / "backend" / "src"))

# 期望路由的合法取值。写错一个字（比如 smalltalks）应该当场报错，
# 而不是安静地算成一道永远不过的题
ROUTES = ("smalltalk", "capability", "kb", "agent", "time")


@dataclass
class CaseResult:
    id: str
    kind: str
    q: str
    expected: str
    actual: str = ""
    bypassed: bool = False

    @property
    def ok(self) -> bool:
        return self.actual == self.expected


# ---------- 被测对象：今天的路由 ----------


def route_of(question: str, *, in_agent_flow: bool = False) -> str:
    """今天这句话会被送到哪里。

    顺序和 `api/routes/chat.py` 里一致，**不能改**：先看会不会进 Agent
    （`_use_agent` 在接口层最先跑），再看是不是寒暄（`ask_stream` 里判），
    都不是才走检索。

    Args:
        in_agent_flow: 这条会话已经在 Agent 流程里（对应生产代码里
            `conv.profile is not None` 那条粘性规则）。少了它，用户答完
            第一个追问就会掉回直路——M7 线上实测踩过。
    """
    from copilot.api.routes.chat import AGENT_TRIGGERS
    from copilot.qa import small_talk_kind

    if in_agent_flow or any(kw in question for kw in AGENT_TRIGGERS):
        return "agent"

    kind = small_talk_kind(question)
    if kind == "capability":
        return "capability"
    if kind is not None:
        return "smalltalk"

    # 今天没有 current_time 这类工具，问时间也只能当知识库问题去检索
    return "kb"


# ---------- 越过工具直答 ----------

# ERP 答案的特征：界面路径、菜单层级、字段名。
# 一个**没查知识库**却写出这些东西的回答，只可能是编的。
_ERP_MARKS = ("【", "设置–", "设置-", "点击", "菜单", "字段", "勾选", "输入框")


def bypassed_tool(answer: str, *, used_kb: bool) -> bool:
    """判定「越过工具直答」：没走知识库，却写出了像模像样的 ERP 操作。

    ⭐ 这是 M10 的**硬**防线，不是 instruction 里那条软的。
    一旦允许 Agent 自由作答，「该查知识库还是自己答」就成了模型的判断题，
    而这道题答错的样子，恰好就是一条编出来的 ERP 配置。

    今天是双路架构，知识库答案**结构上**只可能来自检索，所以这个数字恒为 0。
    先把判定写下来，是为了 M10 P1 之后它能立刻有意义——
    等到那时候再定义指标，就变成了给自己打分。
    """
    if used_kb:
        return False
    return any(mark in answer for mark in _ERP_MARKS)


# ---------- 跑 ----------


def load_cases(only: str | None = None) -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]
    for c in cases:
        if c["route"] not in ROUTES:
            sys.exit(f"{c['id']}：route 写的是 {c['route']!r}，只能是 {ROUTES}")
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["kind"] in keys]
    return data.get("meta", {}), cases


def run(cases: list[dict]) -> list[CaseResult]:
    out = []
    for c in cases:
        # history 里出现过方案请求 = 这条会话已经在 Agent 流程里
        history = c.get("history") or []
        in_flow = _history_started_agent(history)
        r = CaseResult(id=c["id"], kind=c["kind"], q=c["q"], expected=c["route"])
        r.actual = route_of(c["q"], in_agent_flow=in_flow)
        out.append(r)
    return out


def _history_started_agent(history: list) -> bool:
    from copilot.api.routes.chat import AGENT_TRIGGERS

    return any(
        role == "user" and any(kw in text for kw in AGENT_TRIGGERS) for role, text in history
    )


@dataclass
class Metrics:
    total: int = 0
    route_accuracy: float = 0.0
    bypass_rate: float = 0.0
    by_kind: dict = field(default_factory=dict)


def measure(results: list[CaseResult]) -> Metrics:
    m = Metrics(total=len(results))
    if not results:
        return m
    m.route_accuracy = round(100 * sum(r.ok for r in results) / len(results), 1)
    m.bypass_rate = round(100 * sum(r.bypassed for r in results) / len(results), 1)
    for r in results:
        slot = m.by_kind.setdefault(r.kind, {"total": 0, "ok": 0})
        slot["total"] += 1
        slot["ok"] += int(r.ok)
    for slot in m.by_kind.values():
        slot["accuracy"] = round(100 * slot["ok"] / slot["total"], 1)
    return m


def report(results: list[CaseResult], m: Metrics, tag: str) -> None:
    print()
    print("=" * 78)
    print(f"  路由评测 {tag}")
    print("=" * 78)
    print(f"  题数           {m.total}")
    print(f"  路由准确率      {m.route_accuracy}%")
    print(f"  越过工具直答率   {m.bypass_rate}%（双路架构下结构上恒为 0，M10 P1 后才有意义）")
    print()
    print("  分类准确率：", end="")
    for kind, slot in m.by_kind.items():
        print(f" {kind} {slot['accuracy']}%（{slot['ok']}/{slot['total']}）", end="")
    print()

    bad = [r for r in results if not r.ok]
    if bad:
        print()
        print(f"  路由错的 {len(bad)} 题：")
        for r in bad:
            print(f"    [{r.kind:4}] {r.id:28} 期望 {r.expected:10} 实际 {r.actual}")


def save(results: list[CaseResult], m: Metrics, meta: dict, tag: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"routing-{tag}.json"
    path.write_text(
        json.dumps(
            {
                "tag": tag,
                "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "dataset_meta": meta,
                "metrics": asdict(m),
                "cases": [asdict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def compare(tags: list[str]) -> None:
    rows = []
    for tag in tags:
        path = RESULTS_DIR / f"routing-{tag}.json"
        if not path.exists():
            sys.exit(f"没有这一轮：{path}")
        rows.append((tag, json.loads(path.read_text(encoding="utf-8"))))

    base = rows[0][1]["metrics"]
    print()
    print("| 指标 | " + " | ".join(t for t, _ in rows) + " |")
    print("|---|" + "---|" * len(rows))
    for key, label in (("route_accuracy", "路由准确率"), ("bypass_rate", "越过工具直答率")):
        cells = []
        for i, (_, d) in enumerate(rows):
            v = d["metrics"][key]
            cells.append(f"{v}%" if i == 0 else f"{v}% ({v - base[key]:+.1f})")
        print(f"| {label} | " + " | ".join(cells) + " |")

    if len(rows) == 2:
        before = {c["id"]: c for c in rows[0][1]["cases"]}
        print()
        print(f"相对 {rows[0][0]}，{rows[1][0]} 变化的题：")
        changed = False
        for c in rows[1][1]["cases"]:
            b = before.get(c["id"])
            if b and b["actual"] != c["actual"]:
                changed = True
                mark = "错 → 对" if c["actual"] == c["expected"] else "对 → 错"
                print(f"  {c['id']:28} {mark}  {b['actual']} → {c['actual']}")
        if not changed:
            print("  （没有变化）")


def main() -> None:
    ap = argparse.ArgumentParser(description="路由评测：这句话被送去了哪里")
    ap.add_argument("--tag", default="", help="这轮的名字，结果存 results/routing-<tag>.json")
    ap.add_argument("--only", default="", help="只跑指定 id 或 kind，逗号分隔")
    ap.add_argument("--compare", nargs="+", metavar="TAG", help="对比若干轮结果")
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    meta, cases = load_cases(args.only or None)
    results = run(cases)
    m = measure(results)
    tag = args.tag or datetime.now().strftime("run-%m%d-%H%M")
    report(results, m, tag)
    if args.tag:
        print(f"\n结果存在 {save(results, m, meta, tag)}")


if __name__ == "__main__":
    main()
