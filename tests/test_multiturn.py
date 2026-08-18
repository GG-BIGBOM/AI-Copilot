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
import time
import uuid
from collections.abc import Iterator

import pytest
from chat_helpers import ask, parts
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


class ScriptedLLM:
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


class SlowLLM:
    """一个字一个字慢慢吐，好让测试在中途把请求取消掉。"""

    def __init__(self, text: str, delay: float = 0.03) -> None:
        self.text = text
        self.delay = delay

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        def gen():
            for ch in self.text:
                time.sleep(self.delay)
                yield ch

        return gen()

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return "".join(self.stream(messages, temperature))

    def close(self) -> None:
        pass


async def _ask_then_cancel(api_client, question: str, conv_id: str, after: float) -> None:
    """发起提问，`after` 秒后取消——等价于用户点了「停止生成」。

    取消的是**等待响应的那个任务**，这样 CancelledError 会被送进
    `_chat_stream` 当前挂起的那个 yield，走的是和真实断连一模一样的路径。
    """
    task = asyncio.create_task(ask(api_client, question, conv_id=conv_id))
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

    await _ask_then_cancel(api_client, body, conv_id, after=0.55)

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
