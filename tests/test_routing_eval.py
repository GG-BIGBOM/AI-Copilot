"""路由评测这台仪器自己的测试（2026-08-23）。

⭐ 理由同 `test_eval_scoring.py` 的文件头：**评测是用来做决定的仪器**。
这一份守的是 2026-08-23 查出来的两处失真——两处都把**正确行为**记成了违规，
而报表上看起来像模型越了线：

1. **划边界的拒答被记成「越过工具直答」。** 五道越界题（写代码、翻译、
   医疗、算数、薪资）上，Agent 说的是「我只负责旺店通 ERP……不写代码」，
   一个工具都没调、也没替用户把那件事做了——这正是 instructions 要求的。
   旧判据只认「走 answer_kb 再说没有」，于是越界那组恒定 37.5%。
2. **寒暄短路没有让开 Agent 流程。** 生产代码里是
   `if not plan_flow and small_talk_reply(...)`（`chat.py`），而评测无条件
   先查寒暄表，于是「明白了」被短路成寒暄，报出一个线上根本不存在的 bug。

把正确行为记成违规的指标，会逼着人去修一个没坏的东西——这比没有指标更贵。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def _load_routing():
    """按文件路径载入 `eval/routing.py`，理由同 `test_eval_scoring._load_run`。"""
    if "eval_routing" in sys.modules:
        return sys.modules["eval_routing"]
    spec = importlib.util.spec_from_file_location("eval_routing", EVAL_DIR / "routing.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_routing"] = mod
    spec.loader.exec_module(mod)
    return mod


routing = _load_routing()


# ---------- 1：划边界的拒答不是越线 ----------


@pytest.mark.parametrize(
    "answer",
    [
        # 2026-08-23 实测原句，一字未改
        "这个我帮不了你——我只负责旺店通旗舰版 ERP 的实施配置，不写代码。"
        "有 ERP 相关的问题随时问我。",
        "我不能帮你做翻译，这超出了我的职责范围。我只负责旺店通旗舰版 ERP 的实施配置相关问题。",
        "这个和旺店通 ERP 无关，我没法帮你算。有 ERP 相关的问题随时问我。",
    ],
)
def test_scope_refusal_is_recognised(answer):
    assert routing.looks_like_refusal(answer) is True


def test_refusal_with_an_erp_path_is_still_a_bypass():
    """⭐ 末尾补一句「其余的我不负责」不能把前面那段编出来的路径洗白。"""
    answer = "在【设置】-【打印设置】里点击新建模板即可。其余的不在我的范围内。"
    assert routing.looks_like_refusal(answer) is False
    assert routing.bypassed_tool(answer, used_kb=False) is True


def test_a_real_answer_is_not_a_refusal():
    answer = "一百的二次方根是 10。"
    assert routing.looks_like_refusal(answer) is False


def test_bypass_needs_no_tool():
    """走了 `answer_kb` 的答案，无论长什么样都不算越过工具。"""
    answer = "在【设置】-【打印设置】里点击新建模板即可。"
    assert routing.bypassed_tool(answer, used_kb=True) is False


# ---------- 2：一道题可以有两个都对的落点 ----------


def test_accept_list_counts_as_correct():
    r = routing.CaseResult(id="off-code", kind="越界", q="帮我写一段代码", expected="kb")
    r.accept = ["refuse"]
    r.actual = "refuse"
    assert r.ok is True


def test_accept_list_does_not_forgive_a_wrong_route():
    r = routing.CaseResult(id="off-code", kind="越界", q="帮我写一段代码", expected="kb")
    r.accept = ["refuse"]
    r.actual = "direct"  # 它自己把代码写了
    assert r.ok is False


# ---------- 3：寒暄短路必须让开 Agent 流程 ----------


def test_history_with_a_plan_request_counts_as_agent_flow():
    """判据直接来自生产的 `AGENT_TRIGGERS`，不在评测里另抄一张表。"""
    history = [
        ["user", "帮我出一份配置清单"],
        ["assistant", "明白了。仓库是一个还是多个？"],
    ]
    assert routing._history_started_agent(history) is True
    assert routing._history_started_agent([["user", "退货入库怎么操作"]]) is False


def test_plain_thanks_outside_a_flow_is_still_smalltalk():
    """让开只针对流程里那一句，寒暄表本身不能被架空。"""
    from copilot.qa import small_talk_kind

    assert small_talk_kind("明白了") is not None
