"""部署前的评测门禁策略层。

`gate.py` 仍是唯一判定证据是否合格的地方；这里不复制阈值，只消费它的
PASS / FAIL / UNRELIABLE 退出码，再根据本次部署类型决定是否需要人工确认。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
STATUS_BY_CODE = {0: "PASS", 1: "FAIL", 2: "UNRELIABLE"}
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\s*$", re.MULTILINE)
_SHA_RE = re.compile(r"当前语料指纹（[^）]+）：([^\s]+)")


@dataclass(frozen=True)
class GateSnapshot:
    status: str
    evidence_dates: tuple[str, ...]
    corpus_sha: str
    output: str


def run_gate() -> GateSnapshot:
    """运行统一门禁并提取部署摘要；未知异常一律按 UNRELIABLE。"""
    proc = subprocess.run(
        [sys.executable, str(EVAL_DIR / "gate.py")],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    status = STATUS_BY_CODE.get(proc.returncode, "UNRELIABLE")
    dates = tuple(sorted(set(_DATE_RE.findall(output))))
    sha_match = _SHA_RE.search(output)
    corpus_sha = sha_match.group(1) if sha_match else "不可用"
    return GateSnapshot(status, dates, corpus_sha, output)


def current_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def authorize(
    status: str,
    scope: str,
    *,
    interactive: bool,
    confirmation: str,
    commit: str,
    ask: Callable[[str], str] = input,
) -> tuple[bool, str]:
    """返回是否允许部署，以及能写进审计日志的理由。"""
    if status == "PASS":
        return True, "门禁已通过"
    if scope == "security":
        return True, "紧急安全补丁路径；保留未通过门禁的审计记录"
    if scope == "other":
        return True, "不涉及检索、Prompt、Agent 或路由；保留门禁状态记录"
    if confirmation == commit and commit != "unknown":
        return True, "使用与当前提交绑定的一次性人工确认"
    if not interactive:
        return False, "非交互环境禁止静默越过未通过的 AI 门禁"

    entered = ask(f"门禁未通过。确认部署当前提交请输入完整 commit {commit}: ")
    if entered.strip() == commit:
        return True, "交互式人工确认了当前提交"
    return False, "人工确认不匹配，已中止"


def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def main() -> None:
    parser = argparse.ArgumentParser(description="部署评测门禁")
    parser.add_argument("scope", choices=("ai", "other", "security"))
    args = parser.parse_args()

    snapshot = run_gate()
    if snapshot.output:
        print(snapshot.output)

    color = {"PASS": "32", "FAIL": "31", "UNRELIABLE": "33"}[snapshot.status]
    print("\n==> 部署评测摘要")
    print(f"当前评测门禁：{_color(snapshot.status, color)}")
    if snapshot.evidence_dates:
        print(f"证据时间：{snapshot.evidence_dates[0]} — {snapshot.evidence_dates[-1]}")
    else:
        print("证据时间：无可用证据")
    print(f"语料指纹：{snapshot.corpus_sha}")
    touches_ai = args.scope == "ai"
    print(f"本次是否涉及检索、Prompt、Agent、路由：{'是' if touches_ai else '否'}")
    if args.scope == "security":
        print("部署类型：紧急安全补丁")

    commit = current_commit()
    allowed, reason = authorize(
        snapshot.status,
        args.scope,
        interactive=sys.stdin.isatty(),
        confirmation=os.getenv("COPILOT_GATE_CONFIRM", ""),
        commit=commit,
    )
    decision = "ALLOW" if allowed else "BLOCK"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(
        "DEPLOY_AUDIT "
        f"time={stamp} commit={commit} scope={args.scope} "
        f"gate={snapshot.status} decision={decision} reason={reason}"
    )
    if snapshot.status == "FAIL":
        print(_color("⛔ 评测门禁为 FAIL。", "31"))
    elif snapshot.status == "UNRELIABLE":
        print(_color("⚠️ 评测门禁为 UNRELIABLE。", "33"))
    if not allowed:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
