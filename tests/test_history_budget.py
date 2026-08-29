"""上下文预算装配器 + 滚动摘要（W2.1）。

这一节要守的东西，按「错了会怎样」排：

    对照组不许动      短会话上开关开关必须逐字节一样。上下文装配翻车的典型
                      方式不是"跨窗口没修好"，是**"窗口内的反而变差了"**
    摘要要被量得到    摘要必须落在 `messages[1:-1]`，否则免费那一档
                      （`eval/longchat.py --check`）会永远报告"没有效果"
    两条路共用一个窗口 直路和 Agent 各切各的，就会在某条长会话上悄悄分叉
    闸门要跟着改语义   摘要非空时 Agent 不许再短路回「我无法确认」

⚠️ 全部是纯函数测试，一个都不连库、不调模型——W2.1 的摘要**不调模型**，
这件事本身就是它最重要的设计决定（见 `qa.history_digest`）。
"""

from __future__ import annotations

import pytest

from copilot.config import get_settings
from copilot.qa import (
    HISTORY_TURNS,
    assemble_messages,
    history_digest,
    split_history,
    system_prompt_for,
)


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """`get_settings` 是 lru_cache 的，改环境变量前后都得清一次。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def budget_on(monkeypatch):
    monkeypatch.setenv("HISTORY_BUDGET_ENABLED", "true")
    get_settings.cache_clear()
    return get_settings()


def _history(turns: int, first: str = "我们用的是旺店通旗舰版", filler_chars: int = 40):
    """`turns` 轮问答。第 1 轮是那句要跨窗口找回来的话。"""
    out: list[tuple[str, str]] = []
    for i in range(1, turns + 1):
        out.append(("user", first if i == 1 else f"第{i}轮：" + "填充" * (filler_chars // 2)))
        out.append(("assistant", "好的，已经为你查到相关内容。" * 4))
    return out


def _messages(history, question="我一开始说的是哪个版本？"):
    return assemble_messages(system_prompt_for("fast"), history, "参考材料", question)


def _carried(messages) -> str:
    """这一轮**随会话变**的那部分。和 `eval/longchat.py::assemble` 同一个口径：
    第 0 条是固定的 system 模板，最后一条是用户这一问，两头都不算"记住了"。"""
    return "\n".join(m["content"] for m in messages[1:-1])


# ═══════════════ 一、对照组：开关关着，一个字节都不许动 ═══════════════


def test_switch_off_keeps_the_old_fixed_window():
    """⭐ 关着时就是 W2.1 之前那六条，不多不少，也没有摘要段。"""
    history = _history(10)
    kept, dropped = split_history(history)
    assert kept == history[-HISTORY_TURNS:]
    assert dropped == [], "开关关着时不该有「被挤出去的轮次」这个概念"
    assert history_digest(dropped) == ""


def test_switch_off_produces_no_digest_message(monkeypatch):
    messages = _messages(_history(10))
    assert sum(1 for m in messages if m["role"] == "system") == 1
    assert "更早对话的摘要" not in "\n".join(m["content"] for m in messages)
    # 老行为：system + 6 条历史 + 本轮提问
    assert len(messages) == 1 + HISTORY_TURNS + 1


def test_a_short_session_is_identical_either_way(monkeypatch):
    """⭐⭐ **对照组里最要紧的一道。**

    两轮的短会话在任何预算下都装得进去，所以开关开关必须给出**完全一样**的
    消息列表。这道题红了就说明预算装配器在正常会话上也改了行为——
    而那种改动是纯亏：跨窗口一分没赚，窗口内先赔了。
    """
    history = [("user", "退货入库的操作流程是什么？"), ("assistant", "第一步…")]
    before = _messages(history, "那不良品呢？")

    monkeypatch.setenv("HISTORY_BUDGET_ENABLED", "true")
    get_settings.cache_clear()
    after = _messages(history, "那不良品呢？")

    assert before == after


# ═══════════════ 二、开着时：挤出去的不再消失 ═══════════════


def test_the_earliest_turn_survives_as_a_digest(budget_on):
    """第 1 轮那句话原文已经出窗口，但它必须还在这一轮带得动的东西里。"""
    messages = _messages(_history(14))
    carried = _carried(messages)
    assert "旗舰版" in carried, "跨窗口的那条事实整个丢了"
    assert "更早对话的摘要" in carried


def test_the_digest_lands_where_the_eval_can_see_it(budget_on):
    """⚠️⚠️ **摘要不许拼进 system prompt。**

    `eval/longchat.py` 的 `carried` 刻意排除 `messages[0]`——固定的 system
    模板出现什么词都不算"记住了"（第一版就是这么把两道题的基线判成假命中的）。
    摘要是**随会话变的**，拼进 system 的话免费那一档会永远报告"没有效果"，
    而那是一个看起来完成了、其实什么都没量到的功能。
    """
    messages = _messages(_history(14))
    assert sum(1 for m in messages if m["role"] == "system") == 1, "只许有一条 system"
    assert "更早对话的摘要" not in messages[0]["content"]
    assert "更早对话的摘要" in messages[1]["content"]
    assert messages[1]["role"] == "user"


def test_recent_turns_stay_verbatim(budget_on):
    """摘要只压更早的。最近几轮必须还是原文——「接着聊」全靠它们。"""
    history = _history(14)
    kept, dropped = split_history(history)
    assert kept, "一条都没留"
    assert kept == history[len(dropped) :]
    assert kept[-1] == history[-1], "最近那一条必须在"


def test_the_budget_actually_binds(budget_on):
    """预算要真的卡住条数，否则"预算装配器"只是改了个名字。"""
    kept, dropped = split_history(_history(30))
    assert dropped, "30 轮一条都没挤出去，预算没起作用"
    used = sum(len(c) for _, c in kept)
    assert used <= budget_on.history_char_budget + budget_on.history_char_limit


def test_even_a_tiny_budget_keeps_the_last_turn(monkeypatch):
    """⚠️ 预算再小也要留住最近那一条。

    一条都不留的话，「那不良品呢」这种追问会变成一个孤零零的短句，
    检索改写也没有历史可用——那不是"上下文紧张"，那是把会话砍断了。
    """
    monkeypatch.setenv("HISTORY_BUDGET_ENABLED", "true")
    monkeypatch.setenv("HISTORY_CHAR_BUDGET", "1")
    get_settings.cache_clear()
    kept, dropped = split_history(_history(10))
    assert len(kept) == 1
    assert kept[0] == _history(10)[-1]
    assert dropped == _history(10)[:-1]


# ═══════════════ 三、摘要里放什么、不放什么 ═══════════════


def test_the_digest_only_carries_what_the_user_said():
    """⚠️⚠️ **助手那半边整个丢掉，这是个安全决定不是省字数。**

    把助手的旧答案压进摘要，等于把一段没人核对过的生成内容升格成
    "更早对话确认过的事"。ADR-19 否掉"让模型抽取事实"正是因为
    抽错的一条会被钉在上下文里、之后每轮重复同一个错误——
    助手的旧答案是同一个陷阱换了个入口。

    而用户说过的话是**这个系统里没有第二个地方存着**的东西。
    """
    dropped = [
        ("user", "我们有 4 个发货仓"),
        ("assistant", "根据知识库，仓库上限是 999 个。"),
    ]
    digest = history_digest(dropped)
    assert "4 个发货仓" in digest
    assert "999" not in digest, "助手的旧答案混进摘要了"


def test_the_digest_has_its_own_budget_and_says_what_it_dropped():
    """⚠️ 摘要不设上限的话，一条 50 轮的会话会拿两千字的摘要去挤本轮的
    检索材料——**那正是这次要修的病，换个位置犯一遍**。

    装不下时留最早的、丢最新的（最近的本来就还在窗口里），
    而且要**明说省了几轮**，否则摘要看起来像一份完整的会话记录。
    """
    dropped = [("user", f"第{i}轮：" + "很长的一句话" * 6) for i in range(1, 40)]
    digest = history_digest(dropped)
    assert len(digest) < 1200
    assert "第1轮" in digest, "最早的那条正是摘要唯一不可替代的价值"
    assert "未能全部保留" in digest, "省掉了几轮却没说"


def test_the_digest_warns_it_is_not_product_spec():
    """⚠️⚠️ **最贵的错法**：把摘要里的「我们有 4 个发货仓」读成
    "旺店通的仓库数量上限是 4"。前者是用户的情况，后者是产品的规格——
    混起来就绕过了「参数取值只能来自材料」那条红线，而且答案会带一个
    具体数字，看上去比"暂无此内容"可信得多。

    这条提示是 `lc-fact-not-a-product-answer` 那道题的 prompt 侧对手。
    """
    digest = history_digest([("user", "我们有 4 个发货仓")])
    assert "不是产品的规格" in digest
    assert "参考材料" in digest


def test_an_empty_or_assistant_only_drop_makes_no_digest():
    assert history_digest([]) == ""
    assert history_digest([("assistant", "好的")]) == ""


# ═══════════════ 四、两条路共用同一个窗口 ═══════════════


def test_the_agent_path_uses_the_same_window(budget_on):
    """⭐ M10 的「双路税」：窗口规则只许有一份。

    Agent 那条路原来拿到什么就转什么，窗口实际上是由路由层的 SQL LIMIT 定的。
    开着预算装配器时路由层会多取十几条，这里不跟着切的话，
    Agent 会拿到一段**没有上限**的历史。
    """
    from copilot.agent.runner import to_message_history

    history = _history(20)
    kept, dropped = split_history(history)
    msgs = to_message_history(history)
    # 摘要一条 + 窗口内每条一条
    assert len(msgs) == 1 + len(kept)
    assert "更早对话的摘要" in str(msgs[0])
    assert dropped, "这条会话本来就该挤出去一些"


def test_the_agent_path_is_unchanged_when_the_switch_is_off():
    from copilot.agent.runner import to_message_history

    history = _history(20)
    msgs = to_message_history(history)
    assert len(msgs) == HISTORY_TURNS
    assert "更早对话的摘要" not in str(msgs[0])


# ═══════════════ 五、那道边界闸门要跟着改语义 ═══════════════


def _deps(history, truncated=True):
    from copilot.agent.deps import AgentDeps

    return AgentDeps(
        session=None,
        user_id=None,
        conversation_id=None,
        embedder=None,
        history=history,
        history_truncated=truncated,
    )


def test_the_boundary_gate_still_fires_without_a_digest():
    """开关关着时行为一个字都不变：窗口裁了 + 事实表答不出 = 说不知道。"""
    from copilot.agent.runner import _beyond_window

    assert _beyond_window(_deps(_history(20)), "我第一个问题问的是什么？") is True


def test_the_boundary_gate_stands_down_once_there_is_a_digest(budget_on):
    """⭐⭐ 摘要里白纸黑字写着第一轮问的是什么，此时再短路回一句
    「我无法确认你最开始问的是什么」，比 W2.2 之前更糟——
    **这一次系统手里真的有答案，只是自己把嘴堵上了。**

    ⚠️ 判据是「摘要非空」而不是「摘要里有没有这个问题的答案」：
    这道闸门的作用是**放行**（让模型自己去读摘要），不是替它回答。
    放行之后模型看着摘要仍然可以说"这里面没有"——那是读完材料的结论，
    和一句读都没读的短路完全是两回事。
    """
    from copilot.agent.runner import _beyond_window

    assert _beyond_window(_deps(_history(20)), "我第一个问题问的是什么？") is False


def test_the_gate_is_untouched_when_nothing_was_truncated(budget_on):
    from copilot.agent.runner import _beyond_window

    assert _beyond_window(_deps(_history(2), truncated=False), "那个功能在哪") is False
