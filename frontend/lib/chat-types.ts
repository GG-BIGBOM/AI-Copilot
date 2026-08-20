import type { UIMessage } from "ai";

import type { Citation } from "@/lib/api";

/**
 * 后端自定义的两种数据片段。名字要和 FastAPI 那边 `stream.data_part(name, ...)`
 * 传的 name 一字不差——协议上是 `data-conversation` / `data-citations`。
 *
 * 声明成类型之后，`part.type === "data-citations"` 能被 TS 收窄，
 * `part.data.citations` 直接有类型，不用到处 as。
 */
export type CopilotDataParts = {
  conversation: { id: string; title: string };
  citations: { citations: Citation[] };
  images: { images: AnswerImage[] };
  /** M7：Agent 导出的 xlsx。url 是根相对路径，用时拼 API_BASE */
  download: { url: string; name: string };
};

/** 答案正文里 `[图1]` 的编号 → 图片地址。地址是根相对路径，用时拼 API_BASE。 */
export type AnswerImage = { n: number; url: string };

export type CopilotUIMessage = UIMessage<unknown, CopilotDataParts>;

/** 把一条消息里所有文本片段拼起来。 */
export function messageText(message: CopilotUIMessage): string {
  return message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}

/**
 * 取这条消息的推理草稿（详解档才有）。
 *
 * ⚠️ **草稿不是答案。** 它是详解档那个推理模型在正式作答前的自言自语，
 * 里面尽是「材料里好像没提到…」这类会被自己推翻的话。所以它单独渲染、
 * 默认折叠，也**不**参与复制、订正、判「有没有答案」。
 *
 * 它存在的唯一理由是等待：实测第一个草稿字 1 秒就到，第一个正文字要 8~60 秒。
 * 没有它，那几十秒前端一个字都没有，用户以为「详解不回答」。
 */
export function messageReasoning(message: CopilotUIMessage): string {
  return message.parts.map((p) => (p.type === "reasoning" ? p.text : "")).join("");
}

/**
 * 取这条消息带的引用。
 *
 * 后端保证：模型回答「知识库暂无此内容」时**根本不会发**这个片段，
 * 所以前端不需要再判一次——那条防幻觉规则收在服务端一处就够了。
 */
export function messageCitations(message: CopilotUIMessage): Citation[] {
  for (const part of message.parts) {
    if (part.type === "data-citations") return part.data.citations;
  }
  return [];
}

/**
 * 取这条消息的配图对照表。
 *
 * 和引用不同，这个片段在正文**之前**就到了——前端要边流边把 `[图1]` 换成真图，
 * 拿不到对照表就只能干等。这不违反「说不知道就不挂来源」：模型答
 * 「知识库暂无此内容」时正文里根本不会出现 [图N]，什么都不会渲染。
 */
export function messageImages(message: CopilotUIMessage): AnswerImage[] {
  for (const part of message.parts) {
    if (part.type === "data-images") return part.data.images;
  }
  return [];
}

/** M7：这条消息有没有可下载的方案（xlsx）。 */
export function messageDownload(
  message: CopilotUIMessage,
): { url: string; name: string } | null {
  for (const part of message.parts) {
    if (part.type === "data-download") return part.data;
  }
  return null;
}

/**
 * M7：这条消息里 Agent 调过的工具。
 *
 * AI SDK 把工具调用收进 `tool-<toolName>` 类型的 part 里（不是 `tool-call`），
 * 每个 part 带 `state`：`input-streaming` / `input-available` /
 * `output-available` / `output-error`。这里只取渲染要用的三样。
 *
 * ⚠️ 后端发的 `toolName` 已经是中文标签（「检索知识库」），不是 `search_kb`——
 * 转换在服务端做（见 agent/runner.py 的 TOOL_LABELS），前端不用维护第二份映射表。
 */
export type ToolStep = { id: string; name: string; done: boolean; failed: boolean };

export function messageTools(message: CopilotUIMessage): ToolStep[] {
  const out: ToolStep[] = [];
  for (const part of message.parts) {
    if (!part.type.startsWith("tool-") || part.type === "tool-invocation") continue;
    const p = part as { type: string; toolCallId?: string; state?: string };
    if (!p.toolCallId) continue;
    out.push({
      id: p.toolCallId,
      name: part.type.slice("tool-".length),
      done: p.state === "output-available",
      failed: p.state === "output-error",
    });
  }
  return out;
}
