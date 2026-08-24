"""评测门禁（M19-A）：**手上的证据够不够放行下一步。**

    uv run python ../eval/gate.py              # 查一遍，打表，退出码 0/1/2
    uv run python ../eval/gate.py --no-corpus  # 连不上库时用：跳过语料指纹核对

（在 `backend/` 下用 `uv run` 执行，才 import 得到 copilot 包。）

⭐ **它检查证据，不制造证据。** 全套评测跑一次是两百多次付费调用、十几分钟；
而"能不能放行"这个问题在提交前、部署前、导入前各要问一次。所以门禁读
`eval/results/` 里最新的那几轮，逐条对照 `eval/gate.yaml` 的契约。

三种结局，**分得开**才有意义：

    PASS         指标达标 + 结果可信 + 证据没过期 + 语料指纹对得上
    FAIL         有指标破线，或者压根没有这一套的证据
    UNRELIABLE   有证据，但判分失效率超线、或者跑的不是现在这份语料

⚠️⚠️ **UNRELIABLE 不是通过，也不是失败。** 判分器欠费那天的一轮 88.5%，
既不能说明模型退化了，也不能说明它没退化——那 5 题根本没评上（M13 P0）。
把它当通过，等于让门禁在判分器掉线时自动放行；把它当失败，等于
让国内到判分器的网络质量决定能不能上线。所以它是第三种结局，退出码 2。

⚠️ **"语料指纹对不上"也归 UNRELIABLE**，不归 FAIL：那一轮的数字本身没问题，
只是它量的已经不是现在这份语料了。要做的是重跑，不是回滚代码。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import run as base  # noqa: E402  —— 语料指纹、结果目录都从那边来

CONTRACT = EVAL_DIR / "gate.yaml"
RESULTS_DIR = base.RESULTS_DIR

PASS, FAIL, UNRELIABLE = "PASS", "FAIL", "UNRELIABLE"
EXIT_CODE = {PASS: 0, FAIL: 1, UNRELIABLE: 2}

_RULE_RE = re.compile(r"^(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)$")


def check_threshold(value, rule: str) -> bool:
    """`">=95"` / `"==0"` 这种规则串。

    ⚠️ 指标缺项（`None`）一律**不算通过**。缺项的来路是"这一轮没量到这个数"
    ——比如 Agent 路上量不了配图串台——而"没量到"当然不能当作"达标了"。
    """
    m = _RULE_RE.match(rule.strip())
    if not m:
        raise SystemExit(f"gate.yaml 里的阈值写法不认识：{rule!r}")
    op, want = m.group(1), float(m.group(2))
    if value is None:
        return False
    got = float(value)
    return {
        ">=": got >= want,
        "<=": got <= want,
        "==": got == want,
        ">": got > want,
        "<": got < want,
    }[op]


def load_runs() -> list[dict]:
    """把 `eval/results/` 里的结果读成一张表。

    老结果没有 `suite` / `scope` 字段（M19-A 之前存的），这里按文件名和
    config 里的线索补出来——**不改老文件**：改历史结果等于事后篡改证据。
    """
    runs: list[dict] = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict) or "metrics" not in data:
            continue
        cfg = data.get("config") or {}
        suite = data.get("suite")
        if suite is None:
            # 补：routing 按文件名，别的按 config 的形状
            suite = "routing" if path.name.startswith("routing-") else "dataset"
        data["_suite"] = suite
        data["_path"] = cfg.get("path", "direct")
        data["_space"] = cfg.get("space", base.DEFAULT_SPACE)
        # ⚠️ **老结果缺 `scope` 时不能默认成 public。** 默认 public 的后果是
        # 一轮私有库的结果被当成公共库的证据放行——两组题打的是不同的文档集，
        # 数字长得一样，而报告上完全看不出来（2026-08-24 第一版就这么错过一次）。
        # 认不出来就标 unknown，让它匹配不上任何一条要求
        data["_scope"] = data.get("scope") or "unknown"
        data["_corpus_sha"] = cfg.get("corpus_sha", "")
        data["_file"] = path.name
        data["_ran_at"] = data.get("ran_at", "")
        runs.append(data)
    return runs


def matches(run: dict, req: dict) -> bool:
    if run["_suite"] != req["suite"]:
        return False
    for field, key in (("scope", "_scope"), ("path", "_path"), ("space", "_space")):
        if field in req and run[key] != req[field]:
            return False
    return True


def newest(runs: list[dict], req: dict) -> dict | None:
    hits = [r for r in runs if matches(r, req)]
    return max(hits, key=lambda r: r["_ran_at"]) if hits else None


def age_days(run: dict) -> float | None:
    stamp = run.get("_ran_at") or ""
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds() / 86400


def current_corpus_sha(space_code: str) -> str:
    """现在库里那份语料的指纹。跑不通就抛，由调用方决定怎么办。"""
    from copilot import spaces
    from copilot.db.session import SessionLocal, engine

    async def go() -> str:
        try:
            async with SessionLocal() as s:
                space = await spaces.by_code(s, space_code)
                common = await spaces.common_id(s)
                return (await base.corpus_fingerprint(s, space.id, common))["corpus_sha"]
        finally:
            await engine.dispose()

    return asyncio.run(go())


def judge_one(
    run: dict | None, req: dict, corpus_sha: str | None, stale: int = 0
) -> tuple[str, list[str]]:
    """一条要求的结论 + 说明。"""
    if run is None:
        why = ["没有这一套的结果，先跑一遍"]
        if stale:
            # 有同一套题的老结果、但缺 scope/space 这些字段。说清楚，
            # 否则人会以为文件不见了，在 results/ 里找半天
            why.append(f"（有 {stale} 轮同类结果，但缺 M19-A 的空间/范围字段，不作数）")
        return FAIL, why

    notes: list[str] = []
    if (age := age_days(run)) is not None and age > req.get("max_age_days", 30):
        notes.append(f"证据过期：{age:.0f} 天前跑的（上限 {req['max_age_days']} 天）")

    broken = [
        f"{k} {run['metrics'].get(k)}（要 {rule}）"
        for k, rule in (req.get("thresholds") or {}).items()
        if not check_threshold(run["metrics"].get(k), rule)
    ]

    # ⭐ 顺序是刻意的：**先看破没破线，再看可不可信**。
    # 反过来的话，一轮判分器掉线、同时幻觉率破线的结果会被报成 UNRELIABLE，
    # 而幻觉率是规则判定的——它破线这件事和判分器一点关系都没有，
    # 会被"不可信"这个标签盖住
    if broken:
        return FAIL, notes + broken
    if not run.get("reliable", True):
        return UNRELIABLE, [*notes, "判分失效率超线：这一轮的语义指标不能当作通过"]
    if corpus_sha is not None and run["_corpus_sha"] and run["_corpus_sha"] != corpus_sha:
        return UNRELIABLE, [*notes, f"语料变了：证据是 {run['_corpus_sha']}，现在是 {corpus_sha}"]
    if corpus_sha is not None and not run["_corpus_sha"]:
        notes.append("这轮结果里没有语料指纹（M19-A 之前跑的），没法核对是不是同一份语料")
        return UNRELIABLE, notes
    if notes:  # 只剩过期这一条
        return UNRELIABLE, notes
    return PASS, []


def main() -> None:
    import yaml

    ap = argparse.ArgumentParser(description="评测门禁（M19-A）")
    ap.add_argument(
        "--no-corpus",
        action="store_true",
        help="跳过语料指纹核对（连不上库时）。⚠️ 跳过之后这次门禁只能当参考",
    )
    args = ap.parse_args()

    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    runs = load_runs()

    corpus_sha: str | None = None
    corpus_note = ""
    if args.no_corpus:
        corpus_note = "⚠️ 跳过了语料指纹核对（--no-corpus）：这次结论只能当参考"
    else:
        space = (contract.get("corpus") or {}).get("space", base.DEFAULT_SPACE)
        try:
            corpus_sha = current_corpus_sha(space)
            corpus_note = f"当前语料指纹（{space}）：{corpus_sha}"
        except Exception as e:  # noqa: BLE001 - 连不上库不该让门禁崩掉
            corpus_note = f"⚠️ 算不出当前语料指纹（{type(e).__name__}），全部记 UNRELIABLE"
            corpus_sha = "__unavailable__"

    check_scope = (contract.get("corpus") or {}).get("check_scope", "public")

    print()
    print("=" * 78)
    print("  评测门禁（M19-A）")
    print("=" * 78)
    print(f"  {corpus_note}")
    print()

    verdicts: list[str] = []
    for req in contract["requirements"]:
        run = newest(runs, req)
        stale = sum(1 for r in runs if r["_suite"] == req["suite"]) if run is None else 0
        # 两种情况不核对语料指纹：
        #   私有范围   指纹带着某个用户自己的文档，换个人跑就不一样
        #   与语料无关 路由题一块语料都不读（见 gate.yaml 里那条注释）
        want_sha = corpus_sha if req.get("scope", "public") == check_scope else None
        if not req.get("corpus_check", True):
            want_sha = None
        verdict, notes = judge_one(run, req, want_sha, stale)
        verdicts.append(verdict)
        mark = {PASS: "✓", FAIL: "⛔", UNRELIABLE: "？"}[verdict]
        tag = run["tag"] if run else "—"
        when = (run["_ran_at"][:10] if run else "—") if run else "—"
        print(f"  {mark} {verdict:<11} {req['label']:<22} {tag:<26} {when}")
        for note in notes:
            print(f"        {note}")

    print()
    if FAIL in verdicts:
        result = FAIL
        print("  ⛔ 门禁不通过：上面标 FAIL 的那几条要么破线、要么没有证据。")
    elif UNRELIABLE in verdicts:
        result = UNRELIABLE
        print("  ？ 门禁**没有通过，也不算失败**：证据不可信或已过期，重跑那几套。")
        print("     （判分器掉线的数字既不能算模型好也不能算模型坏——见 M13 P0）")
    else:
        result = PASS
        print("  ✓ 门禁通过。")
    print()
    sys.exit(EXIT_CODE[result])


if __name__ == "__main__":
    main()
