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


def test_run_gate_forces_utf8_on_the_child_process():
    """⚠️⚠️ **`encoding="utf-8"` 不是在设置子进程，是在断言子进程**——而这个断言曾经是假的。

    2026-09-02 部署时撞上：`gate.py` 在 Windows 上按 locale（cp936）写 stdout，
    父进程却声明 `encoding="utf-8"` 去解，于是整篇门禁报告变成一串 U+FFFD，
    再 `print()` 到 GBK 控制台时 `\\ufffd` 编不出来，当场：

        UnicodeEncodeError: 'gbk' codec can't encode character '\\ufffd'

    ⚠️ **坏在它长得像显示问题，实际动的是判定的输入——而且只坏一半。**
    `run_gate()` 从 `output` 里正则抠两样东西，实测（58 个 U+FFFD 的那份）：
    证据日期是纯 ASCII，穿过乱码照样抠到；语料指纹要匹配「当前语料指纹（…）：」
    这几个中文字，一乱就没了。于是摘要平静地打出「语料指纹：不可用」，
    而门禁状态走的是退出码，仍然显示 PASS。**一份看起来通过、却说不出
    自己验的是哪份语料的门禁记录**——比直接崩掉危险，因为崩掉会有人查。

    真正修的是那个假设：给子进程 `PYTHONUTF8=1`，让它真的按 UTF-8 写。
    父进程那一侧只放宽 `errors`（见 `eval/run.py` 里那段推理：中文在 GBK
    控制台本来打得出来，把 stdout 改成 utf-8 反而会把整篇变成乱码）。
    """
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)

        class _P:
            returncode = 0
            stdout = "✓ 门禁通过。\n当前语料指纹（flagship）：abc123\n"
            stderr = ""

        return _P()

    import subprocess

    real = subprocess.run
    subprocess.run = fake_run
    try:
        guard.run_gate()
    finally:
        subprocess.run = real

    env = captured.get("env")
    assert env is not None, "run_gate 没给子进程传 env——它会继承 GBK locale，输出解不出来"
    assert env.get("PYTHONUTF8") == "1", (
        f"子进程没有被强制成 UTF-8，encoding='utf-8' 这个声明就是假的：{env.get('PYTHONUTF8')!r}"
    )
    assert captured.get("encoding") == "utf-8", "父进程解码声明和子进程设置必须是同一个编码"
