"""窗口外指代的边界闸门，**两条路共用一份判据**（ISSUES.md I-9）。

⭐ **这道闸门原来只长在 Agent 那条路上**，`qa.ask_stream` 里一行都没有。
同一句「那个功能在哪配置来着？」，指代对象已经出窗口时：

    Agent 路   「当前上下文只保留最近几轮，我无法确认"那个功能"指什么」
    直路       随机挑最近一个话题当成"那个功能"，答得斩钉截铁

`lc-vague-reference-out-of-window` 在三轮付费评测的**每一臂**都失分，
包括基线——根因就是这里，不是 W2.1 引入的。

⚠️ 这份测试守的是**判据本身**，不是"直路开着这道闸门"：
`DIRECT_BOUNDARY_ENABLED` 默认关，要 A/B 之后才谈开不开。
"""

from __future__ import annotations

import pytest

from copilot import qa

VAGUE = "那个功能在哪配置来着？"
EARLIEST = "我最开始问的是什么问题"


def test_short_circuits_only_when_nothing_can_answer():
    reply = qa.boundary_reply(VAGUE, history_truncated=True, digest="", facts_answerable=False)
    assert reply is not None
    assert "那个功能" in reply
    assert "请直接说出功能名称" in reply


def test_the_earliest_question_gets_its_own_wording():
    reply = qa.boundary_reply(EARLIEST, history_truncated=True, digest="", facts_answerable=False)
    assert reply is not None
    assert "最开始问的是什么" in reply
    # ⚠️ 两条话术不能串：问"最开始问的是什么"却回"请直接说出功能名称"，
    # 用户会以为系统没听懂
    assert "功能名称" not in reply


@pytest.mark.parametrize(
    ("truncated", "digest", "facts", "why"),
    [
        (False, "", False, "窗口根本没裁过东西"),
        (True, "第一轮问的是电子面单", False, "W2.1 的摘要里还留着"),
        (True, "", True, "W2.2 的事实表答得出"),
    ],
)
def test_any_one_condition_failing_lets_it_through(truncated, digest, facts, why):
    """⚠️⚠️ **放行是安全的那一侧。**

    放行之后模型看着材料仍然可以说"这里面没有"——那是读完之后的结论；
    而短路是一句读都没读的拒答。不该短路却短路了，用户会看到「我无法确认」，
    而系统其实手里有答案，那比忘了更糟。
    """
    assert (
        qa.boundary_reply(
            VAGUE, history_truncated=truncated, digest=digest, facts_answerable=facts
        )
        is None
    ), why


def test_a_normal_question_is_never_short_circuited():
    """普通问题即使在窗口全裁的会话里也照常走检索。"""
    assert (
        qa.boundary_reply(
            "库存台账在哪看", history_truncated=True, digest="", facts_answerable=False
        )
        is None
    )


def test_the_agent_path_reads_the_same_regexes():
    """⚠️ Agent 和直路必须**共用**这两条正则，不能各存一份。

    各存一份的失败形态是无症状的：改了一处、另一处没改，于是同一句话在
    两条路上行为不同，而两条路的用户互不相见——线上就是这么裂了很久的。
    """
    from copilot.agent import runner

    assert runner._TRUNCATED_REFERENCE_RE is qa._TRUNCATED_REFERENCE_RE
    assert runner._EARLIEST_HISTORY_RE is qa._EARLIEST_HISTORY_RE


def test_the_direct_gate_is_off_by_default():
    """⚠️ 它改的是每一轮的行为，这个项目的规矩是这类改动先做成开关。

    打开之前要跑 `eval/longchat.py` 两边各一次：`cross_window_ref` 要涨、
    `in_window_control` 一分都不许掉。
    """
    from copilot.config import Settings

    assert Settings().direct_boundary_enabled is False


# ─────────── 直路必须收窄（2026-09-02，免费探针量出来的） ───────────


def test_the_direct_path_must_not_short_circuit_the_earliest_question():
    """⚠️⚠️ **直路照搬 Agent 的判据会当场判错两道题。**

    「我一开始说的是哪个版本」这一支在 Agent 路上安全，因为那条路有逃生口：
    `SessionFacts.answers()` 判得出「事实表里有版本这一项」，于是放行。
    直路**没有那个逃生口**——它手里只有渲染好的 `facts` 文本，判不了
    "答不答得出"，而 `SESSION_FACTS_ENABLED` 还是关着的。

    照搬的代价是 `eval/longchat.py` 上量到的：

        lc-version-asked-late               must_not_include 含「无法确认」→ 判错
        lc-earliest-question-out-of-window  must_not_include 含「无法确认」→ 判错
        lc-vague-reference-out-of-window    修好

    **净 -1。** 所以直路只留「那个功能」那一支——指代对象从来不在事实表里，
    短路就是正确答案。
    """
    kwargs = {"history_truncated": True, "digest": "", "facts_answerable": False}

    # Agent 口径：两支都在
    assert qa.boundary_reply("我一开始说的是哪个版本？", **kwargs) is not None
    # 直路口径：这一支必须放行
    assert qa.boundary_reply("我一开始说的是哪个版本？", include_earliest=False, **kwargs) is None
    assert qa.boundary_reply("我第一个问题问的是什么？", include_earliest=False, **kwargs) is None

    # 而指代那一支两边都要短路
    for kw in ({}, {"include_earliest": False}):
        reply = qa.boundary_reply(VAGUE, **kwargs, **kw)
        assert reply is not None and "说出功能名称" in reply
