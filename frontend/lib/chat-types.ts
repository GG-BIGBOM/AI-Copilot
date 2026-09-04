import type { UIMessage } from "ai";

import type { Citation, FeedbackVote } from "@/lib/api";

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
  /**
   * M11 P2：这一轮在 `request_trace` 里的行号，👍👎 打给它。
   *
   * ⭐ **它在正文之前就发**，和引用相反。理由是用户点踩是在读到烂答案的
   * 第一秒，那时候流还没结束——等结束再发，那一秒就没有按钮可点。
   * 这不违反「说不知道就不挂来源」：一个 id 不构成「答案有依据」的暗示。
   *
   * `vote` **后端流里不发**，只有从历史还原的消息才带（那时它来自
   * `StoredMessage.feedback`）。放同一个片段里是为了让前端只有一个
   * 「这条消息的反馈状态」的入口——分成两个片段，迟早会出现
   * 「有 id 没 vote」或「有 vote 没 id」的组合，而那是按钮亮不亮的 bug。
   */
  trace: { id: string; vote?: FeedbackVote | null };
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
 * ⚠️ **进度不是答案。** 它是后端报的处理阶段（正在检索知识库 / 已找到 N 条
 * 相关资料 / 正在整理答案），所以单独渲染、默认折叠，也**不**参与复制、
 * 订正、判「有没有答案」。
 *
 * 它存在的唯一理由是等待：详解档第一个正文字要 8~60 秒，没有它那几十秒
 * 前端一个字都没有，用户以为「详解不回答」。
 *
 * ⚠️⚠️ 2026-09-03 之前这里流的是**模型的原始推理草稿**（后端转发
 * `reasoning_content`）。那条管子会把 system prompt、检索到的材料原文
 * 和注入内容一起送出来，绕开了三道只管正文的闸门。现在后端只发写死的
 * 常量，part 类型没变——所以这个函数一个字都不用改。
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

/**
 * M11 P2：这条回答在 `request_trace` 里的行号，以及已经点过的赞/踩。
 *
 * 两条来路在这里汇合：**这一次流出来的**消息带的是后端发的 `data-trace` 片段；
 * **从历史还原的**消息由 `toUIMessage` 用 `StoredMessage.trace_id / feedback`
 * 拼一个同名片段出来。汇成一个入口，渲染那边就只有一种情况要处理。
 */
export function messageTrace(
  message: CopilotUIMessage,
): { id: string; vote: FeedbackVote | null } | null {
  for (const part of message.parts) {
    if (part.type === "data-trace") {
      return { id: part.data.id, vote: part.data.vote ?? null };
    }
  }
  return null;
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
