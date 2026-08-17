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
};

/** 答案正文里 `[图1]` 的编号 → 图片地址。地址是根相对路径，用时拼 API_BASE。 */
export type AnswerImage = { n: number; url: string };

export type CopilotUIMessage = UIMessage<unknown, CopilotDataParts>;

/** 把一条消息里所有文本片段拼起来。 */
export function messageText(message: CopilotUIMessage): string {
  return message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
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
