"""多轮上下文、招呼语、以及「停止生成」之后那半截答案的归宿。

这三件事都是线上真实反馈出来的：
  - 发一句「你好」，回的是「知识库暂无此内容」
  - 问完「退货入库怎么操作」再追一句「那不良品呢」，检索拿这五个字去搜，必然打偏
  - 点了停止，刷新页面发现只剩一个提问，答案没了

和 test_api_chat.py 共用那套假 provider，一样不打外部 API。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import uuid
from collections.abc import Iterator

import pytest
from chat_helpers import FakeLLM, PartsFromStream, ask, parts
from sqlalchemy import select

from copilot.db.models import Message
from copilot.qa import small_talk_reply

# ---------- 招呼语（纯函数，不用起服务）----------


@pytest.mark.parametrize(
    "text",
    ["你好", "你好！", "您好~", "在吗？", "hi", "HELLO", "  早上好  ", "谢谢", "再见"],
)
def test_small_talk_hit(text):
    assert small_talk_reply(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "你好，电子面单怎么设置",  # 招呼 + 真问题，必须走检索
        "在吗 退货入库流程",
        "谢谢你之前说的那个字段叫什么",
        "怎么用电子面单",  # 「怎么用」是能力词，但带了宾语就是真问题
        "帮助中心在哪里配置",
        "",
    ],
)
def test_small_talk_miss(text):
    """⚠️ 这组比上一组重要：误伤一个真问题，等于凭空编一段 ERP 回答。"""
    assert small_talk_reply(text) is None


async def test_greeting_answers_without_retrieval(
    api_client, logged_in, public_chunk, fake_providers
):
    """「你好」要有像样的回复，而且**一次模型都不能调**。"""
    r = await ask(api_client, "你好")
    assert r.status_code == 200

    text = "".join(p["delta"] for p in parts(r.text) if p["type"] == "text-delta")
    assert "旺店通" in text
    assert "知识库暂无此内容" not in text

    assert fake_providers.calls == [], "招呼语不该调模型——那是防幻觉墙上的洞"
    assert not [p for p in parts(r.text) if p["type"] == "data-citations"], "招呼语不该挂来源"


# ---------- 多轮 ----------


class ScriptedLLM(PartsFromStream):
    """按调用顺序返回不同结果，用来分开看「改写」和「作答」两次调用。"""

    def __init__(self, rewritten: str, answer: str) -> None:
        self.rewritten = rewritten
        self.answer = answer
        self.calls: list[list[dict]] = []

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        self.calls.append(messages)
        # 改写走的是 complete()，而 complete() 内部就是 stream()。
        # 靠 system 提示词区分这一次是哪种调用
        if messages and "改写成一个不依赖上文" in messages[0].get("content", ""):
            return iter([self.rewritten])
        return iter(list(self.answer))

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        # 和真的 ChatLLM 一样，complete 就是把 stream 拼起来
        return "".join(self.stream(messages, temperature))

    def close(self) -> None:
        pass


async def test_followup_is_rewritten_before_retrieval(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """第二轮：检索用的必须是补全后的问题，送给模型的问题仍是用户原话。"""
    from copilot.api import providers

    title, body = public_chunk
    conv_id = str(uuid.uuid4())

    # 第一轮：正常问，落一条问答进库
    r1 = await ask(api_client, body, conv_id=conv_id)
    assert r1.status_code == 200

    llm = ScriptedLLM(rewritten=f"{body} 的不良品部分", answer="不良品要单独指定仓位[1]。")
    monkeypatch.setattr(providers, "get_llm", lambda: llm)

    r2 = await ask(api_client, "那不良品呢？", conv_id=conv_id)
    assert r2.status_code == 200

    assert len(llm.calls) == 2, "应该是两次调用：先改写，再作答"

    rewrite_call, answer_call = llm.calls
    assert "改写成一个不依赖上文" in rewrite_call[0]["content"]
    # 改写的输入里要能看见上一轮，否则它没法知道「那」指什么
    assert body[:12] in rewrite_call[1]["content"]

    # 作答那次：历史进了对话轮次，最后一条仍然是用户的原话
    assert answer_call[-1]["content"].rstrip().endswith("那不良品呢？")
    assert any(m["role"] == "assistant" for m in answer_call[1:-1]), "历史没带上"


async def test_first_turn_does_not_rewrite(api_client, logged_in, public_chunk, fake_providers):
    """第一轮没有历史，不该白花一次改写的钱。"""
    title, body = public_chunk
    r = await ask(api_client, body)
    assert r.status_code == 200
    assert len(fake_providers.calls) == 1, "第一轮只该调一次模型"


# ---------- 停止生成 ----------


FULL_ANSWER = "第一步先进入设置，第二步绑定账号，第三步打印面单"


class SlowLLM(PartsFromStream):
    """一个字一个字慢慢吐，好让测试在中途把请求取消掉。

    ⭐ **`emitted_enough` 是为了不掐秒表。** 「睡 0.55 秒再取消」在本机单跑
    没问题，全量跑起来（几十个用例抢 CPU）就会偶发地落在"一个字都还没吐"
    或"已经吐完了"上——测试于是随机变红，而红的原因和被测代码无关。
    改成「吐够 N 个字就置位」，取消点就由**被测系统的进度**决定，不由调度决定。
    """

    def __init__(self, text: str, delay: float = 0.03, emit_before_cancel: int = 5) -> None:
        self.text = text
        self.delay = delay
        self.emit_before_cancel = emit_before_cancel
        self.emitted_enough = threading.Event()

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        def gen():
            for i, ch in enumerate(self.text, 1):
                time.sleep(self.delay)
                if i >= self.emit_before_cancel:
                    self.emitted_enough.set()
                yield ch

        return gen()

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return "".join(self.stream(messages, temperature))

    def close(self) -> None:
        pass


async def _ask_then_cancel(
    api_client,
    question: str,
    conv_id: str,
    after: float = 0.0,
    when: threading.Event | None = None,
) -> None:
    """发起提问，然后取消——等价于用户点了「停止生成」。

    取消的是**等待响应的那个任务**，这样 CancelledError 会被送进
    `_chat_stream` 当前挂起的那个 yield，走的是和真实断连一模一样的路径。

    Args:
        when: 等这个事件置位再取消（`SlowLLM.emitted_enough`）。**优先用它**，
            它让取消点由被测系统的进度决定，而不是由调度器决定。
        after: 没有 `when` 时，睡这么多秒再取消。只有「一个字都别吐出来」
            那种场景才该用它——那种场景本来就没有进度可等。
    """
    task = asyncio.create_task(ask(api_client, question, conv_id=conv_id))
    if when is not None:
        await asyncio.to_thread(when.wait, 10)
        # 让刚吐出来的那几个字走完 SSE、落到库里。取消得太急的话，
        # 断言的就不是"半截答案存下来了"而是"竞态谁先跑"
        await asyncio.sleep(0.05)
    else:
        await asyncio.sleep(after)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _messages_of(maker, conv_id: str) -> list[Message]:
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(Message)
                    .where(Message.conversation_id == uuid.UUID(conv_id))
                    .order_by(Message.created_at, Message.id)
                )
            ).scalars()
        )


async def test_interrupted_answer_is_persisted_and_marked(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """中断的半截答案要落库，而且要标出来它没写完。"""
    from copilot.api import providers
    from copilot.api.routes import chat as chat_module

    title, body = public_chunk
    conv_id = str(uuid.uuid4())
    # 把落库间隔压到 0：每吐一个字就写一次，测试就不用去掐 FLUSH_SECONDS 的表了
    monkeypatch.setattr(chat_module, "FLUSH_SECONDS", 0.0)
    slow = SlowLLM(FULL_ANSWER, delay=0.1)
    monkeypatch.setattr(providers, "get_llm", lambda: slow)

    await _ask_then_cancel(api_client, body, conv_id, when=slow.emitted_enough)

    rows = await _messages_of(maker, conv_id)
    assert [m.role for m in rows] == ["user", "assistant"], "被中断也要留下这轮问答"

    stored = rows[1].content.removesuffix(chat_module.INTERRUPTED_MARK)
    # 断言的是不变量，不是「刚好存了几个字」——取消落在哪个字上由调度决定，
    # 掐着秒表写断言的测试迟早会变成随机失败
    assert stored, "半截答案的内容要真的存下来"
    assert FULL_ANSWER.startswith(stored), "存下来的必须是已经吐出去的那部分的前缀"
    assert stored != FULL_ANSWER, "这一轮应该是被截断的"
    assert chat_module.INTERRUPTED_MARK in rows[1].content, "没写完的必须标出来"


async def test_empty_interruption_leaves_no_message(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """一个字都没吐出来就被停掉，别留一条空的助手消息。"""
    from copilot.api import providers

    title, body = public_chunk
    monkeypatch.setattr(providers, "get_llm", lambda: SlowLLM("很慢的回答", delay=5.0))

    conv_id = str(uuid.uuid4())
    await _ask_then_cancel(api_client, body, conv_id, after=0.2)

    # 只断言真正要守的那条不变量：不留空的助手消息。
    # 不去断言用户那条一定在——取消恰好落在 commit 上时它也可能没写成，
    # 那是客户端断连的固有结果，不是这里要测的东西
    rows = await _messages_of(maker, conv_id)
    assert all(m.role != "assistant" for m in rows), "一个字都没吐出来，不该留一条空的助手消息"


# ---------- 回答档位 ----------


def test_two_modes_share_the_same_hard_rules():
    """⚠️ 详解档是「同一份事实说得更透」，不是「可以多说材料里没有的」。

    防幻觉那段铁律两档必须一字不差——做成两份，迟早有一份会先松掉。
    """
    from copilot.qa import _TEMPLATE, system_prompt_for

    rules = _TEMPLATE.split("写法要求：")[0]
    for mode in ("fast", "deep"):
        assert rules in system_prompt_for(mode)
        assert "不得用你自己的常识补全或推测" in system_prompt_for(mode)
        assert "知识库暂无此内容" in system_prompt_for(mode)


def test_unknown_mode_falls_back_to_fast():
    """认不出来的档位退回简答，别 500。"""
    from copilot.qa import system_prompt_for

    assert system_prompt_for("没听说过的档位") == system_prompt_for("fast")


async def test_mode_selects_prompt_and_model(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """前端传 deep 时：换模型、也换写法。"""
    from copilot.api import providers

    title, body = public_chunk
    deep = FakeLLM("详细的答案[1]。")
    monkeypatch.setattr(providers, "get_deep_llm", lambda: deep)

    payload = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
        "mode": "deep",
    }
    r = await api_client.post("/api/chat", json=payload)
    assert r.status_code == 200

    assert deep.calls, "选了详解档就该用详解档的模型"
    assert not fake_providers.calls, "不该再打到简答档的模型上"
    system = deep.calls[0][0]["content"]
    assert "每一步写清楚" in system, "system prompt 要换成详解档那套写法"


async def test_missing_mode_defaults_to_fast(api_client, logged_in, public_chunk, fake_providers):
    """老前端不带 mode 字段，不能 422，要按简答档走。"""
    title, body = public_chunk
    r = await ask(api_client, body)
    assert r.status_code == 200
    assert fake_providers.calls


async def test_deep_falls_back_when_not_configured(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """详解档没配 key 时静默退回简答档。

    对用户来说，点一下选择器收到 500 不是「少了个高级功能」，是「这产品坏了」。
    """
    from copilot.api import providers

    def boom():
        raise RuntimeError("缺少 LLM_DEEP_API_KEY")

    monkeypatch.setattr(providers, "get_deep_llm", boom)

    title, body = public_chunk
    payload = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
        "mode": "deep",
    }
    r = await api_client.post("/api/chat", json=payload)
    assert r.status_code == 200
    assert fake_providers.calls, "应该退回简答档，而不是把异常抛给用户"


# ---------- 路由分类（eval/routing.py 依赖它）----------


def test_small_talk_kind_classifies():
    """`small_talk_kind` 是路由评测的判定依据，分类不能串。"""
    from copilot.qa import small_talk_kind

    assert small_talk_kind("你好") == "greeting"
    assert small_talk_kind("你能做什么") == "capability"
    assert small_talk_kind("谢谢") == "thanks"
    assert small_talk_kind("拜拜") == "bye"
    assert small_talk_kind("京东电子面单怎么设置") is None


def test_small_talk_kind_and_reply_agree():
    """两个函数必须对同一句话给出一致的判断。

    `small_talk_reply` 现在是走 `small_talk_kind` 拼出来的，这条测的是
    以后有人把其中一个改成"优化版"时会当场红。
    """
    from copilot.qa import small_talk_kind, small_talk_reply

    for text in ["你好", "你能做什么", "谢谢", "再见", "退货入库怎么操作", ""]:
        assert (small_talk_kind(text) is None) == (small_talk_reply(text) is None)


# ---------- M10 P2：Agent 路径要有和直路一模一样的保障 ----------
#
# 「每加一个能力都只加在一条路上」是双路架构在收的税。上面那两件事
# （招呼语、边流边落库）原来都只有直路有，Agent 路径点停止就只剩一个提问。
# 这一节和上面的直路版本是对照着写的，两边不一致就该有一条红的。


def _tool(name: str) -> tuple[str, str]:
    """脚本里的一次工具调用。**必须和纯文本区分开**——工具名本身也是 str，
    直接传字符串的话它会被当成「Agent 说了 answer_kb 这四个字」。"""
    return ("tool", name)


def _scripted_agent_model(*turns):
    """把模型行为写死：要么吐一段文本（str），要么调一次工具（`_tool(...)`）。"""
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    it = iter(turns)

    async def script(messages, info):
        turn = next(it)
        if isinstance(turn, tuple):
            name = turn[1]
            yield {0: DeltaToolCall(name=name, json_args="{}", tool_call_id=f"c_{name}")}
        else:
            for ch in turn:
                yield ch

    return FunctionModel(stream_function=script)


async def test_agent_path_also_persists_interrupted_answer(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """⭐ 和 `test_interrupted_answer_is_persisted_and_marked` 是同一条不变量。

    Agent 路径原来没有边流边落库——用户点停止，刷新页面只剩一个提问。
    终结工具的正文是流式直通的，所以这条路上「已经吐出去的」同样必须落库。
    """
    from copilot.agent import runner as runner_module
    from copilot.agent.agent import build_agent
    from copilot.api import providers
    from copilot.api.routes import chat as chat_module

    _title, body = public_chunk
    monkeypatch.setattr(chat_module, "FLUSH_SECONDS", 0.0)
    slow = SlowLLM(FULL_ANSWER, delay=0.1)
    monkeypatch.setattr(providers, "get_llm_for", lambda mode: slow)

    async def always_agent(*_a, **_kw):
        return True

    monkeypatch.setattr(chat_module, "_use_agent", always_agent)
    model = _scripted_agent_model(_tool("answer_kb"), "完毕")
    monkeypatch.setattr(runner_module, "build_agent", lambda m=None: build_agent(model))

    conv_id = str(uuid.uuid4())
    await _ask_then_cancel(api_client, body, conv_id, when=slow.emitted_enough)

    rows = await _messages_of(maker, conv_id)
    assert [m.role for m in rows] == ["user", "assistant"], "被中断也要留下这轮问答"

    stored = rows[1].content.removesuffix(chat_module.INTERRUPTED_MARK)
    assert stored, "半截答案的内容要真的存下来"
    assert FULL_ANSWER.startswith(stored), "存下来的必须是已经吐出去的那部分的前缀"
    assert stored != FULL_ANSWER, "这一轮应该是被截断的"
    assert chat_module.INTERRUPTED_MARK in rows[1].content, "没写完的必须标出来"


async def test_small_talk_does_not_hijack_an_agent_conversation(
    api_client, logged_in, fake_providers, monkeypatch, maker
):
    """⭐ 寒暄短路的**顺序**：已经在多轮流程里的会话不许被它截走。

    Agent 问完「要对接哪些平台？」，用户回一句「好的」——那两个字在寒暄表里。
    短路掉就变成「不客气。还有别的问题随时问。」，收集需求的流程当场断掉。
    """
    from copilot.api.routes import chat as chat_module
    from copilot.db.models import Conversation

    conv_id = uuid.uuid4()
    async with maker() as s:
        # `profile is not None` = 这条会话正在走 Agent（第一轮往往是空字典）
        s.add(Conversation(id=conv_id, user_id=logged_in, title="出方案", profile={}))
        await s.commit()

    async def stub(_user_id, _question, _client_id, _mode=None):
        yield "data: {\"type\":\"text-delta\",\"id\":\"t\",\"delta\":\"AGENT\"}\n\n"

    monkeypatch.setattr(chat_module, "_agent_stream", stub)

    r = await ask(api_client, "好的", conv_id=str(conv_id))
    assert r.status_code == 200
    assert "AGENT" in r.text, "这一轮应该留在 Agent 里"
    assert "不客气" not in r.text, "寒暄短路把多轮流程截走了"


async def test_small_talk_still_short_circuits_a_fresh_conversation(
    api_client, logged_in, fake_providers
):
    """反过来也要成立：普通会话里的「谢谢」仍然是 0 成本固定回复。"""
    r = await ask(api_client, "谢谢")
    assert r.status_code == 200
    body = "".join(p["delta"] for p in parts(r.text) if p["type"] == "text-delta")
    assert "不客气" in body
    assert fake_providers.calls == [], "固定回复不该调模型"


# ---------- 首字到了才开正文 ----------


class SilentLLM(PartsFromStream):
    """一个字都不吐的模型。用来测「没有内容就不该开正文片段」。"""

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        return iter(())

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return ""

    def close(self) -> None:
        pass


async def test_no_text_part_when_model_says_nothing(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """⭐ `text-start` 要等**第一个字真的到了**才发。

    原来是在调模型之前就发，于是 AI SDK 立刻从 `submitted` 切到 `streaming`，
    前端那句「正在分析」消失、换成一条空答案加一个闪烁光标。
    普通模型只闪几百毫秒看不出来；详解档走的 kimi-k2.6 是推理模型，
    它先吐三千字草稿，**正文首字要 60 秒**——线上表现就是
    「选了详解，没有回答内容」。
    """
    from copilot.api import providers

    title, body = public_chunk
    monkeypatch.setattr(providers, "get_llm", lambda: SilentLLM())

    r = await ask(api_client, body)
    assert r.status_code == 200

    types = [p["type"] for p in parts(r.text)]
    assert "text-start" not in types, "一个字都没有，不该开一个空的正文片段"
    assert "text-delta" not in types
    assert types[-2:] == ["finish-step", "finish"], "协议骨架仍然要完整收尾"


async def test_text_part_still_opens_when_there_is_content(
    api_client, logged_in, public_chunk, fake_providers
):
    """有内容时协议形状不变——晚发不等于不发。"""
    title, body = public_chunk
    r = await ask(api_client, body)
    types = [p["type"] for p in parts(r.text)]
    assert "text-start" in types and "text-end" in types
    # text-start 必须排在第一个 text-delta 前面
    assert types.index("text-start") < types.index("text-delta")


# ---------- 详解档：推理草稿 ----------


class ThinkingLLM:
    """推理模型：先吐几句草稿，再吐正文。

    对应线上实测的 kimi-k2.6——第一个草稿字 1 秒就到，
    **第一个正文字要 8~60 秒**。中间那段就是用户抱怨的「详解没有回答内容」。
    """

    def __init__(self, draft: str, answer: str) -> None:
        self.draft = draft
        self.answer = answer

    def stream_parts(self, messages: list[dict], temperature: float = 0.1):
        for ch in self.draft:
            yield "reasoning", ch
        for ch in self.answer:
            yield "content", ch

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        return (t for k, t in self.stream_parts(messages, temperature) if k == "content")

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return "".join(self.stream(messages, temperature))

    def close(self) -> None:
        pass


async def test_reasoning_is_streamed_separately_from_the_answer(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch
):
    """⭐ 草稿要**边出边发**，而且和正文分开。

    这是「详解太慢」的正解：模型其实 1 秒就开口了，只是说的是草稿。
    不发草稿，前端那几十秒就是一片空白；混进正文，用户会读到
    「材料里没提到…」这种自我推翻的话，比空白更糟。
    """
    from copilot.api import providers

    title, body = public_chunk
    llm = ThinkingLLM(draft="先看材料里有没有提到绑定网点", answer="第一步进入设置[1]。")
    monkeypatch.setattr(providers, "get_llm", lambda: llm)

    r = await ask(api_client, body)
    assert r.status_code == 200
    chunks = parts(r.text)
    kinds = [c["type"] for c in chunks]

    assert "reasoning-start" in kinds and "reasoning-end" in kinds
    drafted = "".join(c["delta"] for c in chunks if c["type"] == "reasoning-delta")
    answered = "".join(c["delta"] for c in chunks if c["type"] == "text-delta")
    assert drafted == "先看材料里有没有提到绑定网点"
    assert answered == "第一步进入设置[1]。"

    # 草稿在正文之前，而且**先收尾**——不然前端两个块会叠在一起
    assert kinds.index("reasoning-start") < kinds.index("text-start")
    assert kinds.index("reasoning-end") < kinds.index("text-start")


async def test_draft_never_becomes_the_stored_answer(
    api_client, logged_in, public_chunk, fake_providers, monkeypatch, maker
):
    """⚠️ 草稿**不是答案**：不落库，也不参与「说了不知道就别挂来源」的判定。

    落库的话，用户翻历史记录看到的是一段自言自语；参与判定的话，
    草稿里一句「材料里好像没有」就能把整条回答的来源全撤掉。
    """
    from copilot.api import providers

    title, body = public_chunk
    conv_id = str(uuid.uuid4())
    llm = ThinkingLLM(draft="知识库暂无此内容？再找找", answer="第一步进入设置[1]。")
    monkeypatch.setattr(providers, "get_llm", lambda: llm)

    r = await ask(api_client, body, conv_id=conv_id)
    assert r.status_code == 200
    # 草稿里带着那句"暂无此内容"，但正文有实质回答——来源该照挂
    assert [c for c in parts(r.text) if c["type"] == "data-citations"], "来源被草稿误伤了"

    rows = await _messages_of(maker, conv_id)
    assert rows[1].content == "第一步进入设置[1]。", "草稿混进了落库的答案"
