"""路由评测（M10 P0）：这句话被送去了哪里。

    uv run python ../eval/routing.py                  # 跑全量，出报表（确定性路由）
    uv run python ../eval/routing.py --live           # 让真模型决定（M10 P3 的生效路径）
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
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "routing.yaml"
RESULTS_DIR = EVAL_DIR / "results"

sys.path.insert(0, str(EVAL_DIR.parent / "backend" / "src"))

# 期望路由的合法取值。写错一个字（比如 smalltalks）应该当场报错，
# 而不是安静地算成一道永远不过的题
# `direct` 只会出现在 **实测**结果里（模型一个工具都没调、自己答了），
# 不是合法的**期望**值——期望「它自己答」的题都归在 smalltalk / capability
ROUTES = ("smalltalk", "capability", "kb", "agent", "time", "refuse")


@dataclass
class CaseResult:
    id: str
    kind: str
    q: str
    expected: str
    actual: str = ""
    bypassed: bool = False
    # 除 `expected` 外还接受哪些落点（题面里的 `accept`）。
    # ⚠️ 只用在**同样正确**的两种行为上，不是给不及格的结果开后门：
    # 越界题走 `answer_kb` 然后诚实说没有、和当场划一句边界，都是对的。
    accept: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.actual == self.expected or self.actual in self.accept


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


# 划边界的拒答长什么样。⚠️ 这是**评测侧**的近似，生产代码里没有这个概念：
# Agent 的 instructions 只说了「和这个产品无关的事不做」，怎么说由模型自己写。
# 所以这里认的是这类回答共有的形状——先说不做/无关，再把话题引回 ERP。
_REFUSAL_MARKS = (
    "帮不了",
    "没法帮",
    "不能帮",
    "无关",
    "超出",
    "不在",
    "只负责",
    "不写代码",
    "不做",
    "范围",
)


def looks_like_refusal(answer: str) -> bool:
    """这句话是「我不做这个」，而不是「这是答案」。

    ⚠️ 附加条件：**不能带 ERP 操作的特征**。一段既写了界面路径、
    又在末尾补一句「其余的我不负责」的回答，仍然算越过工具直答。
    """
    if not answer:
        return False
    if any(mark in answer for mark in _ERP_MARKS):
        return False
    return any(mark in answer for mark in _REFUSAL_MARKS)


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


# ---------- 模型路由（M10 P3）----------
#
# ⚠️ **灰度之后，上面那个 `route_of` 测的是一条正在退休的分叉。**
# 灰度桶里的用户，路由由模型决定：它看着工具列表挑一个调。关键词表那套
# 只对桶外的人生效。不补这个模式的话，这份题集会安静地一直报 100%——
# 它量的东西已经不在生效路径上了，而报表上完全看不出来。
#
# 怎么做到「只量决策、不花答案的钱」：`FunctionToolCallEvent` 在工具**执行之前**
# 就发出来了。收到第一个就跳出循环，工具根本不会跑。所以一道题只花一次
# 很短的模型请求（几十个 token），58 题跑一遍比一次 `run.py` 便宜得多。

# 工具名 → 期望路由。**这张表是这个模式的全部语义**：
# 「模型调了 answer_kb」= 「它把这句话路由到了知识库」
_TOOL_ROUTE = {
    "answer_kb": "kb",
    "current_time": "time",
    "save_requirement": "agent",
    "generate_plan": "agent",
    "export_excel": "agent",
    "search_kb": "kb",  # M10 起没挂在主 Agent 上，留着是防它哪天被加回去
}


async def _live_probe(question: str, history: list, agent) -> tuple[str | None, str]:
    """跑一轮 Agent，返回 `(第一个调用的工具名, 它自己写的正文)`。

    两样都要，缺一样就分不清下面这两种「一个工具都没调」：

        「帮我出一份实施方案」→ 反问「要对接哪些平台？」   ← **正确**，需求收集
        「帮我写段 Python」   → 真的写了一段 Python        ← 越过工具直答

    ⚠️ **工具还是会被执行一次。** `FunctionToolCallEvent` 发出来的时候
    pydantic-ai 已经把这次调用排上了，跳出循环只能取消**后续**的轮次。
    但下面那个 deps 是空的，所有工具都会立刻返回一句人话（见 tools.py 第 2 条），
    一个外部接口都不打——所以这个模式仍然只花「一次决策」的钱。
    """
    from pydantic_ai import FunctionToolCallEvent, PartDeltaEvent, PartStartEvent
    from pydantic_ai.messages import TextPart, TextPartDelta

    from copilot.agent.deps import AgentDeps
    from copilot.agent.runner import to_message_history

    # 工具一个都不会执行（收到调用事件就跳出），所以 deps 里那些外部依赖
    # 可以是空的。**别在这里接真的 session/embedder**——接了就等于每道题
    # 都真去检索一次，这个模式就不便宜了
    deps = AgentDeps(
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        embedder=None,  # type: ignore[arg-type]
        question=question,
    )
    msgs = to_message_history([(role, text) for role, text in history]) if history else None
    said: list[str] = []
    async with agent.run_stream_events(question, deps=deps, message_history=msgs) as events:
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                return event.part.tool_name, "".join(said)
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                said.append(event.part.content or "")
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                said.append(event.delta.content_delta or "")
    return None, "".join(said)


def _is_a_question(text: str) -> bool:
    """这段话是在反问吗。

    **是个启发式，不是判定**：靠问号。需求收集的第一轮长这样——
    「您要对接哪些电商平台？（如淘宝、拼多多、抖音）」。
    真要严格判，得让它多跑一轮看会不会调 `save_requirement`，
    那要多花一次生成，而这个模式的全部价值就是便宜。
    """
    return "？" in text or "?" in text


def run_live(cases: list[dict]) -> list[CaseResult]:
    """让模型自己决定每一道题该去哪。串行——并发对一次决策没意义。"""
    import asyncio
    import logging

    from copilot.agent.agent import build_agent
    from copilot.qa import small_talk_kind

    agent = build_agent()
    # 工具会被执行一次并抱怨「deps 接线漏了」（见 `_live_first_tool`）。
    # 那句 ERROR 在生产里是有用的，在这里是噪声——而噪声看多了，
    # 真正的接线 bug 也会被当成噪声划过去
    logging.disable(logging.ERROR)

    async def main() -> list[CaseResult]:
        out: list[CaseResult] = []
        for i, c in enumerate(cases, 1):
            r = CaseResult(
                id=c["id"],
                kind=c["kind"],
                q=c["q"],
                expected=c["route"],
                accept=list(c.get("accept") or []),
            )
            # 寒暄短路仍在 Agent **之前**（M10 P2 的定位：缓存层，不是分叉），
            # ⚠️ 但**已经在收集需求的会话不走这条**——生产代码里那一行是
            # `if not plan_flow and small_talk_reply(...)`。少了这个条件，
            # 「明白了」会被短路成「不客气」，而线上根本不会这样
            # （`chat.py` 的 `_active_plan_flow`）。评测漏掉它，报出来的是
            # 一个早就不存在的 bug。
            in_flow = _history_started_agent(c.get("history") or [])
            kind = None if in_flow else small_talk_kind(c["q"])
            if kind is not None:
                r.actual = "capability" if kind == "capability" else "smalltalk"
            else:
                try:
                    tool, said = await _live_probe(c["q"], c.get("history") or [], agent)
                except Exception as e:  # noqa: BLE001 - 单题失败不该毁掉整轮
                    r.actual = f"__ERROR__{type(e).__name__}"
                else:
                    if tool:
                        r.actual = _TOOL_ROUTE.get(tool, "direct")
                    elif _is_a_question(said):
                        # 反问 = 开始收集需求。出方案那条路的第一轮本来就
                        # **不该**调工具（M7 验收原话：「没调工具，先问」）
                        r.actual = "agent"
                    elif looks_like_refusal(said):
                        # 一个工具都没调，但说的是「这不在我的范围里」。
                        # ⭐ 这**不是**越过工具直答，恰恰相反：instructions 里
                        # 「替用户做和这个产品无关的事……这些都不做」就是这么要求的。
                        # 2026-08-23 实测五道越界题，模型全是这个形态
                        # （「我只负责旺店通 ERP……不写代码」），却被记成 direct+bypass，
                        # 报表上看起来像五次越线。**把正确行为记成违规的指标，
                        # 会逼着人去修一个没坏的东西。**
                        r.actual = "refuse"
                    else:
                        # ⭐ 一个工具都没调，还给出了陈述句 = 它自己写了答案。
                        # 这就是「越过工具直答」，M10 要盯的那个硬指标
                        r.actual = "direct"
                        r.bypassed = bypassed_tool(said, used_kb=False)
            out.append(r)
            print(f"  [{i:2}/{len(cases)}] {c['id']:34} → {r.actual}")
        return out

    return asyncio.run(main())


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
    print(
        f"  越过工具直答率   {m.bypass_rate}%"
        "（确定性路由下结构上恒为 0；--live 下按答案内容判，划边界的拒答不算）"
    )
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
                "suite": "routing",  # 门禁靠它认出这份证据，见 eval/gate.py
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
    ap.add_argument(
        "--live",
        action="store_true",
        help="让真模型决定路由（M10 P3 之后的生效路径）。要花钱，但只花决策那一次",
    )
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    meta, cases = load_cases(args.only or None)
    results = run_live(cases) if args.live else run(cases)
    m = measure(results)
    tag = args.tag or datetime.now().strftime("run-%m%d-%H%M")
    report(results, m, f"{tag}（{'模型路由' if args.live else '确定性路由'}）")
    if args.tag:
        print(f"\n结果存在 {save(results, m, meta, tag)}")


if __name__ == "__main__":
    main()
