"""部署门禁策略：失败不能静默越过，安全补丁也不能被永久卡死。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "eval_deploy_gate"
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "deploy_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()
COMMIT = "a" * 40


def test_pass_continues_without_confirmation():
    allowed, reason = guard.authorize(
        "PASS", "ai", interactive=False, confirmation="", commit=COMMIT
    )
    assert allowed
    assert "通过" in reason


def test_fail_is_not_silently_skipped_in_noninteractive_mode():
    allowed, reason = guard.authorize(
        "FAIL", "ai", interactive=False, confirmation="", commit=COMMIT
    )
    assert not allowed
    assert "非交互" in reason


def test_noninteractive_confirmation_is_bound_to_the_current_commit():
    assert guard.authorize(
        "FAIL", "ai", interactive=False, confirmation=COMMIT, commit=COMMIT
    )[0]
    assert not guard.authorize(
        "FAIL", "ai", interactive=False, confirmation="b" * 40, commit=COMMIT
    )[0]


def test_expired_evidence_does_not_permanently_block_a_security_fix():
    allowed, reason = guard.authorize(
        "UNRELIABLE", "security", interactive=False, confirmation="", commit=COMMIT
    )
    assert allowed
    assert "安全补丁" in reason


def test_fail_summary_uses_a_red_warning(monkeypatch, capsys):
    monkeypatch.setattr(
        guard,
        "run_gate",
        lambda: guard.GateSnapshot("FAIL", ("2026-08-29",), "abc123", ""),
    )
    monkeypatch.setattr(guard, "current_commit", lambda: COMMIT)
    monkeypatch.setattr(sys, "argv", ["deploy_gate.py", "other"])
    guard.main()
    output = capsys.readouterr().out
    assert "\033[31mFAIL\033[0m" in output
    assert "⛔ 评测门禁为 FAIL" in output
    assert "decision=ALLOW" in output


def test_deploy_script_keeps_the_unified_check_commands():
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "ruff check . ../tests ../eval" in script
    assert "npm run verify" in script
    assert "deploy_gate.py" in script
