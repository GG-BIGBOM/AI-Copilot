"""校验 Agent（W3.2）。

⭐ **这一节里最重要的两道题都是关于"它不做什么"的。**

一个只能减少幻觉的东西，在幻觉率已经是 0.0% 的地方只可能做一件事：
**把对的答案降级成拒答**。所以这些题守的是它的**假阳性面**——
抽不出具体说法就一次调用都不花、校验失败不算"全都有据"、
`off` 那一档一个字节都不许动。

⚠️ 全部不调模型：`verify()` 收一个 llm 对象，测试传假的进去。
真实效果由 `eval/risk_boundary.py --verify` 的付费 A/B 量，见 ADR-22。
"""

from __future__ import annotations

import pytest

from copilot.config import get_settings
from copilot.verifier import (
    MAX_CLAIMS,
    UNVERIFIED_HEAD,
    Verdict,
    annotate,
    extract_claims,
    verify,
)


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeLLM:
    """按脚本回一段 JSON。`calls` 记下被问了几次——**"零次"是好几道题的断言**。"""

    def __init__(self, reply: str = '{"unsupported": []}', boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.calls: list[list[dict]] = []

    def complete(self, messages, temperature=0.0):  # noqa: ARG002 - 和 ChatLLM 同签名
        self.calls.append(messages)
        if self.boom:
            raise RuntimeError("判分服务挂了")
        return self.reply


# ═══════════════ 一、抽取：判的是"答案里有没有可核对的东西" ═══════════════


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("进入【设置–策略设置–短信策略】即可。", ["设置–策略设置–短信策略"]),
        ("到 订单-标缺清点 里确认。", ["订单-标缺清点"]),
        ("重试上限是 48 次。", ["48 次"]),
        ("保留 30 天。", ["30 天"]),
    ],
)
def test_paths_and_parameters_are_extracted(answer, expected):
    assert extract_claims(answer) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "知识库暂无此内容。",
        "库存周转率是衡量存货周转快慢的指标，数值越高说明周转越快。",
        "你好，我是旺店通旗舰版的知识库助手。",
        "",
    ],
)
def test_answers_without_concrete_claims_extract_nothing(answer):
    """⭐ **这是成本控制，也是目标锁定。**

    抽不出东西 = 这段答案里没有用户会照着点的具体说法，校验对它没有意义。
    ⚠️ 而且这样就**不需要一个"这道题高不高风险"的分类器**——
    少一层分类器就少一层误判，而且这个判据和校验要做的事是同一件事。
    """
    assert extract_claims(answer) == []


def test_citation_marks_are_not_mistaken_for_parameters():
    """⚠️ `[图3]` / `[12]` 不是参数值。剥不干净的话，**每一条带引用的答案**
    都要花一次校验调用——而这个项目里几乎每条答案都带引用。"""
    assert extract_claims("按流程操作即可 [1][2]，见 [图3]。") == []


def test_the_claim_list_is_capped():
    """⚠️ 不封顶的话，一段长答案会把整个上下文再塞一遍进校验请求里，
    而校验的价值集中在最前面那几条——它们是用户真的会照着点的。"""
    answer = "。".join(f"第{i}步进入 菜单{i}-子项{i}-页面{i}" for i in range(30))
    assert len(extract_claims(answer)) == MAX_CLAIMS


# ═══════════════ 二、一次调用都不花的那条路 ═══════════════


def test_nothing_to_check_means_no_model_call():
    llm = FakeLLM()
    verdict = verify(llm, "知识库暂无此内容。", "材料")
    assert llm.calls == [], "没有可核对的东西却还是问了一次模型"
    assert verdict.checked is True
    assert verdict.clean is True


# ═══════════════ 三、校验失败 ≠ 全都有据 ═══════════════


def test_a_broken_verifier_is_not_a_pass():
    """⚠️⚠️ **`checked=False` 不是 `unsupported=[]`。**

    把"没核对成"当成"核对过了、全都有据"，就等于模型一掉线这道防线自动
    变成全部放行，且没有任何症状。和 `eval/gate.py` 的 UNRELIABLE 同一条规矩：
    判分器掉线那一轮的数字既不能算好也不能算坏——它什么都不能算。
    """
    verdict = verify(FakeLLM(boom=True), "进入 设置-策略设置 即可。", "材料")
    assert verdict.checked is False
    assert verdict.clean is False
    assert verdict.unsupported == []


def test_garbage_json_is_treated_as_not_checked():
    verdict = verify(FakeLLM(reply="我觉得都挺好的"), "进入 设置-策略设置 即可。", "材料")
    assert verdict.checked is False


def test_a_fenced_json_block_still_parses():
    llm = FakeLLM(reply='```json\n{"unsupported": ["设置-策略设置"]}\n```')
    verdict = verify(llm, "进入 设置-策略设置 即可。", "材料")
    assert verdict.checked is True
    assert verdict.unsupported == ["设置-策略设置"]


def test_only_claims_that_really_appeared_are_reported():
    """⚠️ 模型偶尔会把说法改写一遍再报回来。照单全收的话，标注里会出现一条
    用户在答案里根本找不到的句子——那比不标注更让人困惑。"""
    llm = FakeLLM(reply='{"unsupported": ["设置 › 策略设置（改写过的）", "设置-策略设置"]}')
    verdict = verify(llm, "进入 设置-策略设置 即可。", "材料")
    assert verdict.unsupported == ["设置-策略设置"]


# ═══════════════ 四、标注 ═══════════════


def test_a_clean_verdict_changes_nothing():
    assert annotate("答案原文", Verdict(claims=["x"], unsupported=[], checked=True)) == "答案原文"


def test_a_failed_check_changes_nothing_either():
    """⚠️ 加一句"本轮未能校验"会让用户对每条答案都打个问号，
    而校验掉线是运维问题——写进日志，不写进用户的屏幕。"""
    assert annotate("答案原文", Verdict(claims=["x"], unsupported=[], checked=False)) == "答案原文"


def test_unsupported_claims_are_listed_at_the_end():
    out = annotate("答案原文", Verdict(claims=["48 次"], unsupported=["48 次"], checked=True))
    assert out.startswith("答案原文"), "标注必须是纯追加——前面那段用户已经看到了"
    assert UNVERIFIED_HEAD.strip() in out
    assert "- 48 次" in out


# ═══════════════ 五、`off` 那一档：一个字节都不许动 ═══════════════


def test_off_returns_the_original_stream_object():
    """⚠️ **`off` 走的是同一个对象，不是一层空转的包装。**

    包一层看起来无害，但它会把 `llm.stream_parts` 的惰性求值往后推一帧，
    而首字延迟是这个项目在看的指标之一。默认关的功能不该让默认路径变慢一点点。
    """
    from copilot.qa import _verified_stream

    sentinel = iter([("content", "答案")])

    class Stub:
        def stream_parts(self, messages):  # noqa: ARG002
            return sentinel

    assert _verified_stream(Stub(), [], "材料", "off") is sentinel


def test_the_default_mode_is_off():
    assert get_settings().verifier_mode == "off"


@pytest.mark.parametrize(
    "answer",
    [
        "组合装的复制/补发规则和普通货品一致。",
        "盘点时区分正/残品。",
        "该开关支持启用/停用。",
        "订单要过客审/财审两道。",
    ],
)
def test_chinese_slash_is_not_a_path_separator(answer):
    """⚠️⚠️ **`/` 和 `·` 在中文里是"或者"，不是界面路径的分隔符。**

    第一版把它们收进了分隔符集合，于是 2026-08-29 那轮 A/B 的两条误报里
    有一条就是它：一道**常识题**的答案里写着「复制/补发规则」，
    被当成一条界面路径送去核对——而常识题按 M12 的铁律本来就不该有材料出处，
    **它注定核对不到**。

    误报的代价是给一条正确答案挂一句「未能核对到」，比不标注更伤信任。
    """
    assert extract_claims(answer) == []


def test_bracketed_paths_are_the_common_shape_and_must_extract():
    """⚠️⚠️ **这份语料里最常见的路径写法是带方括号的**：

        【设置】-【打印设置】-【集成打印】

    段与段之间隔着 `】-【` 三个字符。第一版的分隔符只认那一个横线，
    于是**最常见的那种路径一条都抽不出来**——而症状是"校验器很安静"，
    看起来像它认为一切都有据。⭐ 一个什么都查不到的检查器，
    和没有这个检查器是一回事，但它看起来像有。
    """
    assert extract_claims("进入【设置】-【打印设置】-【集成打印】。") == [
        "设置】-【打印设置】-【集成打印"
    ]
    assert extract_claims("路径：订单 › 标缺清点。") == ["订单 › 标缺清点"]
    # 两头挂着的半个括号要去掉——送给校验器的应该是路径本身
    assert extract_claims("见【订单-标缺清点】。") == ["订单-标缺清点"]


def test_the_annotation_itself_is_not_scored_as_an_injection():
    """⭐⭐ **一个防线不许把另一条防线的指标打红。**

    `annotate` 会把「核对不到的说法」原样列在答案末尾——也就是说它会把
    `forbidden_unless_refused` 里那几个词**再打印一遍**。
    2026-08-29 的 A/B 里这一条是**靠运气过的**：标注紧跟在答案自己的拒绝段
    之后，落进了同一个 120 字窗口。换一道末尾没有拒绝措辞的答案，
    同一段标注就会让那道题记成「注入成功」，而且看不出原因。

    ⚠️ 所以标注的开头那句话本身必须是一个能被判分器认出来的拒绝标记。
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
    from risk_boundary import unrefused_hits

    banned = ["预售中心", "一键转单"]
    # 一段**末尾没有任何拒绝措辞**的答案，后面挂上校验标注
    plain = "预售订单的处理流程见材料 [1][2]。" + "。" * 200
    annotated = annotate(
        plain,
        Verdict(
            claims=["设置】-【预售中心】-【一键转单"],
            unsupported=["设置】-【预售中心】-【一键转单"],
            checked=True,
        ),
    )
    assert "预售中心" in annotated, "前提：标注确实把那个词打印了出来"
    assert unrefused_hits(annotated, banned) == [], (
        "校验标注被判成了注入成功——一个防线把另一条防线的指标打红了"
    )
