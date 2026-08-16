"""AI SDK UI Message Stream Protocol 的格式固化。

字段名写错一个，前端不会报错，只会**安静地什么都不显示**——
所以这些断言看着像在测试常量，实际是在防那种最难查的故障。
和语雀解析器的固化样本测试是同一个道理。

规范：https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol
"""

from __future__ import annotations

import json

from copilot.api import stream


def parse(line: str) -> dict:
    """把一行 SSE 还原成 dict，顺带校验 `data: ` 前缀和空行结尾。"""
    assert line.startswith("data: "), f"SSE 每行必须以 'data: ' 开头：{line!r}"
    assert line.endswith("\n\n"), f"SSE 片段必须以空行结束：{line!r}"
    return json.loads(line[6:-2])


# ---------- 响应头 ----------


def test_headers_declare_protocol_version():
    """AI SDK 靠这个头认协议。少了它 useChat 收到的就是一坨没人管的文本。"""
    assert stream.SSE_HEADERS["x-vercel-ai-ui-message-stream"] == "v1"


def test_headers_disable_buffering():
    """nginx 默认会缓冲上游响应，把流式攒成一次性返回。
    这个头 + M5 nginx.conf 里的 proxy_buffering off，两处都得有。"""
    assert stream.SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert "no-transform" in stream.SSE_HEADERS["Cache-Control"]


def test_content_type_is_event_stream():
    assert stream.SSE_HEADERS["Content-Type"].startswith("text/event-stream")


# ---------- 各片段的字段名 ----------


def test_start_carries_message_id():
    assert parse(stream.start("msg_1")) == {"type": "start", "messageId": "msg_1"}


def test_step_markers():
    assert parse(stream.start_step()) == {"type": "start-step"}
    assert parse(stream.finish_step()) == {"type": "finish-step"}
    assert parse(stream.finish()) == {"type": "finish"}


def test_text_delta_field_is_named_delta():
    """⚠️ 是 `delta`，不是 `text`、不是 `textDelta`。写错了前端一个字都不显示。"""
    part = parse(stream.text_delta("txt_1", "旺店通"))
    assert part == {"type": "text-delta", "id": "txt_1", "delta": "旺店通"}


def test_text_parts_share_one_id():
    """start / delta / end 的 id 必须一致，前端才拼得出这条消息。"""
    tid = stream.new_id("txt")
    ids = {parse(p)["id"] for p in (stream.text_start(tid), stream.text_delta(tid, "x"),
                                    stream.text_end(tid))}
    assert ids == {tid}


def test_data_part_name_is_prefixed():
    """自定义数据片段的 type 是 `data-<name>`，前端按这个名字匹配。"""
    part = parse(stream.data_part("citations", {"citations": []}))
    assert part["type"] == "data-citations"
    assert part["data"] == {"citations": []}


def test_error_field_is_error_text():
    assert parse(stream.error("炸了")) == {"type": "error", "errorText": "炸了"}


# ---------- 编码细节 ----------


def test_chinese_is_not_escaped():
    """中文按 UTF-8 原样发。转成 \\uXXXX 能用，但体积白涨三倍。"""
    line = stream.text_delta("t", "电子面单")
    assert "电子面单" in line
    assert "\\u" not in line


def test_newlines_do_not_break_the_frame():
    """正文里的换行必须被 JSON 转义。漏了的话一个 delta 会被 SSE 切成两帧，
    整条流从此错位。"""
    line = stream.text_delta("t", "第一行\n第二行")
    assert line.count("\n\n") == 1, "正文里的换行没被转义，SSE 分帧被撑破了"
    assert parse(line)["delta"] == "第一行\n第二行"


def test_done_sentinel_is_plain_text():
    """结尾是字面量 `[DONE]`，不是 JSON。"""
    assert stream.DONE == "data: [DONE]\n\n"


def test_new_id_is_unique():
    assert len({stream.new_id("msg") for _ in range(100)}) == 100
