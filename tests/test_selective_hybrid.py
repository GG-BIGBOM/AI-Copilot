"""按查询形状开词法那一路（Selective Hybrid）。

⭐⭐ **这份测试的输入是 `eval/keyword.yaml` 本身，不是我另编的例子。**

判据（`copilot.query_shape`）要回答的是「哪些查询该走词法召回」，而这个
问题在这个项目里已经有一份**量过的**答案：

    完整问句 30 题   dense 29/30 → hybrid 29/30    收益 0
    裸粘贴   15 条   dense  6/15 → hybrid 15/15    收益全在这里

而 2026-08-29 那轮付费评测把 `HYBRID_ENABLED` 打回默认关，被顶出幻觉的
两道题**都是完整问句**（ADR-16）。也就是说伤害面 100% 落在没有收益的那一半。

所以这里的断言不是「分类器看起来合理」，是**「它必须在那 45 条上和题集
自己的分类完全一致」**：题集里 `kw-paste-*` 是裸粘贴，其余是完整问句。
拿另编的例子测的话，测的是我对"编码型查询长什么样"的想象，
而那正是 早期实施计划里那句「不要凭感觉写 regex」要拦的事。

⚠️ 题集改了这里会红。**那是刻意的**——加一条形状不同的新题，就意味着
判据要重新量一遍，不该让它安静地滑过去。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from copilot.query_shape import has_identifier, is_identifier_query, looks_like_question

KEYWORD_YAML = Path(__file__).resolve().parents[1] / "eval" / "keyword.yaml"


def _cases() -> list[dict]:
    data = yaml.safe_load(KEYWORD_YAML.read_text(encoding="utf-8"))
    return data["cases"]


def _split() -> tuple[list[str], list[str]]:
    """(裸粘贴, 完整问句)。分类依据是题集自己的 id 前缀。"""
    pasted, full = [], []
    for c in _cases():
        (pasted if str(c["id"]).startswith("kw-paste-") else full).append(str(c["q"]))
    return pasted, full


def test_the_dataset_still_has_both_shapes():
    """先钉住题集本身的形状。

    ⚠️ 下面两道题都是「全部命中」式的断言，而一个**空列表**会让它们
    恒真——那正是 ISSUES.md I-4 记的那种「绿着的空断言」。
    先在这里把两边的条数钉死。
    """
    pasted, full = _split()
    assert len(pasted) == 15, f"裸粘贴那一组变成了 {len(pasted)} 条，判据要重新量"
    assert len(full) == 30, f"完整问句那一组变成了 {len(full)} 条，判据要重新量"


@pytest.mark.parametrize("q", _split()[0], ids=lambda q: q[:24])
def test_every_pasted_identifier_goes_hybrid(q: str):
    """15 条裸粘贴**一条都不能漏**——漏一条就丢掉一份 hybrid 的收益。"""
    assert is_identifier_query(q), f"{q!r} 被判成了自然语言问句，会退回纯向量"


@pytest.mark.parametrize("q", _split()[1], ids=lambda q: q[:24])
def test_every_full_question_stays_dense(q: str):
    """30 道完整问句**一道都不能放进来**。

    ⚠️⚠️ 这一侧比上一侧更要紧：hybrid 在完整问句上收益是 0，
    而 2026-08-29 那两道被顶出幻觉的题正是完整问句。
    误判一道进来，就等于把当初被打回来的那个配置又开了一条缝。
    """
    assert not is_identifier_query(q), f"{q!r} 被判成了裸粘贴，会走词法召回"


# ---------- 判据本身的形状 ----------


@pytest.mark.parametrize(
    "q",
    [
        "退货入库怎么操作",  # 疑问词，没问号
        "组合装要不要拆分？",
        "自定义属性最多能添加多少个",
        "你好",  # 寒暄。没有标识符、有中文 → 纯向量
        "帮我出一份实施配置方案",  # 出方案，普通中文
        "",
    ],
    ids=lambda q: q[:16] or "空串",
)
def test_ordinary_chinese_never_goes_hybrid(q: str):
    """普通中文提问一律纯向量，**包括没带问号的那些**。

    第二步（含不含标识符）不能省：少了它，「退货入库流程」这种没带问号的
    普通短语也会被送去词法召回，而它恰恰是最容易被「话题相邻」的块带偏的
    那一类——也就是那 10% 幻觉的成因。
    """
    assert not is_identifier_query(q)


def test_a_question_that_also_contains_a_code_stays_dense():
    """⭐ **顺序不能反。** 完整问句里几乎必然也含编码。

    先判标识符的话，「平台物流编码 YUNDA 对应哪家快递公司？」会被送去
    词法召回——而那正是被打回来的那个配置。
    """
    q = "平台物流编码 YUNDA 对应哪家快递公司？"
    assert has_identifier(q), "前提不成立：这句里本来就含大写编码"
    assert looks_like_question(q)
    assert not is_identifier_query(q)


def test_an_all_ascii_error_string_is_caught_without_any_code_shape():
    """`gate invalid packet` 三条形状规则一条都不命中，靠「整句没有中文」收。

    ⚠️ 这一条单列出来是因为它最容易在重构时被顺手删掉——
    另外三条规则看起来已经"覆盖了编码"，而这一类恰恰是最典型的粘贴。
    """
    q = "gate invalid packet"
    assert not has_identifier(q), "前提不成立：这句里没有大写串/长数字/驼峰"
    assert is_identifier_query(q)


def test_short_digits_are_not_identifiers():
    """三位以内的数字不算编码——「3 个仓」「第 100 页」都会命中。"""
    assert not has_identifier("100")
    assert not has_identifier("第 100 页")
    assert has_identifier("1009")


# ---------- 接线 ----------


def test_the_flag_is_off_by_default():
    """⚠️ 默认关，而且这一轮不打开。

    同 `hybrid_enabled` / `session_facts_enabled` 那条老规矩：改了会让答案变、
    但绝不会报错的东西，一律先做成开关、默认关，等 A/B 数字出来再谈开不开。
    """
    from copilot.config import Settings

    assert Settings().selective_hybrid_enabled is False
    assert Settings().hybrid_enabled is False


def test_always_on_hybrid_wins_over_selective():
    """两个开关都开时以「一律开」为准，不出现第三种谁也说不清的组合。"""
    import inspect

    from copilot import retrieve

    src = inspect.getsource(retrieve._search)
    assert "s.hybrid_enabled or (" in src, "优先级写法变了，这条断言要跟着改"
