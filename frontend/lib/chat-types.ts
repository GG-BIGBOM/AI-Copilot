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
};

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
