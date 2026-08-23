"""M10 P1：终结工具的机制。不联网、不花钱——模型行为用 `FunctionModel` 写死。

「终结工具」= 它的返回**就是**给用户的最终答案，Agent 不复述、不加工。
这里守的是四件事，每一件坏掉的表现都是「页面上安静地不对」而不是报错：

1. **终结工具的正文要原样直通前端**，一个字都不能少。
2. **Agent 自己写的正文，在有终结答案时要整个丢掉。** 模型几乎一定会
   在工具之后再补一句「以上就是全部内容，希望对你有帮助」——那句话对用户
   毫无价值，还会让答案看起来像是它写的。
3. **调工具之前的开场白也要丢掉。** 「我查一下」顶在答案前面很难看，
   而且它是在工具跑完**之前**就产生的，边流边发就收不回来了。
4. **没有终结答案时，Agent 自己的话必须照常吐出来。** 追问、时间、闲聊
   全靠这条；丢错了的表现是「问它几点，它一个字都不回」。
"""

from __future__ import annotations

import json
import uuid

import pytest
from chat_helpers import FakeEmbedder, FakeLLM, TopOneReranker, parts
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete

from copilot.agent.deps import AgentDeps
from copilot.agent.runner import run_agent_stream
from copilot.agent.tools import answer_kb
from copilot.db.models import Chunk, Document

KB_REPLY = "先绑定物流账号[1]，再打印面单。"


def scripted(*turns) -> FunctionModel:
    """按顺序把这些回复喂给 Agent。多要一次就报错——那说明它多转了一圈。

    每一轮要么是一段文本（`str`），要么是一次工具调用（`call("...")`）。
    ⚠️ 流式模式下**一轮里不能既有文本又有工具调用**，那是 `FunctionModel` 的
    限制、不是真模型的（真模型会两样一起给）。所以「调工具前先说一句开场白」
    那条不在这里测，见 `test_preamble_never_reaches_the_stream`。
    """
    it = iter(turns)

    async def f(messages, info):  # noqa: ARG001 - FunctionModel 的签名要求
        try:
            turn = next(it)
        except StopIteration:  # pragma: no cover - 只有测试写错了才会到这
            raise AssertionError("Agent 比预期多请求了一次模型") from None
        if isinstance(turn, str):
            for ch in turn:  # 一个字一个字地吐，逼出多个 text-delta
                yield ch
        else:
            yield turn

    return FunctionModel(stream_function=f)


def call(name: str, **args) -> dict[int, DeltaToolCall]:
    return {
        0: DeltaToolCall(
            name=name,
            json_args=json.dumps(args, ensure_ascii=False),
            tool_call_id=f"call_{name}",
        )
    }


def _deps(session, **kw) -> AgentDeps:
    return AgentDeps(
        session=session,
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        embedder=FakeEmbedder(),
        reranker=TopOneReranker(),
        llm=kw.pop("llm", None) or FakeLLM(KB_REPLY),
        **kw,
    )


async def drain(question: str, deps: AgentDeps, model: FunctionModel) -> tuple[list[dict], str]:
    """跑一轮，返回 (协议片段列表, 最后一次报告的正文)。"""
    body: list[str] = []
    answer = ""
    async for part, so_far in run_agent_stream(question, deps, model=model):
        body.append(part)
        answer = so_far
    return parts("".join(body)), answer


def text_of(chunks: list[dict]) -> str:
    return "".join(c["delta"] for c in chunks if c["type"] == "text-delta")


# ---------- 1 / 2：终结答案直通，Agent 的复述丢掉 ----------


async def test_terminal_answer_reaches_the_user_verbatim(maker, public_chunk):
    async with maker() as s:
        deps = _deps(s)
        chunks, answer = await drain(
            "电子面单怎么设置",
            deps,
            scripted(call("answer_kb"), "以上，希望对你有帮助。"),
        )

    assert text_of(chunks) == KB_REPLY
    assert answer == KB_REPLY  # 调用方拿它去判 is_no_answer，必须是完整的
    assert deps.final_answer == KB_REPLY


async def test_agent_does_not_get_to_add_a_closing_remark(maker, public_chunk):
    async with maker() as s:
        chunks, _ = await drain(
            "电子面单怎么设置",
            _deps(s),
            scripted(call("answer_kb"), "以上，希望对你有帮助。"),
        )

    assert "希望对你有帮助" not in text_of(chunks)


async def test_a_usage_limit_blowout_still_answers(maker, public_chunk, monkeypatch):
    """⭐ 撞到工具额度上限，不能变成「一个字都没有」。

    2026-08-23 线上：组 8 一句话给全七个字段，模型连调 7 次 `save_requirement`
    冲破上限，这一轮抛异常、页面上只剩一个步骤徽章。**更糟的是那条会话从此
    废了**——之后每一句（连「你好」）都在同一个位置炸。

    额度上限的本意是「别让跑飞的模型烧光额度」，不是「这一轮不许有答案」。
    """
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "1")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS_QA", "1")

    _, body = public_chunk
    try:
        async with maker() as s:
            deps = _deps(s, plan_flow=True)
            # 两次工具调用，上限是 1——第二次必炸
            chunks, answer = await drain(
                body,
                deps,
                scripted(
                    call("save_requirement", field="platforms", value="淘宝"),
                    call("save_requirement", field="shop_count", value="5"),
                ),
            )
    finally:
        get_settings.cache_clear()

    assert answer, "撞上限之后一个字都没发出来"
    assert text_of(chunks), "协议流里没有任何正文"


def test_preamble_never_reaches_the_stream():
    """「我查一下」是在工具跑完**之前**产生的。边流边发就收不回来了。

    真模型会在同一次回复里既说话又调工具（`FunctionModel` 的流式模式做不到，
    所以这里直接验翻译层）：Agent 写的字只进 `drafted`，一个 SSE 片段都不发。
    """
    from pydantic_ai import PartDeltaEvent, PartStartEvent
    from pydantic_ai.messages import TextPart, TextPartDelta

    from copilot.agent.runner import _translate

    drafted: list[str] = []
    delta = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="我查一下。"))
    assert _translate(PartStartEvent(index=0, part=TextPart("好的，")), drafted) == []
    assert _translate(delta, drafted) == []
    assert "".join(drafted) == "好的，我查一下。"


def test_only_the_draft_after_the_last_tool_call_is_sent():
    """⭐ 2026-08-23 的 20 组人工验收：出方案那几轮，每条回答都是同一句话的
    两个版本首尾相接——

        好的，记下了。你们仓库是自营还是外包？有几个仓？
        好的，已记录。你们仓库是自营、云仓、委外，还是混合？有几个仓？

    调工具**前**写的那句是草稿，模型拿到工具结果后自己重写了一遍；
    发出去的只能是重写后的那一段。"""
    from pydantic_ai import FunctionToolCallEvent, PartDeltaEvent, PartStartEvent
    from pydantic_ai.messages import TextPart, TextPartDelta, ToolCallPart

    from copilot.agent.runner import _translate, latest_draft

    drafted: list[str] = []
    _translate(PartStartEvent(index=0, part=TextPart("好的，记下了。仓库自营还是外包？")), drafted)
    _translate(
        FunctionToolCallEvent(
            part=ToolCallPart(tool_name="save_requirement", args={}, tool_call_id="c1")
        ),
        drafted,
    )
    _translate(
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="好的，已记录。仓库是自营、云仓还是委外？")),
        drafted,
    )

    assert latest_draft(drafted) == "好的，已记录。仓库是自营、云仓还是委外？"


def test_a_draft_survives_when_the_model_says_nothing_after_the_tool():
    """退路：工具之后模型什么都没写，也不能把整条回答吞掉。"""
    from pydantic_ai import FunctionToolCallEvent, PartStartEvent
    from pydantic_ai.messages import TextPart, ToolCallPart

    from copilot.agent.runner import _translate, latest_draft

    drafted: list[str] = []
    _translate(PartStartEvent(index=0, part=TextPart("我查一下。")), drafted)
    _translate(
        FunctionToolCallEvent(part=ToolCallPart(tool_name="whoami", args={}, tool_call_id="c1")),
        drafted,
    )

    assert latest_draft(drafted) == "我查一下。"


# ---------- 3：没有终结答案时，Agent 自己的话要照常吐 ----------


async def test_agent_own_text_is_emitted_when_no_terminal_tool(maker):
    """追问 / 时间 / 闲聊全靠这条。丢错了的表现是「问它几点，它一个字都不回」。"""
    async with maker() as s:
        chunks, answer = await drain(
            "你好",
            _deps(s),
            scripted("你好，我是旺店通 ERP 的知识库助手。"),
        )

    assert text_of(chunks) == "你好，我是旺店通 ERP 的知识库助手。"
    assert answer == text_of(chunks)


async def test_text_start_and_end_wrap_every_segment(maker):
    """少一个 text-end，前端那条消息就永远停在「正在输入」。"""
    async with maker() as s:
        chunks, _ = await drain(
            "你好", _deps(s), scripted("你好。")
        )

    starts = [c for c in chunks if c["type"] == "text-start"]
    ends = [c for c in chunks if c["type"] == "text-end"]
    assert len(starts) == len(ends) == 1
    assert starts[0]["id"] == ends[0]["id"]
    assert all(c["id"] == starts[0]["id"] for c in chunks if c["type"] == "text-delta")


# ---------- 4：工具调用要出现在流里 ----------


async def test_tool_call_is_reported_with_a_human_label(maker, public_chunk):
    """前端拿 toolName 直接显示。发 `answer_kb` 的话用户看到的是内部标识。"""
    async with maker() as s:
        chunks, _ = await drain(
            "电子面单怎么设置",
            _deps(s),
            scripted(call("answer_kb"), "完毕"),
        )

    names = [c["toolName"] for c in chunks if c["type"] == "tool-input-start"]
    assert names == ["查知识库"]
    assert any(c["type"] == "tool-output-available" for c in chunks)


# ---------- 5：配图必须在正文之前 ----------


@pytest.fixture
async def chunk_with_image(maker):
    """一篇带配图的公共文档。正文里是 `[图:a3f9]` 这种标记，检索时才被编号。"""
    tag = uuid.uuid4().hex[:8]
    title = f"打印设置-{tag}"
    body = f"进入打印设置页 [图:a3f9]，勾选自动打印-{tag}"
    async with maker() as s:
        doc = Document(
            owner_id=None,
            source_type="yuque",
            title=title,
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=None,
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=title,
                images=[{"id": "a3f9", "url": "https://cdn.test/pic.png"}],
            )
        )
        await s.commit()
        doc_id = doc.id

    yield body

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


async def test_images_are_sent_before_the_text(maker, chunk_with_image):
    """前端要边流边把 [图1] 换成真图。对照表来晚了，用户看到的是一个裸标记。"""
    async with maker() as s:
        deps = _deps(s, llm=FakeLLM("按 [图1] 操作即可。"))
        chunks, _ = await drain(
            "打印设置在哪",
            deps,
            scripted(call("answer_kb"), "完毕"),
        )

    kinds = [c["type"] for c in chunks]
    assert "data-images" in kinds
    assert kinds.index("data-images") < kinds.index("text-delta")
    assert deps.images_sent is True  # 路由层据此不再发第二遍


# ---------- 6：一轮只允许一次终结答案 ----------


async def test_second_answer_kb_call_is_refused(maker, public_chunk):
    """两次调用会毁掉第一次的引用编号——那批 [1][2] 已经连着正文发出去了。"""
    from types import SimpleNamespace

    async with maker() as s:
        deps = _deps(s)
        first = await answer_kb(SimpleNamespace(deps=deps))
        assert "已经把答案直接给用户了" in first

        before = deps.final_answer
        second = await answer_kb(SimpleNamespace(deps=deps))

    assert "已经回答过" in second
    assert deps.final_answer == before  # 没有被第二次调用覆盖


# ---------- 7：限额按路径分 ----------


def test_usage_limits_are_tighter_for_plain_questions():
    """普通问答的正常形态是「决策 → answer_kb → 结束」。给 8 次等于允许它
    多烧 6 次才被拦住，而用户全程只看到一个转圈。"""
    from copilot.agent.agent import usage_limits

    qa = usage_limits()
    plan = usage_limits(plan_flow=True)
    assert qa.request_limit < plan.request_limit
    assert qa.tool_calls_limit < plan.tool_calls_limit


async def test_runner_uses_plan_limits_before_profile_is_filled(maker, monkeypatch):
    """首轮完整方案请求在任何 save_requirement 运行前就必须拿到方案限额。"""
    from copilot.agent import runner as runner_module

    seen: list[bool] = []
    original = runner_module.usage_limits

    def capture(*, plan_flow=False):
        seen.append(plan_flow)
        return original(plan_flow=plan_flow)

    monkeypatch.setattr(runner_module, "usage_limits", capture)
    async with maker() as s:
        await drain(
            "帮我出实施方案", _deps(s, plan_flow=True), scripted("请告诉我平台。")
        )

    assert seen == [True]


async def test_active_plan_retries_text_only_fake_recording(maker):
    """模型说“已记录”却没调工具时必须重试，否则刷新后字段会凭空消失。"""
    from copilot.agent.checklist import Requirement

    async with maker() as s:
        deps = _deps(
            s,
            plan_flow=True,
            profile=Requirement(platforms="淘宝、拼多多"),
        )
        _chunks, answer = await drain(
            "5 个店",
            deps,
            scripted(
                "好的，已记录。请问仓库模式？",
                call("save_requirement", field="shop_count", value="5 个店"),
                "好的，请问仓库模式？",
            ),
        )

    assert deps.profile.shop_count == "5 个店"
    assert "save_requirement" in deps.used_tools
    assert "仓库模式" in answer


def test_pure_continue_may_ask_the_next_plan_question_without_a_tool():
    """“继续”没有新字段，允许直接追问，避免把 G6 一起锁死。"""
    from copilot.agent.agent import plan_turn_requires_tool
    from copilot.agent.checklist import Requirement

    deps = object.__new__(AgentDeps)
    deps.plan_flow = True
    deps.profile = Requirement(platforms="淘宝")
    deps.question = "好，继续。"
    assert plan_turn_requires_tool(deps, tool_calls=0) is False


async def test_complete_profile_structurally_generates_plan(maker, monkeypatch):
    """最后一项保存后即使模型继续追问，也必须进入现有方案生成工具。"""
    from copilot.agent import tools as tools_module
    from copilot.agent.checklist import Checklist, Requirement

    async def fake_generate(ctx):
        ctx.deps.checklist = Checklist(title="测试方案", summary="完整", items=[])
        return "已生成《测试方案》。"

    monkeypatch.setattr(tools_module, "generate_plan", fake_generate)
    profile = Requirement(
        platforms="淘宝、拼多多",
        shop_count="5 个店",
        warehouse_mode="自营",
        warehouse_count="1 个仓",
        daily_orders="平峰 500 单，大促 2000 单",
        logistics="中通、顺丰",
    )
    async with maker() as s:
        deps = _deps(s, plan_flow=True, profile=profile)
        _chunks, answer = await drain(
            "没有特殊业务",
            deps,
            scripted(
                call("save_requirement", field="specials", value="无特殊业务"),
                "请再告诉我平台和店铺。",
            ),
        )

    assert answer == "已生成《测试方案》。"
    assert deps.checklist is not None
    assert "generate_plan" in deps.used_tools


async def test_export_intent_structurally_exports_existing_plan(maker, monkeypatch):
    """“导出来”不能再被模型带回需求收集，完整方案直接走现有导出工具。"""
    from copilot.agent import tools as tools_module
    from copilot.agent.checklist import Checklist, Requirement

    async def fake_export(ctx):
        ctx.deps.download_url = f"/api/conversations/{ctx.deps.conversation_id}/export"
        return "已导出 xlsx，页面上会出现下载按钮。"

    monkeypatch.setattr(tools_module, "export_excel", fake_export)
    profile = Requirement(
        platforms="淘宝、拼多多",
        shop_count="5 个店",
        warehouse_mode="自营",
        warehouse_count="1 个仓",
        daily_orders="平峰 500 单，大促 2000 单",
        logistics="中通、顺丰",
        specials="无特殊业务",
    )
    checklist = Checklist(title="测试方案", summary="完整", items=[])
    async with maker() as s:
        deps = _deps(s, plan_flow=True, profile=profile, checklist=checklist)
        _chunks, answer = await drain(
            "导出来",
            deps,
            scripted(
                call("save_requirement", field="specials", value="无特殊业务"),
                "还缺平台和店铺。",
            ),
        )

    assert answer == "已导出 xlsx，页面上会出现下载按钮。"
    assert deps.download_url is not None
    assert "export_excel" in deps.used_tools


# ---------- 8：每个注册的工具都要有中文标签 ----------


def test_every_registered_tool_has_a_label():
    """⭐ 加了工具忘了加标签，前端就把 `my_documents` 原样显示给用户。

    这条是补的——`whoami` / `my_documents` 上线时就漏了标签，靠人记不住。
    """
    from copilot.agent.agent import TOOLS
    from copilot.agent.runner import TOOL_LABELS

    missing = [t.__name__ for t in TOOLS if t.__name__ not in TOOL_LABELS]
    assert not missing, f"这些工具没有中文标签：{missing}"


# ---------- 9：越过工具直答的硬防线 ----------


def test_guard_catches_answers_that_should_have_been_retrieved():
    """带 [n] 标记、或够长且含界面路径特征的，都算越线。"""
    from copilot.agent.guard import looks_like_kb_answer

    assert looks_like_kb_answer("方式1：勾选残次品入库[4]")
    assert looks_like_kb_answer("按 [图2] 操作")
    assert looks_like_kb_answer(
        "进入【设置】-【仓库管理】页面，点击新增，勾选残次品入库，"
        "然后在弹出的输入框里填写单号并保存，这样就完成了整个配置流程"
    )


def test_guard_does_not_touch_follow_up_questions():
    """⚠️ 宁可漏判不可误伤：把一句正常追问替换成兜底话术，多轮收集会当场卡死。"""
    from copilot.agent.guard import looks_like_kb_answer

    assert not looks_like_kb_answer("您要对接哪些电商平台？（如淘宝、拼多多、抖音）")
    assert not looks_like_kb_answer("现在是 2026年08月19日 15:32，星期三（北京时间）")
    assert not looks_like_kb_answer("你好，我是旺店通旗舰版 ERP 的知识库助手。")
    assert not looks_like_kb_answer("")


async def test_bypassed_answer_is_retrieved_by_the_existing_kb_path(maker, public_chunk):
    """⭐ 没调终结工具、却写出一段像知识库答案的东西 —— 结构化回到直路。

    实测撞到过：追问「那不良品呢」时 Agent 拿上一轮留在历史里的答案编了一段，
    带着 [3][4] 标记，而页面上 0 条引用可点（见 guard.py 文件头）。
    """
    bogus = "残次品入库有两种方式：1. 进入【仓库管理】点击入库单 [3]；2. 勾选残次品 [4]。"
    async with maker() as s:
        deps = _deps(s)
        chunks, answer = await drain("那不良品呢", deps, scripted(bogus))

    assert text_of(chunks) == KB_REPLY
    assert answer == KB_REPLY
    assert deps.used_tools == {"answer_kb"}


async def test_no_tool_refusal_is_retried_through_answer_kb(maker, public_chunk):
    """线上追问曾直接回兜底且 tools=[]；这时应让现有 RAG 做最终判断。"""
    from copilot.qa import NO_ANSWER

    async with maker() as s:
        deps = _deps(s)
        chunks, answer = await drain("那个要先审核吗？", deps, scripted(NO_ANSWER))

    assert text_of(chunks) == KB_REPLY
    assert answer == KB_REPLY
    assert deps.used_tools == {"answer_kb"}


async def test_history_strips_citation_marks_and_truncates(maker):
    """历史是用来听懂这一句在问什么的，不是当材料用的。"""
    from copilot.agent.runner import HISTORY_ANSWER_LIMIT, to_message_history

    long_answer = "进入【仓库管理】[1]，点击提交 [图2]。" + "补" * 800
    msgs = to_message_history([("user", "退货入库怎么操作"), ("assistant", long_answer)])
    kept = msgs[1].parts[0].content
    assert "[1]" not in kept and "[图2]" not in kept, "编号在新一轮里无效，抄过去会指错来源"
    assert len(kept) == HISTORY_ANSWER_LIMIT
    assert msgs[0].parts[0].content == "退货入库怎么操作", "用户那半边原样保留"


async def test_truncated_history_never_invents_the_first_question(maker):
    """窗口第一条不等于会话第一条；问最早内容时必须明确说不可见。"""
    async with maker() as s:
        chunks, answer = await drain(
            "第一个问题我问的是什么？",
            _deps(s, history_truncated=True),
            scripted("你第一个问的是订单审核在哪里。"),
        )

    assert "无法确认" in text_of(chunks)
    assert answer == text_of(chunks)
    assert "订单审核" not in answer


async def test_truncated_history_asks_to_resolve_vague_reference(maker):
    """指代对象已被窗口裁掉时，不得拿「那个功能」随机检索一个答案。"""
    async with maker() as s:
        deps = _deps(s, history_truncated=True)
        chunks, answer = await drain(
            "那个功能在哪配置来着？",
            deps,
            scripted("可以在【设置】-【系统设置】中配置。"),
        )

    assert "无法确认“那个功能”" in text_of(chunks)
    assert "请直接说出功能名称" in answer
    assert deps.used_tools == set()


async def test_truncated_history_blocks_tool_calls_for_vague_reference(maker):
    """模型即使准备检索，历史边界也应在 Agent 启动前直接返回澄清。"""
    async with maker() as s:
        deps = _deps(s, history_truncated=True)
        chunks, answer = await drain(
            "那个功能在哪配置来着？",
            deps,
            scripted(call("answer_kb"), "随机命中的功能说明"),
        )

    assert "请直接说出功能名称" in answer
    assert text_of(chunks) == answer
    assert deps.used_tools == set()


async def test_guard_does_not_fire_when_a_tool_did_run(maker):
    """⚠️ 误伤检查：`generate_plan` 之后那段方案摘要带着界面路径，
    长得和越线的一模一样——但它有据，据在刚跑完的那个工具里。

    少了「这一轮一个工具都没调」这个前提，整条出方案流程会变成
    「知识库暂无此内容」。
    """
    summary = "已生成《实施配置方案》，共 15 项：[必做] 设置–基本设置–店铺 → 创建 5 个店铺并授权"
    async with maker() as s:
        chunks, answer = await drain(
            "帮我出一份实施方案", _deps(s), scripted(call("current_time"), summary)
        )

    assert text_of(chunks) == summary, "调过工具的那一轮不该被硬防线碰"
    assert answer == summary
