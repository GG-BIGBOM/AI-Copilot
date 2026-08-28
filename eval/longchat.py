"""长会话评测（W2.1）：量的是「聊到第 15 轮，第 1 轮说过的东西还在不在」。

    uv run python ../eval/longchat.py --check              # 免费，不调 LLM
    uv run python ../eval/longchat.py --tag w21-before
    SESSION_FACTS_ENABLED=true uv run python ../eval/longchat.py --tag w21-facts
    uv run python ../eval/longchat.py --compare w21-before w21-facts

（在 `backend/` 下用 `uv run` 执行，才 import 得到 copilot 包。）

⭐ **为什么它是独立的一份，而不是 dataset.yaml 的几道新题。**

两个理由，第二个是硬的：

1. 坐标轴不一样。`dataset.yaml` 量的是 fact/probe/partial/no_answer（准确率与
   幻觉率），这份量的是「窗口外的东西找不找得回」。混在一起两边都算不清，
   而且历史 tag 之间的 `--compare` 会变成拿两个不同的题集比大小。
2. **形状不一样。** 那边每道题是一句话，`run.py` 两阶段跑（检索一次、生成一次），
   全程不带 history；这里每道题是一整条会话加一个探针。塞不进去。

判分口径（三态、判分器失败不算答错）仍然复用 `run.py`，不另写一套。

─────────────────────────────────────────────────────────
⭐⭐ **两档指标，一免费一收费。这个区分是这个脚本的全部要点。**

    context_hit   `--check`。装配好这一轮要送进模型的消息，看答案所需的那个
                  字串**在不在里面**。不在 = 模型只能猜。零成本、确定性判定。
    resolved      `--tag`。真把答案生成出来判分。

为什么要有免费那一档：W2.1 的规矩是「改前必须先量基线」。而如果量一次基线
就要打几百次 LLM，那条规矩在实践中就会被跳过——**一条要花钱才能遵守的规矩，
迟早会变成一条没人遵守的规矩**。`--check` 让"改前先量"这件事的成本降到零。

⚠️ **`--check` 只装配「系统指令 + 历史 + 本轮问题」，不做检索。**
检索要打 embedding 接口（要钱），而这份题集测的恰恰是 W2.1 / W2.2 动的那一块
（历史窗口与事实表），不是检索。所以这个近似不是偷懒，是把量的东西对准。
代价说清楚：`context_needle` 为空的那几道题（must_refuse / 对照组里的产品题）
在 `--check` 下**量不到东西**，它们只在收费那一档有意义。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import run as base  # noqa: E402  —— 判分口径、生成、报告一律复用，不另写一套

DATASET = EVAL_DIR / "longchat.yaml"
RESULTS_DIR = base.RESULTS_DIR

CATEGORIES = (
    "cross_window_fact",
    "cross_window_ref",
    "in_window_control",
    "must_refuse",
)


@dataclass
class ChatResult:
    id: str
    category: str
    probe: str
    turns: int = 0
    # ---- 免费那一档 ----
    # 答案所需的字串在不在装配好的上下文里。None = 这道题没有 needle（不参与）
    context_hit: bool | None = None
    # 装配好之后送进模型的全文，出报告时不打，存进结果文件供事后翻查
    assembled: str = ""
    # ---- 收费那一档 ----
    answer: str = ""
    missing: list[str] = field(default_factory=list)  # must_include 里没出现的
    banned: list[str] = field(default_factory=list)  # must_not_include 里出现了的
    error: str = ""

    @property
    def resolved(self) -> bool | None:
        """这道题答对了没有。没生成答案就是 None（`--check` 那一档）。"""
        if not self.answer:
            return None
        return not self.missing and not self.banned


def load_cases(only: str | None = None) -> tuple[dict, list[dict]]:
    import yaml

    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = list(data["cases"])
    for c in cases:
        if c.get("category") not in CATEGORIES:
            raise SystemExit(f"{c['id']}：category={c.get('category')!r} 不在 {CATEGORIES}")
        # ⚠️ `turns` 里可以塞一个 YAML 锚点（一串填充轮），这里摊平。
        # 摊平放在读题这一步做，后面所有代码就只看见一个字符串列表
        flat: list[str] = []
        for t in c.get("turns", []):
            flat.extend(t) if isinstance(t, list) else flat.append(t)
        c["turns"] = flat
    if only:
        keys = {k.strip() for k in only.split(",") if k.strip()}
        cases = [c for c in cases if c["id"] in keys or c["category"] in keys]
    return data.get("meta", {}), cases


# ---------- 装配（免费那一档的全部内容）----------


def _fake_history(turns: list[str]) -> list[tuple[str, str]]:
    """把用户发言列表补成 (role, content) 的完整历史。

    ⚠️ **助手那半边不能省。** `qa.HISTORY_TURNS` 数的是**条数**（user 和
    assistant 各算一条），只塞用户发言的话，窗口里能装下的轮数会凭空翻一倍——
    量出来的窗口压力比线上小一半，而报告上完全看不出来。

    助手的回答用一句固定话术占位。内容不重要，长度和条数才重要。
    """
    out: list[tuple[str, str]] = []
    for t in turns:
        out.append(("user", t))
        out.append(("assistant", "好的，已经为你查到相关内容。"))
    return out


def _facts_for(case: dict, facts_on: bool):
    """**模拟**路由层记事实那一步（`routes/chat._record_facts`）。

    版本从会话记录来（这条会话是旗舰版），客户名从用户原话里抽。
    ⚠️ 日均单量这类**没人说过**的一律不记——`must_refuse` 那两道题量的
    就是"没记的到底会不会被编出来"，先在这里替它记一条就把题作废了。
    """
    from copilot.qa import named_subject
    from copilot.session_facts import SessionFacts

    facts = SessionFacts()
    if not facts_on:
        return facts
    facts.note("knowledge_space", "旗舰版", 1)
    for i, t in enumerate(case["turns"], start=1):
        if (name := named_subject(t)) is not None:
            facts.note("subject", name, i)
    return facts


def assemble(case: dict, facts_on: bool) -> tuple[str, str]:
    """按线上那条路装配这一轮的消息。返回 `(全文, 带得动的那部分)`。

    ⚠️ 走的是 `qa.assemble_messages` 和 `qa.system_prompt_for` ——**线上那两个
    函数本身**，不是抄一份。抄一份的话，改了线上忘了改这边，
    评测会一直在报告一个早就不存在的系统（这条规矩见 `qa.needs_subject_guard`
    的 docstring，这个项目里已经踩过一次）。

    ⚠️⚠️ **第二个返回值是这个函数最要紧的部分，也是一个 bug 修出来的。**
    第一版拿"全文"去找 needle，结果 `lc-version-asked-late` 在开关**关着**时
    就已经"命中"了——因为固定的 system prompt 第一句是
    「你是一名旺店通**旗舰版** ERP 的实施顾问助手」，铁律第 **4** 条那个序号
    也让 `lc-warehouse-count` 的 needle「4」凭空命中。
    也就是说 4 道跨窗口题里有 2 道的基线是假的。

    ⭐ 判据因此改成：needle 只能出现在**这一轮真正带得动的东西**里 ——
    事实表那一段 + 历史窗口。固定的 system prompt 模板是常量，
    它出现什么词都不构成"记住了"。
    """
    from copilot.qa import assemble_messages, system_prompt_for

    facts = _facts_for(case, facts_on)
    block = facts.human()
    history = _fake_history(case["turns"])
    messages = assemble_messages(
        system_prompt_for("fast", facts=block), history, "", case["probe"]
    )
    full = "\n".join(m["content"] for m in messages)
    # 只有这两样是随会话变的。**探针本身不算**——needle 出现在用户这一问里
    # 不说明系统记住了什么，那是用户又说了一遍
    carried = block + "\n" + "\n".join(m["content"] for m in messages[1:-1])
    return full, carried


def check(cases: list[dict], facts_on: bool) -> list[ChatResult]:
    """免费那一档：答案所需的字串，在不在这一轮带得动的东西里。"""
    out: list[ChatResult] = []
    for c in cases:
        full, carried = assemble(c, facts_on)
        needle = (c.get("context_needle") or "").strip()
        out.append(
            ChatResult(
                id=c["id"],
                category=c["category"],
                probe=c["probe"],
                turns=len(c["turns"]),
                context_hit=(needle in carried) if needle else None,
                assembled=full,
            )
        )
    return out


# ---------- 收费那一档 ----------


def answer_all(cases: list[dict], results: list[ChatResult], workers: int, facts_on: bool) -> None:
    """真跑一遍：检索 + 生成。**每道题都要单独检索**——探针问题各不相同。

    ⚠️ 走的是 `qa.ask_stream` 整条，不是自己拼 messages 再调模型。
    这一层的全部意义就是"和线上跑同一条路"，绕过去就什么都没量到。
    """
    import asyncio

    from copilot.db.session import SessionLocal
    from copilot.providers.llm import ChatLLM
    from copilot.providers.siliconflow import (
        SiliconFlowClient,
        SiliconFlowEmbedder,
        SiliconFlowReranker,
    )
    from copilot.qa import ask_stream

    by_id = {r.id: r for r in results}

    async def one(case: dict, session, emb, rr, llm) -> None:
        from copilot import spaces

        cr = by_id[case["id"]]
        facts = _facts_for(case, facts_on)
        try:
            streamed = await ask_stream(
                session,
                case["probe"],
                emb,
                rr,
                llm,
                user_id=None,
                space_id=(await spaces.by_code(session, "flagship")).id,
                history=_fake_history(case["turns"]),
                facts=facts.human(),
            )
            cr.answer = "".join(t for kind, t in streamed.stream if kind == "content")
        except Exception as e:  # noqa: BLE001 - 一道题挂了不该毁掉整轮
            cr.error = f"{type(e).__name__}: {e}"
            return
        cr.missing = _missing(cr.answer, case.get("must_include") or [])
        cr.banned = [w for w in (case.get("must_not_include") or []) if w in cr.answer]

    async def main() -> None:
        client = SiliconFlowClient()
        emb, rr = SiliconFlowEmbedder(client=client), SiliconFlowReranker(client=client)
        llm = ChatLLM()
        async with SessionLocal() as session:
            for i, case in enumerate(cases, 1):
                await one(case, session, emb, rr, llm)
                cr = by_id[case["id"]]
                mark = "✗" if cr.resolved is False else ("✓" if cr.resolved else "—")
                print(f"  [{i:2}/{len(cases)}] {mark} {case['id']}")
        client.close()

    # `workers` 收下但暂时不用：这条路要串行开同一个 session（`ask_stream`
    # 拿的是 AsyncSession，不是线程安全的）。留着是为了和 run.py 同签名
    _ = ThreadPoolExecutor
    asyncio.run(base._with_fresh_pool(main()))


def _missing(answer: str, wanted: list[str]) -> list[str]:
    """`must_include` 是**任一命中即可**（同义写法列表），全都没有才算缺。

    ⚠️ 和 `run.missing_facts` 的「每一条都要出现」不一样，因为这里列的是
    「无法确认 / 具体指什么 / 说出功能名称」这种同一件事的几种说法。
    写成"每条都要"的话，一句完全正确的澄清会因为措辞不同被判错。
    """
    return [] if (not wanted or any(w in answer for w in wanted)) else list(wanted)


# ---------- 报告 ----------


def score(results: list[ChatResult]) -> dict:
    m: dict = {"题数": len(results)}

    checkable = [r for r in results if r.context_hit is not None]
    if checkable:
        hit = sum(1 for r in checkable if r.context_hit)
        m["可解析题数"] = len(checkable)
        m["上下文命中率"] = round(100 * hit / len(checkable), 1)

    judged = [r for r in results if r.resolved is not None]
    if judged:
        ok = sum(1 for r in judged if r.resolved)
        m["已判题数"] = len(judged)
        m["跨窗口解析成功率"] = round(100 * ok / len(judged), 1)

    for cat in CATEGORIES:
        group = [r for r in results if r.category == cat]
        if not group:
            continue
        c = [r for r in group if r.context_hit is not None]
        j = [r for r in group if r.resolved is not None]
        row: dict = {"题数": len(group)}
        if c:
            row["上下文命中"] = f"{sum(1 for r in c if r.context_hit)}/{len(c)}"
        if j:
            row["答对"] = f"{sum(1 for r in j if r.resolved)}/{len(j)}"
        m[cat] = row
    return m


def print_report(meta: dict, metrics: dict, results: list[ChatResult], facts_on: bool) -> None:
    print()
    print("═" * 62)
    print(f"  长会话评测   题集 {DATASET.name}   built_at {meta.get('built_at', '?')}")
    print(f"  HISTORY_TURNS(题集构建时) {meta.get('history_turns_at_build', '?')}"
          f"   SESSION_FACTS_ENABLED={'true' if facts_on else 'false'}")
    print("═" * 62)
    for k in ("题数", "可解析题数", "上下文命中率", "已判题数", "跨窗口解析成功率"):
        if k in metrics:
            v = metrics[k]
            print(f"  {k:<16} {v}{'%' if k.endswith('率') else ''}")
    print()
    for cat in CATEGORIES:
        if cat in metrics:
            row = metrics[cat]
            bits = "  ".join(f"{k} {v}" for k, v in row.items())
            print(f"  {cat:<20} {bits}")

    bad = [r for r in results if r.context_hit is False or r.resolved is False or r.error]
    if bad:
        print()
        print("── 没过的题 ──")
        for r in bad:
            why = r.error or (
                "上下文里没有" if r.context_hit is False else
                (f"缺 {r.missing}" if r.missing else f"出现了不该有的 {r.banned}")
            )
            print(f"  ✗ {r.id:36} {why}")
    print()


def save(tag: str, meta: dict, metrics: dict, results: list[ChatResult], facts_on: bool) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{tag}.json"
    path.write_text(
        json.dumps(
            {
                "suite": "longchat",
                "tag": tag,
                # ⚠️ 时间戳要留：这份题集的分数只在同一个 HISTORY_TURNS 下可比，
                # 而那个常量改过之后，旧 tag 就只是历史了
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "dataset_meta": meta,
                "session_facts_enabled": facts_on,
                "metrics": metrics,
                "cases": [asdict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def compare(tags: list[str]) -> None:
    runs = []
    for t in tags:
        p = RESULTS_DIR / f"{t}.json"
        if not p.exists():
            raise SystemExit(f"没有这一轮的结果：{p}")
        runs.append(json.loads(p.read_text(encoding="utf-8")))

    print()
    print(f"  {'指标':<20}" + "".join(f"{t:>22}" for t in tags))
    print("  " + "─" * (20 + 22 * len(tags)))
    for k in ("上下文命中率", "跨窗口解析成功率"):
        if any(k in r["metrics"] for r in runs):
            cells = "".join(f"{r['metrics'].get(k, '—')!s:>22}" for r in runs)
            print(f"  {k:<20}{cells}")
    print()
    print("  ── 逐题（只列有变化的）──")
    by_tag = [{c["id"]: c for c in r["cases"]} for r in runs]
    for cid in by_tag[0]:
        cells = []
        for d in by_tag:
            c = d.get(cid)
            if c is None:
                cells.append("—")
            elif c["answer"]:
                cells.append("对" if not c["missing"] and not c["banned"] else "错")
            elif c["context_hit"] is None:
                cells.append("—")
            else:
                cells.append("有" if c["context_hit"] else "无")
        if len(set(cells)) > 1:
            print(f"  {cid:<36}" + "".join(f"{c:>10}" for c in cells))
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="长会话评测（W2.1 上下文装配）")
    ap.add_argument("--tag", default="", help="这轮的名字，结果存 results/<tag>.json")
    ap.add_argument("--check", action="store_true", help="只装配上下文、不调 LLM（免费）")
    ap.add_argument("--compare", nargs="+", metavar="TAG", help="对比若干轮结果")
    ap.add_argument("--only", default="", help="只跑指定 id 或 category，逗号分隔")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return

    from copilot.config import get_settings

    facts_on = get_settings().session_facts_enabled
    meta, cases = load_cases(args.only or None)
    print(f"题集 {DATASET.name}：{len(cases)} 道，SESSION_FACTS_ENABLED={facts_on}")

    results = check(cases, facts_on)
    if not args.check:
        if not args.tag:
            raise SystemExit("跑全量要给 --tag（不然结果没处存，也没法 --compare）")
        t0 = time.monotonic()
        answer_all(cases, results, args.workers, facts_on)
        print(f"  生成用时 {time.monotonic() - t0:.0f}s")

    metrics = score(results)
    print_report(meta, metrics, results, facts_on)
    if args.tag:
        print(f"  结果写到 {save(args.tag, meta, metrics, results, facts_on)}")


if __name__ == "__main__":
    main()
