"""`copilot ask` 这条命令行问答路径。

⭐ 它是**运维当场验一句**用的：部署完在服务器上敲一句，看线上这套配置
（真语料 + 真模型 + 真检索）到底怎么答。网页那条路走 `routes/chat.py`，
这条路自己单独一份收尾逻辑——于是它坏了半个月没人发现：

    2026-08-20 详解档加了「模型草稿」那一路，`ask_stream` 从吐字符串改成吐
    `(kind, text)`；`routes/chat.py` 跟着改了，CLI 这份忘了。
    表现是终端上打出 `('content', '知识')('content', '库')…`，
    最后 `"".join(buf)` 抛 `TypeError: expected str instance, tuple found`。
    2026-08-23 在生产上验收部署时才撞出来。

所以这条路也要有测试：不联网、不打真模型，只验「正文原样出来、草稿不打、
说了暂无此内容就不挂来源」。
"""

from __future__ import annotations

import pytest
from chat_helpers import FakeEmbedder, TopOneReranker

from copilot.qa import NO_ANSWER


class PartsLLM:
    """按 `(kind, text)` 吐的假模型——和真 `ChatLLM.stream_parts` 同形状。"""

    def __init__(self, parts: list[tuple[str, str]]) -> None:
        self.parts = parts

    def stream_parts(self, messages: list[dict], temperature: float = 0.1):
        return iter(self.parts)

    def close(self) -> None:
        pass


@pytest.fixture
def wire_cli(monkeypatch, maker):
    """把 `_ask` 里那几个函数内 import 的东西全换成测试替身。"""

    def _wire(parts: list[tuple[str, str]]):
        from copilot.db import session as session_module
        from copilot.providers import llm as llm_module
        from copilot.providers import siliconflow as sf

        monkeypatch.setattr(session_module, "SessionLocal", maker)
        monkeypatch.setattr(sf, "SiliconFlowEmbedder", FakeEmbedder)
        monkeypatch.setattr(sf, "SiliconFlowReranker", TopOneReranker)
        monkeypatch.setattr(llm_module, "ChatLLM", lambda *a, **kw: PartsLLM(parts))

    return _wire


async def test_ask_prints_the_answer_and_hides_the_draft(
    capsys, wire_cli, public_chunk, maker
):
    """草稿不打到终端；正文一个字不少。"""
    from copilot.cli import _ask

    _, body = public_chunk
    wire_cli([("reasoning", "先想一想……"), ("content", "先绑定物流账号[1]，"), ("content", "再打印面单。")])

    await _ask(body, show_chunks=False)

    out = capsys.readouterr().out
    assert "先绑定物流账号[1]，再打印面单。" in out
    assert "先想一想" not in out, "模型草稿不该打到命令行上"
    assert "('content'" not in out, "元组被原样打出来了（2026-08-23 线上撞到的那个 bug）"
    assert "来源：" in out


async def test_ask_drops_citations_when_the_answer_says_it_does_not_know(
    capsys, wire_cli, public_chunk, maker
):
    """⭐ 说了「暂无此内容」就不能挂来源——命令行和网页是同一条规矩。"""
    from copilot.cli import _ask

    _, body = public_chunk
    wire_cli([("content", NO_ANSWER)])

    await _ask(body, show_chunks=False)

    out = capsys.readouterr().out
    assert NO_ANSWER in out
    assert "来源：" not in out
