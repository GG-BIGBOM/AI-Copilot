"""门禁的判定口径（M19-A）。

门禁是**唯一一个"能不能上线"由代码说了算**的地方。它读 `eval/results/` 里
已有的结果，对照 `eval/gate.yaml` 的契约给出 PASS / FAIL / UNRELIABLE。
这三个结论的边界要是模糊了，后果不是报告难看，是**在证据不成立的时候放行**。

所以这一层和 `test_eval_scoring.py` 一样是纯函数测试：不连库、不调模型、
不读真实结果目录，手工造出每一种形状的证据。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    # gate 自己 `import run as base`，所以 eval/ 要在 path 上
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load("eval_gate", "gate.py")


def run(**kw) -> dict:
    """一份最小的、通过的证据。各条测试只改自己关心的那一处。"""
    base = {
        "tag": "t",
        "_suite": "dataset",
        "_scope": "public",
        "_path": "direct",
        "_space": "flagship",
        "_corpus_sha": "abc123",
        "_ran_at": gate.datetime.now(gate.UTC).isoformat(timespec="seconds"),
        "reliable": True,
        "metrics": {"准确率": 98.0, "幻觉率": 0.0},
    }
    base.update(kw)
    return base


REQ = {
    "key": "flagship-public-direct",
    "label": "公共库 · 直路",
    "suite": "dataset",
    "scope": "public",
    "path": "direct",
    "space": "flagship",
    "max_age_days": 30,
    "thresholds": {"准确率": ">=95", "幻觉率": "==0"},
}


# ─────────────────────────────────────────────────────────
# 三种结局分得开
# ─────────────────────────────────────────────────────────


def test_a_clean_run_passes():
    assert gate.judge_one(run(), REQ, "abc123") == (gate.PASS, [])


def test_a_broken_red_line_fails():
    verdict, why = gate.judge_one(run(metrics={"准确率": 98.0, "幻觉率": 2.0}), REQ, "abc123")
    assert verdict == gate.FAIL
    assert "幻觉率" in why[0]


def test_an_unreliable_run_is_neither_pass_nor_fail():
    """判分失效超线 = 第三种结局。**不能当通过，也不能当失败。**

    当通过 → 判分器掉线那天门禁自动放行；
    当失败 → 国内到判分器的网络质量决定能不能上线。
    """
    verdict, why = gate.judge_one(run(reliable=False), REQ, "abc123")
    assert verdict == gate.UNRELIABLE
    assert "判分失效" in why[0]
    assert gate.EXIT_CODE[gate.UNRELIABLE] == 2
    assert (gate.EXIT_CODE[gate.PASS], gate.EXIT_CODE[gate.FAIL]) == (0, 1)


def test_a_broken_red_line_beats_an_unreliable_judge():
    """⭐ 顺序：**先看破没破线，再看可不可信**。

    幻觉率是规则判定的——判分器挂没挂，它破线这件事都成立。
    先看可信度的话，这条会被"不可信"这个标签盖住，而它才是要命的那条。
    """
    verdict, why = gate.judge_one(
        run(reliable=False, metrics={"准确率": 98.0, "幻觉率": 3.0}), REQ, "abc123"
    )
    assert verdict == gate.FAIL
    assert any("幻觉率" in w for w in why)


def test_missing_evidence_is_a_failure_not_a_pass():
    verdict, why = gate.judge_one(None, REQ, "abc123")
    assert verdict == gate.FAIL
    assert "先跑一遍" in why[0]


# ─────────────────────────────────────────────────────────
# 证据要对得上：语料、时间、范围
# ─────────────────────────────────────────────────────────


def test_a_stale_corpus_makes_the_evidence_unreliable():
    """指纹对不上 = 这一轮量的不是现在这份语料。

    归 UNRELIABLE 而不是 FAIL：数字本身没问题，要做的是重跑，不是回滚代码。
    """
    verdict, why = gate.judge_one(run(_corpus_sha="old999"), REQ, "abc123")
    assert verdict == gate.UNRELIABLE
    assert "语料变了" in why[0]


def test_evidence_without_a_fingerprint_cannot_be_trusted():
    """M19-A 之前跑的结果没有指纹——没法核对，就不能算通过。"""
    verdict, why = gate.judge_one(run(_corpus_sha=""), REQ, "abc123")
    assert verdict == gate.UNRELIABLE
    assert "没有语料指纹" in why[0]


def test_expired_evidence_is_unreliable():
    old = gate.datetime.now(gate.UTC).replace(year=2025).isoformat(timespec="seconds")
    verdict, why = gate.judge_one(run(_ran_at=old), REQ, "abc123")
    assert verdict == gate.UNRELIABLE
    assert "过期" in why[0]


def test_a_private_run_is_never_taken_as_public_evidence():
    """⚠️ **老结果缺 `scope` 时不能默认成 public。**

    2026-08-24 第一版就这么错了一次：私有库那一轮被当成公共库的证据，
    门禁照常打勾——两组题打的是不同的文档集，而数字长得一模一样。
    """
    assert gate.matches(run(_scope="private"), REQ) is False
    assert gate.matches(run(_scope="unknown"), REQ) is False
    assert gate.matches(run(), REQ) is True


def test_old_results_without_a_scope_are_marked_unknown(tmp_path, monkeypatch):
    """真正咬住那次误判的一条：**从磁盘读进来的老结果**不许被当成 public。

    上面那条验的是 `matches()` 的判据，这条验的是读文件时补出来的那个值——
    错误当初就发生在这里（`data.get("scope", "public")`）。
    """
    import json

    (tmp_path / "old-private.json").write_text(
        json.dumps({"tag": "old", "ran_at": "2026-08-23T00:00:00+00:00",
                    "config": {"path": "direct"}, "metrics": {"准确率": 100.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "RESULTS_DIR", tmp_path)
    runs = gate.load_runs()
    assert [r["_scope"] for r in runs] == ["unknown"]
    assert gate.newest(runs, REQ) is None


def test_the_newest_matching_run_wins():
    older = run(tag="old", _ran_at="2026-08-01T00:00:00+00:00")
    newer = run(tag="new", _ran_at="2026-08-20T00:00:00+00:00")
    other = run(tag="agent", _path="agent")
    assert gate.newest([older, newer, other], REQ)["tag"] == "new"


# ─────────────────────────────────────────────────────────
# 阈值语法
# ─────────────────────────────────────────────────────────


def test_threshold_rules():
    assert gate.check_threshold(0.0, "==0")
    assert not gate.check_threshold(0.1, "==0")
    assert gate.check_threshold(95.0, ">=95")
    assert not gate.check_threshold(94.9, ">=95")
    assert gate.check_threshold(3.0, "<=5")


def test_a_missing_metric_never_passes():
    """「这一轮没量到这个数」不能当作「达标了」。

    最典型的是 Agent 路上的配图串台率：图片没有出处，指标缺项。
    缺项当通过的话，门禁会在**最该发现问题的地方**打勾。
    """
    assert not gate.check_threshold(None, "==0")
    assert not gate.check_threshold(None, ">=0")


def test_a_corpus_independent_suite_is_not_asked_for_a_fingerprint():
    """路由题一块语料都不读——要它带语料指纹，这一条会永远停在 UNRELIABLE。

    而理由是个假的：这一轮的数字和语料变没变毫无关系。门禁里挂着一条
    永远亮着的黄灯，久了就没人看门禁了。
    """
    req = {**REQ, "suite": "routing", "corpus_check": False}
    del req["scope"], req["path"], req["space"]
    routing_run = run(_suite="routing", _corpus_sha="", _scope="unknown")
    assert gate.matches(routing_run, req)
    assert gate.judge_one(routing_run, req, None)[0] == gate.PASS
