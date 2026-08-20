"""常识兜底（M12）。

⚠️ **这一组守的是一条被挪过位置的红线，不是一条被拆掉的红线。**

M1–M11 的规矩是「不许用自己的知识」。M12 改成：

    可以用自己的知识答   行业术语、概念解释、通用做法    错了是理解偏差
    绝不能凭记忆写       界面路径、菜单层级、字段名、
                        参数取值、数量上限              错了客户的订单卡住

所以这里最重要的用例不是「常识题能答了」，而是**放开之后那条硬防线还在**：
一段没查知识库却写着「进入【设置】-【打印设置】，勾选…」的话，
仍然要被拦下来。这两件事必须同时成立，缺一个这次改动就是纯亏。
"""

from __future__ import annotations

import pytest

from copilot.agent.guard import looks_like_kb_answer
from copilot.config import get_settings
from copilot.qa import NO_ANSWER, system_prompt_for


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """`get_settings` 是 lru_cache 的，改环境变量前后都得清一次。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------- prompt 的两个版本 ----------


def test_strict_version_forbids_own_knowledge():
    p = system_prompt_for("fast", general=False)
    assert "不得用你自己的常识补全或推测" in p
    assert "只依据下面提供的「参考材料」回答问题" in p


def test_open_version_allows_concepts_but_not_paths():
    """⭐ 放开版必须**同时**说清两件事，只说一件就是危险的。"""
    p = system_prompt_for("fast", general=True)
    # 放开的那一半
    assert "可以答" in p
    assert "行业术语" in p
    # ⚠️ 守住的那一半 —— 少了它，模型会开始"想起来"旺店通的界面路径
    assert "不要凭记忆写旺店通的界面路径" in p
    assert "参数取值、数量上限" in p


def test_open_version_still_keeps_no_answer_for_config_questions():
    """放开常识 ≠ 什么都答。具体配置查不到，仍然只回那一句。"""
    p = system_prompt_for("fast", general=True)
    assert NO_ANSWER.rstrip("。") in p
    assert "凭记忆编一个出来是这里唯一不可接受的答法" in p


def test_open_version_keeps_the_sentences_that_actually_hold_the_line():
    """⚠️⚠️ **这几句是用一次 50% 的幻觉率换来的，别删。**

    2026-08-21 为了救配图带出率，把放开版的铁律 1 从 7 行压到 3 行、
    铁律 3 的三分支压成两句。配图回来了 2 道，代价是 16 道 no_answer 题错了 8 道，
    而错的样子是这个：

        问：Lazada 店铺的订单怎么同步到 ERP？（知识库里没有 Lazada 的任何文档）
        答：进入【设置】-【基本设置】-【店铺】，点击"添加"，
            店铺平台选择 "Lazada"，填写必要信息后保存。[5]

    它把材料里通用的建店流程照抄下来、把平台名换成 Lazada、**还挂上了 [5]**。
    用户照着点进去下拉框里根本没有那一项，而那句话长着有出处的样子。

    压缩版丢掉的关键是最后那句——整段里**唯一一句把「不知道」和「编一个」
    明确对立起来**的话。所以这道题盯着它，不盯别的。
    """
    p = system_prompt_for("fast", general=True)
    assert "凭记忆编一个出来是这里唯一不可接受的答法" in p
    # 三个分支要分开写；合并成两句就是压缩版那个下场
    assert p.count("·") >= 3, "铁律 3 的三个分支被合并了"


def test_open_version_tells_the_model_not_to_cite_its_own_knowledge():
    """⭐ 常识那一段**不许标 [n]**。

    标了的话，页面上会出现一条可点的来源，点开却和这句话无关——
    比不标更糟：它把「没有出处」伪装成「有出处」。
    """
    p = system_prompt_for("fast", general=True)
    assert "不要给它标来源编号" in p


def test_only_rule1_and_rule3_differ_between_versions():
    """⚠️ 两版之间**只准差这两处**。

    别的铁律（引用编号、照原文答数字、带图号、历史不是材料、私有优先）
    在两个版本里必须一字不差 —— 一旦放开版顺手把别的规则也松了，
    就再也说不清指标的变化是哪一条带来的。
    """
    strict = system_prompt_for("fast", general=False)
    open_ = system_prompt_for("fast", general=True)
    for keep in (
        "每一句结论后面标注来源编号",
        "照原文答",
        "只能用材料里真实出现过的图号",
        "前面的对话记录**只用来理解这一轮在问什么**",
        "以「你的文档」为准",
        "绝不能因为答不全就整个不答",
    ):
        assert keep in strict, keep
        assert keep in open_, keep


def test_switch_defaults_to_env(monkeypatch):
    monkeypatch.setenv("ALLOW_GENERAL_KNOWLEDGE", "false")
    get_settings.cache_clear()
    assert "不得用你自己的常识补全或推测" in system_prompt_for("fast")

    monkeypatch.setenv("ALLOW_GENERAL_KNOWLEDGE", "true")
    get_settings.cache_clear()
    assert "不要凭记忆写旺店通的界面路径" in system_prompt_for("fast")


# ---------- 硬防线：放开之后仍然拦操作步骤 ----------

# ⚠️ 这一段刻意含「字段」和「界面」两个词。它们原来在 `_ERP_MARKS` 里，
# M12 从 `_OPERATIONAL_MARKS` 里拿掉了——**因为概念解释里出现它们太自然了**
# （「在系统里对应供销商这个字段」），拿它们当越线特征会误伤一大片。
# 所以这一段正好卡在两个版本的分界上：严格版拦、放开版放行。
CONCEPT = (
    "品牌方就是货品的源头——拥有品牌和货权、把货铺给分销商去卖的那一方。"
    "在分销链路里，品牌方负责定价规则，分销商从品牌方铺货、推单，"
    "订单最终回到品牌方发货。它在系统里对应「供销商」这个字段，"
    "在分销相关的界面里也是这个叫法。"
)
OPERATION = (
    "设置电子面单模板的步骤：1. 进入【设置】-【打印设置】-【集成打印】；"
    "2. 点击新建模板，选择对应的物流公司；3. 勾选「自动获取单号」后保存。"
)


def test_concept_answer_passes_when_general_is_on():
    """⭐ 这是 2026-08-20 线上被误伤的那一类。

    当时用户追问「品牌方又是什么」，模型答了一段正确的行业概念解释，
    被硬防线整段换成「知识库暂无此内容」。事后查证：知识库里确实没有这个概念的
    定义（实测最高分 0.35 那条讲的是一盘货库存），上一轮召回的 5 块材料
    也一个「品牌方」都没有 —— **怎么修检索都救不回来**。

    ⚠️ 这里用的是**重建**的一段，不是线上原文（journal 只截了前 120 字）。
    重建时刻意保留了那一类的关键特征：专业、长、含系统里的名词，
    但**没有一步能点下去的操作**。
    """
    assert looks_like_kb_answer(CONCEPT) is True, "严格模式下它确实会被当成知识库答案"
    assert looks_like_kb_answer(CONCEPT, operational_only=True) is False


def test_operation_answer_is_still_blocked_when_general_is_on():
    """⚠️ **这一条是这次改动的安全底线。**

    放开常识之后，模型最危险的行为不是解释概念，是「想起来」一段界面路径。
    它和真的长得一模一样，而用户会照着去点。
    """
    assert looks_like_kb_answer(OPERATION) is True
    assert looks_like_kb_answer(OPERATION, operational_only=True) is True


@pytest.mark.parametrize("mark", ["[3]", "[图2]"])
def test_citation_marks_are_blocked_in_both_modes(mark):
    """`[n]` / `[图n]` **任何模式下都拦**。

    一轮里没调工具却写出引用编号，那些编号指向的是上一轮的来源——
    页面上 0 条引用可点，而正文里挂着 [3]。编号错位是无条件的错，
    和「放不放开常识」没有关系。
    """
    text = f"品牌方就是货品的源头，拥有品牌和货权，把货铺给分销商去卖 {mark}。" * 2
    assert looks_like_kb_answer(text) is True
    assert looks_like_kb_answer(text, operational_only=True) is True


def test_short_replies_are_never_blocked():
    """追问、闲聊都很短，两种模式下都不该被当成越线。"""
    for text in ("要对接哪些平台？", "好的，我记下了。", "仓库是一个还是多个？"):
        assert looks_like_kb_answer(text) is False
        assert looks_like_kb_answer(text, operational_only=True) is False
