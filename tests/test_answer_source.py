"""`request_trace.answer_source`：这一轮的答案是从哪来的（M13 P5）。

⭐ **为什么值得单独一组测试。**
M12 把红线从「知识的来源」挪到了「错了会不会伤到人」，于是
「答了但一个来源编号都没有」第一次成了一件**正常且允许**的事。
它同时也是最需要盯着的一件事——而在这一列之前，
「常识答的」和「查库答的」在表里的**每一列上都长得一模一样**：

    直路的 tools 恒为空数组
    chunk_count 只说检索到几块，不说用没用
    answer_kb 既可能引材料也可能拒答

所以这不是一个统计字段，是 M12 那条线**唯一的观察窗口**。
它错了的表现是：报告上「有多少回答来自知识库」这个数字是错的，
而没有任何地方会报错。
"""

from __future__ import annotations

import pytest
from chat_helpers import ask
from sqlalchemy import delete, select

from copilot.api import trace as trace_module
from copilot.db.models import RequestTrace

# ─────────────────────────────────────────────────────────
# 纯函数：判据本身
# ─────────────────────────────────────────────────────────


def classify(**kw) -> str:
    kw.setdefault("route", "direct")
    kw.setdefault("tools", [])
    kw.setdefault("no_answer", False)
    return trace_module.classify_answer_source(**kw)


def test_answer_with_citation_marks_is_kb():
    assert classify(answer="进入【设置】-【打印设置】[1]，勾选自动获取单号。[2]") == "kb"


def test_answer_without_citation_marks_is_general_knowledge():
    """M12 的规矩：常识答案**不标来源编号**。所以没编号 = 没引材料。"""
    assert (
        classify(answer="品牌方指拥有品牌所有权、对库存统一管理的企业，是一盘货模式里的货主方。")
        == "general_knowledge"
    )


def test_no_answer_wins_over_everything():
    """兜底话术优先。它没引用也没工具，但它不是「常识答的」。"""
    assert classify(answer="知识库暂无此内容。", no_answer=True) == "no_answer"
    assert classify(answer="知识库暂无此内容。", no_answer=True, tools=["answer_kb"]) == "no_answer"


def test_canned_route_wins_over_everything():
    assert classify(route="canned", answer="不客气。还有别的问题随时问。") == "canned"


def test_non_terminal_tools_are_tool_source():
    """出方案那条路：正文是围着工具结果写的，既不是材料也不是常识。"""
    plan_tools = ["generate_plan", "save_requirement"]
    assert classify(route="agent", tools=plan_tools, answer="配置清单如下：") == "tool"
    assert classify(route="agent", tools=["my_documents"], answer="你传过 3 份文档。") == "tool"


def test_answer_kb_with_citations_is_kb_not_tool():
    """⚠️ `answer_kb` 是**终结工具**——它的返回就是最终答案。

    把它归成 `tool` 的话，Agent 那条路上所有的知识库问答都会被记成「工具答的」，
    而这一列存在的全部意义就是分开「查库答的」和「常识答的」。
    """
    assert classify(route="agent", tools=["answer_kb"], answer="先绑定物流账号[1]。") == "kb"


def test_answer_kb_without_citations_is_general_knowledge():
    """终结工具跑了、但答案一个编号都没标 —— 那就是它走了常识那条分支。"""
    assert (
        classify(
            route="agent", tools=["answer_kb"], answer="安全库存是为应对需求波动预留的缓冲量。"
        )
        == "general_knowledge"
    )


def test_empty_answer_does_not_crash():
    assert classify(answer="") == "general_knowledge"


# ─────────────────────────────────────────────────────────
# 端到端：真的写进那一列了吗
#
# ⚠️ 纯函数对了不代表列是对的：`TraceDraft.answer` 在四个地方赋值
# （直路正常/取消、Agent 正常/取消），漏掉任何一处的表现都是
# 「那一列静静地记成 general_knowledge」——不报错，只是数字是错的
# ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def clean_traces(maker, logged_in):
    yield
    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.user_id == logged_in))
        await s.commit()


async def _sources(maker, user_id) -> list[str | None]:
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(RequestTrace.answer_source)
                    .where(RequestTrace.user_id == user_id)
                    .order_by(RequestTrace.created_at)
                )
            ).scalars()
        )


async def test_direct_answer_with_citation_is_recorded_as_kb(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    """夹具里的假 LLM 回的是「先绑定物流账号[1]，再打印面单。」——带编号。"""
    _title, body = public_chunk
    assert (await ask(api_client, body)).status_code == 200
    assert await _sources(maker, logged_in) == ["kb"]


async def test_direct_answer_without_citation_is_general_knowledge(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    """把假 LLM 换成一段不带编号的概念解释——就是 M12 放开的那条路。"""
    fake_providers.reply = "品牌方指的是拥有品牌所有权、统一管理库存的那一方。"
    _title, body = public_chunk
    assert (await ask(api_client, body)).status_code == 200
    assert await _sources(maker, logged_in) == ["general_knowledge"]


async def test_no_answer_is_recorded_as_no_answer(
    api_client, logged_in, fake_providers, public_chunk, maker
):
    fake_providers.reply = "知识库暂无此内容。"
    _title, body = public_chunk
    assert (await ask(api_client, body)).status_code == 200
    assert await _sources(maker, logged_in) == ["no_answer"]


async def test_small_talk_is_recorded_as_canned(api_client, logged_in, fake_providers, maker):
    assert (await ask(api_client, "你好")).status_code == 200
    assert await _sources(maker, logged_in) == ["canned"]


async def test_old_rows_keep_null(maker, logged_in):
    """⚠️ 老数据必须留 NULL。

    给这一列一个默认值（比如 'kb'）会让 M13 之前的每一行都凭空变成
    一条查库答案——而 M12 放开常识恰恰是在那段时间上的线，
    那批行正是最需要「不知道」这个状态的。
    """
    import uuid as _uuid
    from datetime import UTC, datetime

    row_id = _uuid.uuid4()
    async with maker() as s:
        s.add(
            RequestTrace(
                id=row_id,
                user_id=logged_in,
                route="direct",
                question="M13 之前写下的一行",
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
    async with maker() as s:
        got = await s.get(RequestTrace, row_id)
        assert got is not None and got.answer_source is None
