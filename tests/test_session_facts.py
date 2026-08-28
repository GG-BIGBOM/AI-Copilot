"""会话级已确认事实（W2.2）。

⚠️ **这一组守的是「记得住」，但守法是「别乱记」。**

一张会话事实表最容易失败的方式不是漏记一项——漏了只是回到 W2.2 之前的行为
（末 6 轮硬窗口，问一开始说的就说不知道）。真正的失败是**记错一项**：
它会被钉在 system prompt 里，之后每一轮都在重复同一个错误，
而它长着「系统确认过」的样子，用户分辨不出。

所以下面的用例分成三块，重要性从上往下递减：

1. **不注入的那几种情形**（开关关着、表是空的、脏数据）——行为必须和 W2.2 之前一样
2. **注入的内容说了什么**——轮次、改口历史、以及"这不是参考材料"那三句
3. **表里有什么**——记录本身
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import ask, parts
from sqlalchemy import select

from copilot.agent.checklist import REQUIREMENT_FIELDS, Requirement
from copilot.config import get_settings
from copilot.db.models import Conversation
from copilot.qa import named_subject, system_prompt_for
from copilot.session_facts import FACT_LABELS, SessionFacts


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """`get_settings` 是 lru_cache 的，改环境变量前后都得清一次。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ═══════════════ 一、字段清单只许有一份 ═══════════════


def test_every_requirement_field_is_a_fact_field():
    """⭐ `FACT_LABELS` 必须**派生**自 `REQUIREMENT_FIELDS`，不能抄一份。

    抄一份的话，`checklist.py` 那边加一个字段而这边忘了加，表现是那一项
    永远不进事实表——不报错，没有症状，而 `note_requirements` 会照常返回
    "没改动"。这条断言是那件事唯一能被发现的地方。
    """
    for f in REQUIREMENT_FIELDS:
        assert f in FACT_LABELS, f"需求字段 {f} 没进事实表"


def test_fact_labels_match_requirement_labels():
    """标签也一样：两边各写一份中文名，用户会看到同一个字段的两种叫法。"""
    for f, (label, _) in REQUIREMENT_FIELDS.items():
        assert FACT_LABELS[f] == label


# ═══════════════ 二、不注入的那几种情形 ═══════════════


def test_empty_table_renders_nothing():
    """空表渲染成空串。调用方据此整段不注入——prompt 逐字节回到 W2.2 之前。"""
    assert SessionFacts().human() == ""


def test_prompt_without_facts_is_unchanged():
    """⭐ `facts=""` 时的 prompt 必须和不传这个参数**一模一样**。

    这条守的是开关关着时的行为。差一个换行都算不一样：
    prompt 的每一次改动这个项目都要拿评测量一遍，
    而"开关关着却悄悄改了 prompt"意味着那些历史数字全部作废。
    """
    assert system_prompt_for("fast", facts="") == system_prompt_for("fast")


def test_prompt_with_facts_appends_at_the_end():
    """事实表接在最后。前面那几段都在收紧「什么不能答」，
    放在收紧之前的话，模型很容易把它读成材料的一部分然后给它标 [n]。"""
    base = system_prompt_for("fast")
    facts = SessionFacts()
    facts.note("knowledge_space", "旗舰版", 1)
    p = system_prompt_for("fast", facts=facts.human())
    assert p.startswith(base)
    assert "旗舰版" in p[len(base) :]


def test_load_tolerates_garbage():
    """脏数据一律跳过，不抛。

    ⚠️ 这一列是 W2.2 才加的，早于它的会话读出来是 None；而一条读坏的事实
    不该让整轮提问变成 500——最坏后果只该是这一轮少注入一条。
    """
    dirty = {
        "knowledge_space": {"value": "旗舰版", "turn": 1},
        "platforms": {"value": None},  # 值不是字符串
        "shop_count": "3",  # 整条不是 dict
        "不认识的字段": {"value": "x", "turn": 1},
        "logistics": {"value": "", "turn": 2},  # 空串
    }
    facts = SessionFacts.load(dirty)
    assert set(facts.facts) == {"knowledge_space"}
    assert SessionFacts.load(None).facts == {}
    assert SessionFacts.load({}).facts == {}


# ═══════════════ 三、注入的内容说了什么 ═══════════════


def test_injected_block_says_it_is_not_reference_material():
    """⭐ 三句挡三种错法，一句都不能少。

    最贵的是第三句：把「仓库数量：4」读成"旺店通有个字段该填 4"。
    事实表说的是**用户的情况**，不是**产品的配置**——两件事混起来，
    等于给了模型一条绕过"界面路径只能来自材料"的新路。
    """
    facts = SessionFacts()
    facts.note("warehouse_count", "4", 3)
    block = facts.human()
    assert "不要给它们标来源编号" in block  # 挡「当材料引用」
    assert "不要去翻对话记录猜" in block  # 挡「表里没有就去猜」
    assert "不是旺店通的配置依据" in block  # 挡最贵的那一种
    assert "参数取值仍然只能来自参考材料" in block


def test_turn_number_is_rendered():
    """⭐ 轮次必须写出来。「我一开始说的是几个仓」问的就是轮次——
    只给一个当前值，模型要么答改口后的那个，要么去翻已被裁掉的记录猜。"""
    facts = SessionFacts()
    facts.note("warehouse_count", "2", 3)
    assert "第 3 轮说的" in facts.human()


def test_revision_keeps_the_first_statement_visible():
    """改口之后，**最早那次说了什么仍然要在**。"""
    facts = SessionFacts()
    facts.note("warehouse_count", "2", 3)
    facts.note("warehouse_count", "4", 9)
    block = facts.human()
    assert "仓库数量：4" in block
    assert "第 9 轮改的" in block
    assert "第 3 轮说的是「2」" in block
    assert facts.first_value("warehouse_count") == "2"


def test_knowledge_space_is_not_described_as_something_the_user_said():
    """⚠️ 版本不是"说"出来的，是新建会话时钉死的（models.Conversation）。

    写成「第 1 轮说的」会让模型以为它和别的字段一样可以改口，
    然后在用户说「那按企业版算」时顺着改——而那条会话的检索范围根本不会跟着变。
    """
    facts = SessionFacts()
    facts.note("knowledge_space", "旗舰版", 1)
    block = facts.human()
    assert "本会话开始时选定，中途不可更改" in block
    assert "轮说的" not in block


# ═══════════════ 四、记录本身 ═══════════════


def test_same_value_repeated_is_not_a_revision():
    """同一个值重复说不算改口。

    用户每一轮都提「星辰电商」是常态；每次都往 `was` 里塞一条的话，
    第 15 轮那段注入会变成一屏改口记录，把真正有用的几行挤没。
    """
    facts = SessionFacts()
    assert facts.note("subject", "星辰电商", 2) is True
    assert facts.note("subject", "星辰电商", 5) is False
    assert facts.facts["subject"]["was"] == []
    assert facts.facts["subject"]["turn"] == 2


def test_blank_value_is_ignored():
    facts = SessionFacts()
    assert facts.note("platforms", None, 1) is False
    assert facts.note("platforms", "   ", 1) is False
    assert facts.facts == {}


def test_unknown_field_is_a_programming_error():
    """不认识的字段直接抛。这不是用户输入能造成的——只可能是接线写错了，
    静默忽略的话那一项永远不进表，而没有任何症状。"""
    with pytest.raises(KeyError):
        SessionFacts().note("没有这个字段", "x", 1)


def test_revision_history_is_capped_but_keeps_the_first():
    """⭐ 超了从**中间**丢，第一条永远留着——「我一开始说的」问的就是它。"""
    facts = SessionFacts()
    for turn, value in enumerate(["1", "2", "3", "4", "5", "6"], start=1):
        facts.note("shop_count", value, turn)
    was = facts.facts["shop_count"]["was"]
    assert len(was) <= 3
    assert was[0]["value"] == "1"  # 最早那次
    assert was[0]["turn"] == 1
    assert facts.first_value("shop_count") == "1"
    assert facts.facts["shop_count"]["value"] == "6"


def test_requirements_are_mirrored_not_shared():
    """需求档案镜像进事实表。

    ⭐ 镜像而不是共用一份：`profile` 是「出方案还缺什么」的工作状态，
    事实表是「用户说过什么」的账本，要留改口历史、要跨窗口注入。
    合成一份的话，任何一边加个字段都会悄悄改变另一边的行为。
    """
    facts = SessionFacts()
    profile = Requirement(platforms="淘宝、抖音", warehouse_count="3")
    assert facts.note_requirements(profile, turn=4) is True
    assert facts.facts["platforms"]["value"] == "淘宝、抖音"
    assert facts.facts["warehouse_count"]["turn"] == 4
    # 没填的字段不进表——空项进表就等于告诉模型"这一项确认过是空的"
    assert "logistics" not in facts.facts
    # 再镜像一次同样的档案不算改动
    assert facts.note_requirements(profile, turn=5) is False


def test_roundtrip_through_jsonb():
    facts = SessionFacts()
    facts.note("knowledge_space", "旗舰版", 1)
    facts.note("warehouse_count", "2", 3)
    facts.note("warehouse_count", "4", 9)
    assert SessionFacts.load(facts.dump()).human() == facts.human()


# ═══════════════ 五、`answers()`：这句话问的是不是表里有的 ═══════════════


def test_answers_needs_both_the_alias_and_the_fact():
    """⚠️ 两个条件缺一不可，而且必须偏向漏判。

    漏判 = 退回今天的边界话术「我无法确认」，安全；
    误判 = 让一个本该说不知道的问题走进模型，而模型手里有一张看着很权威的表。
    """
    facts = SessionFacts()
    facts.note("knowledge_space", "旗舰版", 1)
    # 别名对上、表里有 → 认
    assert facts.answers("我一开始说的是哪个版本") == "knowledge_space"
    # 别名对上、表里没有 → 不认
    assert facts.answers("我们是几个仓来着") is None
    # 表里有、但问的不是它 → 不认
    assert facts.answers("退货入库怎么操作") is None


def test_asking_about_the_first_question_is_still_beyond_the_window():
    """⭐ 「我最开始问的是什么问题」问的是**提问历史**，事实表里没有这种东西。

    这道题必须继续走边界话术。认了它就等于让模型拿一张讲版本和仓库数的表，
    去回答"你最开始问的是什么"——它会编一个出来。
    """
    facts = SessionFacts()
    facts.note("knowledge_space", "旗舰版", 1)
    facts.note("warehouse_count", "4", 3)
    assert facts.answers("我最开始问的是什么问题") is None
    assert facts.answers("我第一个问题是什么") is None


@pytest.mark.parametrize(
    "question",
    [
        "怎么设置物流单量限制",
        "仓库权限在哪里配",
        "多少单以上要走批量打印",
        "店铺授权失效了怎么办",
    ],
)
def test_normal_product_questions_do_not_hit_the_alias_table(question):
    """⚠️ 别名表里的词不能出现在一句正常的 ERP 产品问题里。

    这几道都是该走检索的题。任何一道被认成"问事实表"，
    表现是它在窗口裁掉之后不再检索，而是从一张讲客户情况的表里找答案。
    加新别名之前先把这个列表跑一遍。
    """
    facts = SessionFacts()
    for f in FACT_LABELS:
        facts.note(f, "x", 1)  # 表里全都有，把条件放到最松
    assert facts.answers(question) is None


# ═══════════════ 六、主体名的抽取 ═══════════════


def test_named_subject_extracts_the_third_party():
    assert named_subject("星辰电商的退货入库要走哪几个审核节点") == "星辰电商"
    assert named_subject("远岸家居的对账以什么为准") == "远岸家居"


def test_named_subject_ignores_first_person():
    """「我们公司」说的是他自己，不是点名某一家。

    ⚠️ 这一条不能省：`_SUBJECT_SUFFIX_RE` 的后缀表里有「公司」，
    不排掉的话每个说「我们公司」的用户都会被记成一家叫「我们公司」的客户。
    """
    assert named_subject("我们公司的电子面单怎么配") is None
    assert named_subject("本公司的组合装要拆吗") is None
    assert named_subject("电子面单怎么设置") is None


# ═══════════════ 七、跑通整条路（要库）═══════════════


async def _facts_of(maker, conv_id: uuid.UUID) -> dict:
    async with maker() as s:
        return (
            await s.execute(select(Conversation.facts).where(Conversation.id == conv_id))
        ).scalar_one()


async def _ask_n_turns(api_client, conv_id: str, n: int, first: str) -> None:
    """灌 n 轮对话。第 1 轮用 `first`，之后都是无关的问题。

    ⚠️ 轮数要**超过 `HISTORY_TURNS`**，否则第 1 轮还在窗口里，
    这一组测的东西根本没被触发过。
    """
    await ask(api_client, first, conv_id)
    for i in range(2, n + 1):
        await ask(api_client, f"第 {i} 个无关问题：打印面单怎么弄", conv_id)


async def test_facts_are_recorded_even_when_the_switch_is_off(
    api_client, logged_in, fake_providers, public_chunk, maker, monkeypatch
):
    """⭐ 开关关着也照样**记录**，只是不注入。

    这个顺序是刻意的：真开的那天，存量会话手里已经有账本了，
    而不是从那一刻起才开始攒——否则线上灰度的头几天，
    所有老会话的表都是空的，量出来的数字只反映新会话。
    """
    monkeypatch.setenv("SESSION_FACTS_ENABLED", "false")
    get_settings.cache_clear()

    conv_id = str(uuid.uuid4())
    r = await ask(api_client, "星辰电商的退货入库怎么操作", conv_id)
    assert r.status_code == 200

    stored = await _facts_of(maker, uuid.UUID(conv_id))
    assert stored["subject"]["value"] == "星辰电商"
    assert stored["knowledge_space"]["value"]  # 版本从会话本身推出来的
    assert stored["subject"]["turn"] == 1

    # 但 prompt 里一个字都没有
    system = fake_providers.calls[-1][0]["content"]
    assert "已确认的事实" not in system


async def test_facts_are_injected_when_the_switch_is_on(
    api_client, logged_in, fake_providers, public_chunk, maker, monkeypatch
):
    monkeypatch.setenv("SESSION_FACTS_ENABLED", "true")
    get_settings.cache_clear()

    conv_id = str(uuid.uuid4())
    await ask(api_client, "星辰电商的退货入库怎么操作", conv_id)

    system = fake_providers.calls[-1][0]["content"]
    assert "已确认的事实" in system
    assert "星辰电商" in system
    assert "不要给它们标来源编号" in system


async def test_the_erp_version_survives_past_the_history_window(
    api_client, logged_in, fake_providers, public_chunk, maker, monkeypatch
):
    """⭐⭐ **这一组的验收条件。**

    第 8 轮问「我一开始说的是哪个版本」时，第 1 轮早就掉出 `HISTORY_TURNS` 了——
    历史窗口里一个字都没有。而版本这件事**从来就不在对话记录里**：
    它是新建会话时钉死在 `conversations.knowledge_space_id` 上的。

    ⚠️ 断言分两半，缺一不可：
      1. 送进模型的**历史**里确实没有第 1 轮（否则这题是靠窗口答的，没测到东西）
      2. 送进模型的 **system prompt** 里有版本
    """
    monkeypatch.setenv("SESSION_FACTS_ENABLED", "true")
    get_settings.cache_clear()

    conv_id = str(uuid.uuid4())
    await _ask_n_turns(api_client, conv_id, 8, "星辰电商的退货入库怎么操作")
    await ask(api_client, "我一开始说的是哪个版本", conv_id)

    messages = fake_providers.calls[-1]
    system = messages[0]["content"]
    history = "".join(m["content"] for m in messages[1:])

    # 1. 窗口里真的没有第 1 轮了
    assert "退货入库" not in history, "第 1 轮还在窗口里，这道题没测到跨窗口"
    # 2. 但版本在 system prompt 里
    assert "已确认的事实" in system
    assert "ERP 版本：" in system
    assert "本会话开始时选定" in system


async def test_recording_facts_does_not_route_the_conversation_to_the_agent(
    api_client, logged_in, fake_providers, public_chunk, maker, monkeypatch
):
    """⚠️⚠️ 事实表**绝不能**写进 `profile`。

    `profile is not None` 是「这条会话在走 Agent」的路由标记
    （见 routes/chat.py 的 `_is_agent_conversation`）。往里塞事实的话，
    每一条普通问答会话都会被路由到 Agent 上去——线上表现是用户随口问一句，
    系统开始追问「你们有几个仓」。
    """
    monkeypatch.setenv("SESSION_FACTS_ENABLED", "true")
    get_settings.cache_clear()

    conv_id = str(uuid.uuid4())
    await ask(api_client, "星辰电商的退货入库怎么操作", conv_id)

    async with maker() as s:
        conv = await s.get(Conversation, uuid.UUID(conv_id))
        assert conv.facts, "事实没记上"
        assert conv.profile is None, "事实表串进了 Agent 的路由标记"


async def test_greeting_still_short_circuits_with_facts_on(
    api_client, logged_in, fake_providers, public_chunk, monkeypatch
):
    """寒暄仍然一次模型都不调。事实表不该把这条零成本的路弄没。"""
    monkeypatch.setenv("SESSION_FACTS_ENABLED", "true")
    get_settings.cache_clear()

    conv_id = str(uuid.uuid4())
    before = len(fake_providers.calls)
    r = await ask(api_client, "你好", conv_id)
    assert r.status_code == 200
    assert len(fake_providers.calls) == before
    assert any("知识库助手" in str(p) for p in parts(r.text))
