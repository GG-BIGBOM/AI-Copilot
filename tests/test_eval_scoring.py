"""评测判分口径的测试（M13 P0）。

⭐ **为什么要给「仪器」写测试。**
2026-08-20 的 `m12-general-on` 那一轮，61 题里 5 题挂在判分器的 SSL 断连上，
报告显示准确率 88.5%，而严格版是 95.1%——看起来像「放开常识把系统打退化了
6.6 个点」。真实情况是其中 4 个点纯粹是国内连 Gemini 的网络。
差一点就据此把一个正确的产品决定回滚掉。

评测是用来做决定的仪器。仪器读数会被网络污染而没人看得出来，比没有仪器更危险——
没有仪器至少知道自己在猜。所以这一层要有测试，和业务代码一样。

这里全是**纯函数**测试：不连数据库、不调模型、不联网。
`score()` 吃的是已经填好的 `CaseResult`，判分口径的对错在这一层就能定死。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def _load_run():
    """按文件路径载入 `eval/run.py`。

    ⚠️ **不能 `import run`**：那个名字太通用，谁都可能有一个 `run.py`；
    也不能把 eval/ 当包 import——它没有 `__init__.py`，而且 `eval` 撞内建函数名。
    起个显式的模块名，只此一处。
    """
    if "eval_run" in sys.modules:
        return sys.modules["eval_run"]
    spec = importlib.util.spec_from_file_location("eval_run", EVAL_DIR / "run.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load_run()


def case(cid: str, kind: str = "fact", **extra) -> dict:
    return {"id": cid, "kind": kind, "q": "问题", **extra}


def result(cid: str, kind: str = "fact", **kw):
    return run.CaseResult(id=cid, kind=kind, q="问题", **kw)


# ─────────────────────────────────────────────────────────
# 三态：correct / incorrect / invalid
# ─────────────────────────────────────────────────────────


def test_judge_error_is_invalid_not_incorrect():
    """判分器断线 = 这题没评上，**不是模型答错**。这是 P0 的全部要点。"""
    cases = [case("a"), case("b")]
    results = [
        result("a", answer="答案 A[1]", verdict="correct", grounded=True),
        result("b", answer="答案 B[1]", verdict="judge_error", judge_error=True),
    ]
    m = run.score(results, cases)

    assert [r.status for r in results] == ["correct", "invalid"]
    assert m["题数"] == 2
    assert m["有效题数"] == 1
    assert m["判分失效"] == 1
    # ⭐ 分母是 1 不是 2：一对一错会是 50%，而这一轮只评上了一题，且判对了
    assert m["准确率"] == 100.0
    assert m["判分失效率"] == 50.0


def test_accuracy_denominator_excludes_invalid():
    """任务书里那张示例报告：61 题、2 题判分失效、58 对 1 错 → 98.3%。"""
    cases = [case(f"c{i}") for i in range(61)]
    results = [result(f"c{i}", answer="x[1]", verdict="correct", grounded=True) for i in range(58)]
    results.append(result("c58", answer="x[1]", verdict="wrong", grounded=True))
    results += [
        result(f"c{i}", answer="x[1]", verdict="judge_error", judge_error=True) for i in (59, 60)
    ]

    m = run.score(results, cases)
    assert (m["题数"], m["有效题数"], m["判分失效"]) == (61, 59, 2)
    assert (m["判对"], m["判错"]) == (58, 1)
    assert m["准确率"] == 98.3
    assert m["判分失效率"] == 3.3
    assert m["可信"] is True


def test_unreliable_when_judge_error_rate_over_limit():
    """失效率超 5% → 整轮标不可信，不能拿来比较 prompt。"""
    cases = [case(f"c{i}") for i in range(10)]
    results = [result(f"c{i}", answer="x[1]", verdict="correct", grounded=True) for i in range(9)]
    results.append(result("c9", answer="x[1]", verdict="judge_error", judge_error=True))

    m = run.score(results, cases)
    assert m["判分失效率"] == 10.0
    assert m["可信"] is False


# ─────────────────────────────────────────────────────────
# 确定性判定优先于判分器
#
# 判分器挂了**不代表**这题就没结论：一半的判定根本不需要它
# ─────────────────────────────────────────────────────────


def test_hallucination_still_counted_when_judge_dies():
    """该说不知道却答了——看答案文本就能定，判分器挂了也照样算错。

    这条反过来做会很危险：把它洗成 INVALID，等于让幻觉率跟着网络质量抖，
    而幻觉率是这个项目唯一一条硬指标。
    """
    cases = [case("neg", kind="no_answer")]
    results = [
        result(
            "neg",
            kind="no_answer",
            answer="进入【设置】-【店铺】，点击添加。[1]",
            said_no_answer=False,
            verdict="judge_error",
            judge_error=True,
        )
    ]
    m = run.score(results, cases)
    assert results[0].status == "incorrect"
    assert m["判分失效"] == 0
    assert m["幻觉率"] == 100.0


def test_false_negative_still_counted_when_judge_dies():
    """材料里有答案却答「暂无此内容」，同样是确定性失败。"""
    cases = [case("pos")]
    results = [
        result(
            "pos",
            answer="知识库暂无此内容。",
            said_no_answer=True,
            verdict="judge_error",
            judge_error=True,
        )
    ]
    m = run.score(results, cases)
    assert results[0].status == "incorrect"
    assert m["假阴性率"] == 100.0
    assert m["判分失效"] == 0


def test_missing_fact_beats_judge_error():
    cases = [case("f", must_include=["JTSD"])]
    results = [
        result(
            "f",
            answer="编码在设置里",
            missing_facts=["JTSD"],
            verdict="judge_error",
            judge_error=True,
        )
    ]
    run.score(results, cases)
    assert results[0].status == "incorrect"
    assert "漏掉关键事实" in results[0].fail_why


def test_hallucination_rate_ignores_invalid_denominator():
    """幻觉率的分母是**全部** no_answer 题，不剔 invalid（它不需要判分器）。"""
    cases = [case("n1", kind="no_answer"), case("n2", kind="no_answer")]
    results = [
        result("n1", kind="no_answer", answer="知识库暂无此内容。", said_no_answer=True),
        result("n2", kind="no_answer", answer="知识库暂无此内容。", said_no_answer=True),
    ]
    m = run.score(results, cases)
    assert m["幻觉率"] == 0.0


# ─────────────────────────────────────────────────────────
# must_include：一条事实可以有几种写法
# ─────────────────────────────────────────────────────────


def test_a_fact_can_be_written_several_ways():
    """⭐ 2026-08-23 判错的原句：题面要「批量入库」，答案写的是「批量采购入库」，
    语料原文的标题是「采购批量入库」——三种词序说的是同一件事。
    这道题量的是有没有漏掉这条入库方式，不是词序对不对。"""
    wanted = ['快速入库', ['批量入库', '批量采购入库', '采购批量入库']]
    assert run.missing_facts('支持快速入库，也支持批量采购入库。', wanted) == []
    assert run.missing_facts('支持快速入库和采购批量入库。', wanted) == []


def test_a_missing_fact_is_still_missing():
    """同义组不是给放宽事实判定开的口子：一个都没出现就还是漏。"""
    wanted = ['快速入库', ['批量入库', '批量采购入库']]
    assert run.missing_facts('只讲了快速入库。', wanted) == ['批量入库']


def test_plain_string_facts_still_match_exactly():
    assert run.missing_facts('上限是 4320 分钟。', ['4320']) == []
    assert run.missing_facts('上限是 3 天。', ['4320']) == ['4320']


# ─────────────────────────────────────────────────────────
# must_not_include：出现即算错，但别把否定句判成违规
# ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer,banned",
    [
        ("群消息通知不支持指定员工，只支持 @所有人。", ["支持指定员工"]),
        ("面单统一 76×130，不使用二联。", ["二联"]),
        ("以 ERP 出库单为准，不是平台结算单为准。", ["平台结算单"]),
    ],
)
def test_banned_matcher_skips_negated_mentions(answer, banned):
    """⚠️ 这三句都是**标准答案**，历史结果里全被裸子串匹配判成了违规。

    抓串台的规则把正确答案抓成违规，比不抓更糟——它会让人去"修"一个没坏的东西。
    """
    assert run.banned_hits(answer, banned) == []


def test_banned_matcher_catches_real_contamination():
    """真串了别家的数字就要抓住——这条是 P1 跨平台污染率的判据。"""
    answer = "京东电子面单的模板尺寸是 100×180，在打印设置里选择。"
    assert run.banned_hits(answer, ["100×180"]) == ["100×180"]


@pytest.mark.parametrize(
    "answer,banned",
    [
        # 2026-08-23 风险边界实测原句（`st-cancel-release-stock`）：
        # 先照材料讲 JIT 的规则、点明出处，再划清界限。排除句在被禁串**后面**，
        # 往前看三个字的窗口够不着
        (
            "JIT 实时订单占用后 48 小时未核销会自动释放，或按订单创建时间+x 小时后"
            "当天的夜间定时任务自动释放。[1] 但普通淘宝订单不适用此规则。",
            ["夜间定时任务"],
        ),
        ("材料里讲的是唯品会 JIT 的立即释放规则，与普通淘宝订单无关。", ["立即释放"]),
        # 划界的话也可能在被禁串**前面**（同一道题的另一种写法）
        (
            "以上释放逻辑针对普通订单。JIT 实时订单的库存释放规则不同——"
            "占用后 48 小时未核销自动释放，或在设置的时间点后由夜间定时任务释放。[1]",
            ["夜间定时任务"],
        ),
    ],
)
def test_banned_matcher_skips_terms_that_are_explicitly_excluded(answer, banned):
    """⭐ 把别家的规则摆出来、再说清「不适用」，正是铁律 8 要的答法。

    判成违规的话，唯一能让指标变好的做法是让模型闭嘴——
    而「什么都不敢答」恰恰是这套指标最容易被骗过去的失败形态。
    """
    assert run.banned_hits(answer, banned) == []


def test_exclusion_is_a_window_not_a_pardon_for_the_whole_answer():
    """赦免只在被禁串近旁生效——隔着一整段的「不适用」不算数。

    ⚠️ 这条钉的是**近似的边界**：这个函数不理解中文，只看被禁串附近有没有划界的话。
    离得远的排除句救不回一处真串台，正是想要的结果；
    真正分不开的情形（编完数字紧跟一句"另一家的规则不适用"）留在
    `banned_hits` 的文档里明写，靠出题时把禁串写具体来兜。
    """
    answer = (
        "Temu 的面单模板尺寸是 100×180，在打印设置里选择模板后保存，"
        "打印前记得确认打印机型号、纸张方向和面单版本都设置正确，否则会打偏。"
        "另外补充一句，京东的共享面单规则不适用于 Temu。"
    )
    assert run.banned_hits(answer, ["100×180"]) == ["100×180"]


def test_banned_content_fails_the_case():
    """dataset.yaml 写着「出现即算错」，代码里现在真的算错了。

    ⚠️ M13 之前这个判定只写进 `unsupported` 那句说明，**没有任何地方拿它判分**。
    """
    cases = [case("x", must_not_include=["100×180"])]
    results = [
        result(
            "x",
            answer="京东面单尺寸 100×180。",
            banned_hits=["100×180"],
            verdict="correct",
            grounded=True,
        )
    ]
    run.score(results, cases)
    assert results[0].status == "incorrect"
    assert "禁止内容" in results[0].fail_why


# ─────────────────────────────────────────────────────────
# 重试口径
# ─────────────────────────────────────────────────────────


def test_judge_retry_budget_is_bounded():
    """最多 3 次、指数退避、不无限重试。"""
    assert run.JUDGE_RETRIES == 3
    assert run.JUDGE_BACKOFF == (1.0, 2.0, 4.0)
    assert run.JUDGE_TIMEOUT <= 120.0


def test_compare_refuses_unreliable_runs(capsys):
    """老结果里没有 `reliable` 字段，要能从 cases 现算出来。"""
    stale = {
        "tag": "m12-general-on",
        "metrics": {"准确率": 88.5},
        "cases": [{"verdict": "judge_error"} for _ in range(5)]
        + [{"verdict": "correct"} for _ in range(56)],
    }
    ok, rate, stuck = run._reliability(stale)
    assert (ok, stuck) == (False, 5)
    assert rate == 8.2


# ─────────────────────────────────────────────────────────
# M19-A：空间与配图的负例
#
# 路线图 49「Evaluation 自身也要有测试」的四个 case。四条全是**规则判定**，
# 手工造结果就能钉死口径——评测这台仪器自己有没有刻度，不该等到跑真题时才知道
# ─────────────────────────────────────────────────────────


def test_case_b_cross_space_contamination_is_counted():
    """Case B：本轮问的是这个空间，召回里却混进了别的空间的块。

    ⭐ 它**不看判分器**：`foreign_space_hits` 是逐块核对 `knowledge_space_id`
    得来的，判分器挂着也算得出来。这正是它能当 M18 门禁的原因。
    """
    cases = [case("x"), case("y")]
    results = [
        result("x", answer="答案[1]", verdict="correct", grounded=True, foreign_space_hits=2),
        result("y", answer="答案[1]", verdict="correct", grounded=True),
    ]
    m = run.score(results, cases)
    assert m["跨空间污染率"] == 50.0
    assert m["跨空间污染块数"] == 2


def test_clean_run_has_zero_contamination():
    """全部块都在本空间（或 common）里时，这个数必须是 0.0 而不是缺项。

    缺项和 0 在报告上长得不一样，但在「门禁通过了吗」这个问题上会被同样地
    读成"没问题"——所以它得永远在。
    """
    cases = [case("x")]
    m = run.score([result("x", answer="答案[1]", verdict="correct", grounded=True)], cases)
    assert m["跨空间污染率"] == 0.0


def test_case_d_wrong_image_context_is_counted():
    """Case D：配图编号真实存在，但那张图出自**答案没引用过**的文档。

    正文可以完全正确、引用也可以完全正确——用户看到一张界面截图，
    却在正文里找不到任何一个 `[n]` 指向它所在的那篇，**无处可考**。
    这类失败在准确率、引用正确率、配图带出率上一个数都不会动。
    """
    cases = [case("pic", source="微信视频号电子面单")]
    results = [
        result(
            "pic",
            answer="按 [图1] 里的位置点开。[1]",
            verdict="correct",
            grounded=True,
            citations=[{"n": 1, "title": "微信视频号电子面单"}],
            context_images=[{"n": 1, "url": "/images/ab/1.png", "title": "抖音电子面单"}],
        )
    ]
    m = run.score(results, cases)
    assert results[0].foreign_image_refs == [1]
    assert m["配图串台率"] == 100.0
    assert m["可判串台数"] == 1
    # ⚠️ 串台**不算答错**：正文可能确实是对的。它是单独一条要压到 0 的指标，
    # 混进准确率会让"配错图"和"答错事"变成同一个数
    assert results[0].status == "correct"


def test_an_image_from_another_cited_document_is_not_contamination():
    """⚠️⚠️ **这条是第一版判据的墓碑。**

    第一版判的是「图出自题目声明的期望来源以外的文档」，在 75 道真题上
    量出 40%——逐条看下去一条真的都没有。最典型的是 `proc-purchase-settle`：

        正文写「生成一条对应的应付单 [2][图5]」，[2] 就是《账款 · 应收应付》，
        图5 也正是那一篇里的截图——严丝合缝，却被判成串台。

    答案本来就跨文档作答。期望来源是出题人标的"该命中哪一篇"，
    **从来不是"只许用这一篇的图"**。一个天天误报的指标比没有更糟：
    人会学会忽略它，然后连真的那一次也一起忽略。
    """
    cases = [case("pic", source="采购流程")]
    results = [
        result(
            "pic",
            answer="结算后生成应付单 [2][图5]",
            verdict="correct",
            grounded=True,
            citations=[{"n": 1, "title": "采购 · 采购流程"}, {"n": 2, "title": "账款 · 应收应付"}],
            context_images=[{"n": 5, "url": "/i/5.png", "title": "账款 · 应收应付"}],
        )
    ]
    m = run.score(results, cases)
    assert results[0].foreign_image_refs == []
    assert m["配图串台率"] == 0.0


def test_an_image_from_a_retrieved_but_uncited_document_is_contamination():
    """召回了、但正文没引用的那一篇，它的图同样是"无处可考"。

    分母只看正文里的 `[n]`，不看召回清单——用户能溯源的只有正文里的编号。
    """
    cases = [case("pic")]
    results = [
        result(
            "pic",
            answer="照着 [图2] 操作即可。[1]",
            verdict="correct",
            grounded=True,
            citations=[{"n": 1, "title": "采购 · 采购流程"}, {"n": 2, "title": "仓储 · 图片验货"}],
            context_images=[{"n": 2, "url": "/i/2.png", "title": "仓储 · 图片验货"}],
        )
    ]
    m = run.score(results, cases)
    assert results[0].foreign_image_refs == [2]


def test_invalid_image_reference_is_a_deterministic_failure():
    """写了上下文里没有的 [图N] = 配图版的假引用，判错，且不靠判分器。"""
    cases = [case("pic")]
    results = [
        result(
            "pic",
            answer="见 [图3]。[1]",
            verdict="correct",
            grounded=True,
            judge_error=True,
            context_images=[{"n": 1, "url": "/i/1.png", "title": "某篇"}],
        )
    ]
    m = run.score(results, cases)
    assert results[0].bad_image_refs == [3]
    assert results[0].status == "incorrect"  # 不是 invalid
    assert "不存在的配图" in results[0].fail_why
    assert m["无效配图率"] == 100.0


def test_image_reference_with_empty_context_is_still_invalid():
    """⚠️ 上下文一张图都没有时的 [图1] 是**最该抓住**的那一类，不能豁免。

    第一版写成「没有图就没什么可比的」直接返回空——于是凭空编图恒判通过。
    """
    assert run.bad_image_refs("见 [图1]", []) == [1]


def test_wrong_image_context_needs_titles_to_be_judgeable():
    """Agent 路上图片没有出处（title 为空）：不判串台，也不假装判过。

    ⭐ 这条守的是**分母**。`配图串台率` 缺项时报告会打「未量到」，
    而不是打一个 0.0%——后者会被读成「配图很干净」。
    """
    cases = [case("pic", source="微信视频号")]
    results = [
        result(
            "pic",
            answer="见 [图1]。[1]",
            verdict="correct",
            grounded=True,
            citations=[{"n": 1, "title": "微信视频号电子面单"}],
            context_images=[{"n": 1, "url": "/i/1.png"}],  # 没有 title
        )
    ]
    m = run.score(results, cases)
    assert results[0].foreign_image_refs == []
    assert m["可判串台数"] == 0
    assert "配图串台率" not in m
    assert m["带图答案数"] == 1


def test_answers_without_pictures_are_out_of_the_image_denominators():
    """没写 [图N] 的答案不进配图两条指标的分母。"""
    cases = [case("a")]
    m = run.score([result("a", answer="纯文字答案[1]", verdict="correct", grounded=True)], cases)
    assert "无效配图率" not in m
    assert m.get("带图答案数") is None


def test_case_c_unsupported_claim_is_flagged():
    """Case C：引用编号存在，但材料不支持那句话。

    这一条**必须**靠判分器（"材料支不支持这句话"没有规则判法），
    所以它的分母要剔掉 invalid——见 `score()` 里 `无据陈述率` 那一段。
    """
    cases = [case("a"), case("b")]
    results = [
        result("a", answer="材料里没有的说法[1]", verdict="correct", grounded=False),
        result("b", answer="老实的答案[1]", verdict="correct", grounded=True),
    ]
    m = run.score(results, cases)
    assert m["无据陈述率"] == 50.0


def test_space_and_corpus_are_part_of_the_archived_config():
    """结果档案里必须看得出这一轮跑的是哪个空间。

    没有它，两轮只差空间的结果存出来一模一样，而 `--compare` 会把
    两个不同的题集当成同一个题集比大小。
    """
    assert run.Config().resolved()["space"] == run.DEFAULT_SPACE
    assert run.Config(space="enterprise_web").resolved()["space"] == "enterprise_web"


# ─────────────────────────────────────────────────────────
# 重算与重判：口径改了，答案不用重跑
# ─────────────────────────────────────────────────────────


def test_rescore_does_not_lose_the_context_length(tmp_path, monkeypatch):
    """`--rescore` 只重算指标，**不许顺手弄丢诊断信息**。

    `context_chars` 只在第一次存档时算得出来（那时 `context` 还在）。
    重算一次就把它抹成 0，是一个报告上完全看不出来的静默损失。
    """
    import json

    monkeypatch.setattr(run, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run, "load_cases", lambda **kw: ({}, [case("a")]))
    (tmp_path / "t.json").write_text(
        json.dumps(
            {
                "tag": "t",
                "suite": "dataset",
                "scope": "public",
                "config": {"space": "flagship"},
                "metrics": {},
                "cases": [
                    {
                        "id": "a",
                        "kind": "fact",
                        "q": "问题",
                        "answer": "答案[1]",
                        "verdict": "correct",
                        "grounded": True,
                        "context_chars": 2500,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run.rescore("t")
    after = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert after["cases"][0]["context_chars"] == 2500
    assert after["metrics"]["准确率"] == 100.0
    assert after["rescored_at"]


def test_judging_again_clears_the_previous_judge_error(monkeypatch):
    """判分是可以重跑的：这一次判成了，上一次的「没判成」就必须被清掉。

    留着的话，一条已经判成功的结果会继续被算成 invalid——准确率的分母
    悄悄少一题，而报告上看不出来。
    """
    from copilot.providers import llm as llm_module

    class FakeJudge:
        def __init__(self, *a, **kw):
            pass

        def complete(self, messages, temperature=0.0):
            return '{"verdict": "correct", "grounded": true, "unsupported": "", "reason": "ok"}'

        def close(self):
            pass

    monkeypatch.setattr(llm_module, "ChatLLM", FakeJudge)
    stuck = result("a", answer="答案[1]", verdict="judge_error", judge_error=True, context="材料")
    run.judge_all([stuck], [case("a")], workers=1, quiet=True)
    assert stuck.judge_error is False
    assert stuck.verdict == "correct"
