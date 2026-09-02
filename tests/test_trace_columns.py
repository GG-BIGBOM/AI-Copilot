"""M19-B 给 `request_trace` 补的四列，以及它们和 `answer_source` 的关系。

⭐ **这四列是补充维度，不是新的 source 值。** plan.md 里那条约束写得很硬：
「KB + 常识兜底时仍是 `kb` + `general_knowledge_used=true`，**不新增 source 值**，
否则历史统计的分母会变」——给 source 加一个新值，半年的历史统计会集体换分母，
而那是个没人会发现的错。
"""

from __future__ import annotations

import uuid

import pytest

from copilot.api.trace import GENERAL, KB, NO_ANSWER, VERIFIED, TraceDraft


def _draft(**kw) -> TraceDraft:
    base = {"route": "direct", "question": "快递拦截怎么配", "user_id": None}
    return TraceDraft(**{**base, **kw})


# ═══════════════ 一、general_knowledge_used ═══════════════


def test_allowed_and_uncited_is_the_only_true_case():
    """允许了常识 + 答案一个 `[n]` 都没标 = 常识这条路真的产出了这次答案。"""
    d = _draft(answer="一般来说，电子面单要先在物流平台开通。", general_allowed=True)
    s = d.summary()
    assert s["answer_source"] == GENERAL
    assert s["general_knowledge_used"] is True


def test_uncited_but_not_allowed_is_a_missing_citation_not_general_knowledge():
    """⚠️⚠️ **这一行是这一列存在的全部理由。**

    没允许常识、答案却一个来源编号都没标——那不是"用了常识"，是**模型漏标
    `[n]`**，属于引用正确率那条线的病。在此之前这两种情形在表里长得一模一样：
    `answer_source` 都是 `general_knowledge`，没有任何一列分得开。
    """
    d = _draft(answer="一般来说，电子面单要先在物流平台开通。", general_allowed=False)
    s = d.summary()
    assert s["answer_source"] == GENERAL, "source 口径不变"
    assert s["general_knowledge_used"] is False, "没允许过，就不能算常识答的"


def test_cited_answer_is_kb_even_when_general_is_allowed():
    """允许着常识，但这一句指着材料说话——source 是 kb，used 是 false。"""
    d = _draft(answer="在设置—物流里配置 [1]。", general_allowed=True)
    s = d.summary()
    assert s["answer_source"] == KB
    assert s["general_knowledge_used"] is False


def test_a_refusal_is_never_counted_as_general_knowledge():
    """拒答那一轮什么都没答，不该被算成"用了常识"。"""
    d = _draft(answer="知识库暂无此内容。", no_answer=True, general_allowed=True)
    s = d.summary()
    assert s["answer_source"] == NO_ANSWER
    assert s["general_knowledge_used"] is False


# ═══════════════ 二、verified / correction ═══════════════


def test_a_verified_hit_records_which_answer_and_which_correction():
    """⭐ 这两列合起来，「用户提的那条纠错后来真的救到人了吗」第一次可查。

    在此之前纠错一发布就断线了：`answer_corrections` 里有那一条、
    `verified_answers` 里有发布出来的那条，而**没有任何一列**能把它们和
    后续真实问答连起来。
    """
    aid, cid = uuid.uuid4(), uuid.uuid4()
    d = _draft(answer="订正后的原文。", verified=True, verified_answer_id=aid, correction_id=cid)
    s = d.summary()
    assert s["answer_source"] == VERIFIED
    assert s["verified_answer_id"] == aid
    assert s["correction_id"] == cid


def test_no_verified_hit_leaves_both_ids_none():
    d = _draft(answer="在设置—物流里配置 [1]。")
    s = d.summary()
    assert s["verified_answer_id"] is None
    assert s["correction_id"] is None


# ═══════════════ 三、image_count ═══════════════


@pytest.mark.parametrize("n", [0, 1, 5])
def test_image_count_is_recorded_as_given(n: int):
    """⚠️ 记的是**发给前端的**张数，不是检索到的。

    「配图串台」和「该配图没配」都只能从用户看到的那几张查起——
    检索到十张、发出去两张时，记十张会让这两类问题都查不出来。
    """
    assert _draft(answer="x", image_count=n).summary()["image_count"] == n


def test_image_count_defaults_to_none_not_zero():
    """⚠️ 老数据是 NULL，不是 0。

    0 的意思是"这一轮确实一张图都没发"，NULL 是"那时候还没有这一列"。
    填成 0 会让上线之前的行凭空多出一批"确定没配图"的观测。
    """
    assert _draft(answer="x").summary()["image_count"] is None


# ═══════════════ 四、和落库那一份必须是同一个数 ═══════════════


def test_the_saved_row_reuses_summary_and_never_recomputes():
    """⚠️ `save()` 里的 `answer_source` 必须取自 `summary()`，不能重算一遍。

    这是 trace.py 文件头「每个数只有一个定义」那条规矩。看板上说
    `answer_source=kb`、表里那行写 `general_knowledge`，两个数对不上时，
    **没有任何办法判断哪个是对的**。
    """
    import inspect

    from copilot.api import trace

    src = inspect.getsource(trace.TraceDraft.save)
    assert 'snap["answer_source"]' in src, "save 应该从 summary 取"
    assert "classify_answer_source(" not in src, "save 里不许再算一遍 answer_source"
