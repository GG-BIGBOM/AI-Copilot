"""周质量报告（M13 P10）。

⚠️ **每个端到端用例都把范围限定在自己这个用户上**（`user_id=logged_in`）。
开发库里攒着几百行别的测试造出来的台账，不限定的话量到的是那一堆，
断言会以一种很难看懂的方式失败（"20 条 1ms 的寒暄把 8 秒首字冲没了"，
而实际上是另外 370 行把它冲没了）。

这条命令的产出是给人看的文字，不是给程序用的数据结构，所以测试盯两件事：

1. **算数不能错。** 百分位是自己算的（不引 numpy），而它是唯一一个
   「看起来对、其实错了也没人发现」的地方——延迟数字没人会去手算核对。
2. **口径不能悄悄变。** 差评率的分母、延迟要不要算上寒暄、老数据算哪一类，
   每一条都是刻意选的；改掉其中任何一条，报告的结论会变，而报告本身
   仍然打得出来、看起来一切正常。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from copilot.cli import _pct, _percentile, _quality_report
from copilot.db.models import RequestTrace

# ─────────────────────────────────────────────────────────
# 纯函数
# ─────────────────────────────────────────────────────────


def test_percentile_on_empty_is_none():
    assert _percentile([], 0.5) is None


def test_percentile_of_one_value():
    assert _percentile([7], 0.5) == 7
    assert _percentile([7], 0.95) == 7


def test_percentile_picks_from_the_sorted_values():
    """⭐ 最近邻取法：p50 / p95 都必须是**样本里真实出现过的那个数**。

    不做插值是刻意的。样本量常常只有两位数，这时候插值给出的
    「p95 = 1873.5ms」是一个从未发生过的事件，而它会被当成
    「有一次请求慢到 1873ms」去排查。
    """
    values = list(range(1, 21))  # 1..20
    assert _percentile(values, 0.5) in values
    assert _percentile(values, 0.95) in values
    assert _percentile(values, 0.5) == 11
    assert _percentile(values, 0.95) == 20


def test_percentile_is_order_independent():
    import random

    values = [5, 100, 3, 88, 12, 7, 400, 9]
    shuffled = values[:]
    random.shuffle(shuffled)
    assert _percentile(shuffled, 0.95) == _percentile(values, 0.95)


def test_percentile_never_indexes_past_the_end():
    """q=0.95 且 n=20 时 int(0.95*20)=19 —— 正好是最后一个，不能越界。"""
    for n in range(1, 60):
        assert _percentile(list(range(n)), 0.95) is not None


def test_pct_handles_zero_denominator():
    """⚠️ 分母为 0 要给「—」，不能是 0.0%。

    「没人评价过」和「评价过但一条差评都没有」是两件完全不同的事，
    显示成 0.0% 会让前者看起来像后者——而那正是最需要区分的一对。
    """
    assert _pct(0, 0) == "—"
    assert _pct(0, 10) == "0.0%"
    assert _pct(1, 3) == "33.3%"


# ─────────────────────────────────────────────────────────
# 端到端：口径
# ─────────────────────────────────────────────────────────


@pytest.fixture
async def seeded(maker, logged_in):
    """造一批已知构成的台账。"""
    made: list[uuid.UUID] = []

    async def add(**kw) -> uuid.UUID:
        row_id = uuid.uuid4()
        kw.setdefault("route", "direct")
        kw.setdefault("created_at", datetime.now(UTC) - timedelta(hours=1))
        async with maker() as s:
            s.add(RequestTrace(id=row_id, user_id=logged_in, question="q", **kw))
            await s.commit()
        made.append(row_id)
        return row_id

    yield add

    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.id.in_(made)))
        await s.commit()


async def test_report_runs_and_counts_sources(seeded, capsys, maker, logged_in):

    await seeded(answer_source="kb", ttfb_ms=200, total_ms=900, tokens=100)
    await seeded(answer_source="general_knowledge", ttfb_ms=300, total_ms=800, tokens=50)
    await seeded(answer_source="no_answer", ttfb_ms=100, total_ms=200, tokens=20)
    await seeded(answer_source="canned", route="canned", ttfb_ms=1, total_ms=2, tokens=0)
    await seeded(answer_source=None, tokens=10)  # M13 之前的老行

    await _quality_report(7, maker=maker, user_id=logged_in)
    out = capsys.readouterr().out

    assert "知识库回答" in out
    assert "常识回答（无出处）" in out
    assert "M13 之前的老数据" in out, (
        "老数据必须单列。并进任何一类都会让那一类凭空变大，"
        "而这份报告的用途恰恰是判断放开常识之后有多少回答没有出处"
    )


async def test_old_rows_are_not_counted_as_kb(seeded, capsys, maker, logged_in):
    """⚠️ `answer_source` 是 NULL 的老行**不能**被算成知识库回答。

    算进去的话，M12 放开常识那段时间的行会全部变成「有出处」，
    而那批行正是最需要「不知道」这个状态的。
    """

    for _ in range(3):
        await seeded(answer_source=None)

    await _quality_report(7, maker=maker, user_id=logged_in)
    out = capsys.readouterr().out
    assert "知识库回答" not in out
    assert "M13 之前的老数据" in out


async def test_canned_is_excluded_from_latency(seeded, capsys, maker, logged_in):
    """⭐ 寒暄不进延迟统计。

    它一次模型调用都不花、首字是毫秒级的，混进来会把 p50 拉到看不出问题——
    而这两个数字存在的全部意义就是回答「用户等了多久」。
    """

    await seeded(route="direct", answer_source="kb", ttfb_ms=8000, total_ms=9000)
    for _ in range(20):
        await seeded(route="canned", answer_source="canned", ttfb_ms=1, total_ms=2)

    await _quality_report(7, maker=maker, user_id=logged_in)
    out = capsys.readouterr().out
    ttfb_line = next(line for line in out.splitlines() if "首字 TTFB" in line)
    assert "8000" in ttfb_line, "20 条 1ms 的寒暄把真实的 8 秒首字冲没了"
    assert "（1 次）" in ttfb_line


async def test_agent_route_filter_excludes_direct_rows(seeded, capsys, maker, logged_in):
    """P12 的灰度报告只能统计 Agent 路，不能混入旧直路。"""

    await seeded(route="direct", answer_source="kb", tools=[])
    await seeded(
        route="agent",
        answer_source="general_knowledge",
        tools=["answer_kb"],
        ttfb_ms=1200,
    )

    await _quality_report(7, route="agent", maker=maker, user_id=logged_in)
    out = capsys.readouterr().out

    assert "只看 agent 路" in out
    assert "提问数                 1" in out
    assert "Agent 轮次             1" in out
    assert "常识回答（无出处）" in out
    assert "知识库回答" not in out


async def test_agent_no_tool_count_is_not_the_bypass_count(
    seeded, capsys, maker, logged_in
):
    """tools 为空可能是拒答或允许的常识；只有伪造材料编号才算 bypass。"""

    await seeded(
        route="agent", answer_source="no_answer", no_answer=True, tools=[]
    )
    await seeded(
        route="agent", answer_source="general_knowledge", no_answer=False, tools=[]
    )
    await seeded(route="agent", answer_source="kb", no_answer=False, tools=[])

    await _quality_report(7, route="agent", maker=maker, user_id=logged_in)
    out = capsys.readouterr().out

    assert "其中 tools 为空       3" in out
    assert "越过工具直答           1" in out


async def test_user_cancellation_is_not_counted_as_service_error(
    seeded, capsys, maker, logged_in
):
    await seeded(ok=False, error="CancelledError: user stopped generation")
    await seeded(ok=False, error="RuntimeError: provider unavailable")

    await _quality_report(7, maker=maker, user_id=logged_in)
    out = capsys.readouterr().out

    assert "用户主动中断           1" in out
    assert "出错                   1" in out


async def test_no_cost_is_printed_without_a_price_config(seeded, capsys, maker, logged_in):
    """**没有可靠的价格配置就不印成本。**

    硬编码一个单价，半年后换模型 / 调价之后，报告会一本正经地给出一个
    错的成本 —— 那比不给更糟，因为它看起来像真的。
    """
    await seeded(answer_source="kb", tokens=1000)

    await _quality_report(7, maker=maker, user_id=logged_in)
    out = capsys.readouterr().out
    assert "成本未估算" in out
    assert "¥" not in out and "$" not in out


async def test_empty_window_says_so(capsys, maker, logged_in):
    await _quality_report(0, maker=maker, user_id=logged_in)  # 窗口是「现在之后」，必然为空
    assert "一条请求都没有" in capsys.readouterr().out
