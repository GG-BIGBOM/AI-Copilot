"""AI SDK **UI Message Stream Protocol** 编码器。

前端 `useChat` 认的就是这套 SSE 格式。协议本身不复杂，但错一个字段名前端
就只会安静地什么都不显示——**不报错，只是空白**。所以把它单独隔离在这个文件里，
配 `tests/test_stream_protocol.py` 逐个字段固化，跟语雀解析器一样的待遇。

一次回答的完整事件序列：

    data: {"type":"start","messageId":"..."}
    data: {"type":"start-step"}
    data: {"type":"text-start","id":"..."}
    data: {"type":"text-delta","id":"...","delta":"旺"}
    data: {"type":"text-delta","id":"...","delta":"店通"}
    data: {"type":"text-end","id":"..."}
    data: {"type":"data-citations","data":{"citations":[...]}}
    data: {"type":"finish-step"}
    data: {"type":"finish"}
    data: [DONE]

三个必须记住的点：

1. `x-vercel-ai-ui-message-stream: v1` 响应头**不能少**，AI SDK 靠它认协议版本。
2. `text-delta` 的字段叫 `delta`，不是 `text`、不是 `textDelta`。
   `id` 要和配套的 `text-start` / `text-end` 一致，否则前端拼不出这条消息。
3. 结尾的 `data: [DONE]` 是普通文本，不是 JSON。

参考：https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol
"""

from __future__ import annotations

import json
import uuid
from typing import Any

# SSE 响应头。缺 `x-vercel-ai-ui-message-stream` 前端不认；
# 缺 `X-Accel-Buffering` 的话 nginx 会把整条流缓冲起来，
# 用户要等生成完才一次性看到全文，"流式"就白做了（M5 的 nginx.conf 里
# 还要再配一次 proxy_buffering off，两处都得有）。
SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "X-Accel-Buffering": "no",
}

DONE = "data: [DONE]\n\n"


def encode(part: dict[str, Any]) -> str:
    """把一个协议片段编成一行 SSE。

    `ensure_ascii=False`：中文按 UTF-8 原样发，别转成 \\uXXXX 白白撑大三倍。
    """
    return f"data: {json.dumps(part, ensure_ascii=False, separators=(',', ':'))}\n\n"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---------- 消息骨架 ----------


def start(message_id: str) -> str:
    return encode({"type": "start", "messageId": message_id})


def start_step() -> str:
    return encode({"type": "start-step"})


def finish_step() -> str:
    return encode({"type": "finish-step"})


def finish() -> str:
    return encode({"type": "finish"})


# ---------- 正文 ----------


def text_start(text_id: str) -> str:
    return encode({"type": "text-start", "id": text_id})


def text_delta(text_id: str, delta: str) -> str:
    return encode({"type": "text-delta", "id": text_id, "delta": delta})


def text_end(text_id: str) -> str:
    return encode({"type": "text-end", "id": text_id})


# ---------- 推理草稿 ----------
#
# AI SDK 的 reasoning part。前端 `useChat` 会把它们收成 `type: "reasoning"` 的
# part，和正文分开。**分开这件事是刚需**：草稿里会出现「材料里没提到…」
# 「也许是设置-快递管理？」这类自我推翻的话，混进正文就是一条会引人误会的答案。


def reasoning_start(part_id: str) -> str:
    return encode({"type": "reasoning-start", "id": part_id})


def reasoning_delta(part_id: str, delta: str) -> str:
    return encode({"type": "reasoning-delta", "id": part_id, "delta": delta})


def reasoning_end(part_id: str) -> str:
    return encode({"type": "reasoning-end", "id": part_id})


# ---------- 自定义数据 ----------


def data_part(name: str, data: Any) -> str:
    """自定义数据片段。前端按 `data-<name>` 匹配，如 `data-citations`。"""
    return encode({"type": f"data-{name}", "data": data})


# ---------- 工具调用（M7）----------
#
# 字段名是从 `frontend/node_modules/ai/dist/index.d.ts` 的 UIMessageChunk 联合类型里
# **逐个核对**出来的，不是照记忆写的：
#     tool-input-start      toolCallId, toolName
#     tool-input-available  toolCallId, toolName, input
#     tool-output-available toolCallId, output
#     tool-output-error     toolCallId, errorText
# 错一个字段前端不会报错，只是那一段工具调用**安静地不显示**——
# 和文件头说的同一类故障。


def tool_input_start(call_id: str, tool_name: str) -> str:
    """工具开始被调用。前端据此先渲染一个「正在检索…」的占位。"""
    return encode({"type": "tool-input-start", "toolCallId": call_id, "toolName": tool_name})


def tool_input_available(call_id: str, tool_name: str, args: Any) -> str:
    """参数齐了。`input` 是工具的入参对象。"""
    return encode(
        {
            "type": "tool-input-available",
            "toolCallId": call_id,
            "toolName": tool_name,
            "input": args,
        }
    )


def tool_output_available(call_id: str, output: Any) -> str:
    return encode({"type": "tool-output-available", "toolCallId": call_id, "output": output})


def tool_output_error(call_id: str, message: str) -> str:
    """工具失败。**用它而不是 `error`**：`error` 会让整轮对话变成错误态，
    而单个工具失败时 Agent 还能换个方式继续——这是 M7「工具失败恢复」的前提。"""
    return encode({"type": "tool-output-error", "toolCallId": call_id, "errorText": message})


def error(message: str) -> str:
    """错误片段。前端会把 errorText 渲染出来，所以这里只放能给用户看的话，
    堆栈和内部细节留在服务端日志里。"""
    return encode({"type": "error", "errorText": message})
