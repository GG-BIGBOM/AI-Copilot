/**
 * 「这一轮还什么都没回来」——决定要不要显示思考态。
 *
 * ⚠️⚠️ **判据是「页面上有没有东西在动」，不是 `status`。**
 *
 * 原来写的是 `status === "submitted"`。`useChat` 收到流里**第一个 part**
 * 就把 status 翻成 `"streaming"`——而那个 part 不一定是正文：可能是
 * `start`，可能是一步工具调用。于是思考态在第一个字之前就消失了，
 * 而正文还没开始，页面上什么都没有。
 *
 * 那段空白有多长取决于这一轮要检索多久，**而 Agent 全量之后每一轮都要检索**。
 * 用户看到的是「转了一下，然后没了」，比一直转着更像卡死。
 *
 * 所以改成看三样可见物：正文、推理草稿、工具步骤。
 * 三样都没有 = 页面上一个动的东西都没有 = 该显示思考态。
 *
 * ⚠️ 详解档的草稿（`messageReasoning`）算「在动」：`ReasoningPanel` 自己
 * 会显示「正在思考」并把草稿流出来，那时再叠一个思考态就是两个转圈。
 * 同理 `messageTools`——`AgentTrace` 会显示「正在分析」。
 */

import {
  messageReasoning,
  messageText,
  messageTools,
  type CopilotUIMessage,
} from "./chat-types.ts";

export type StreamStatus = "submitted" | "streaming" | "ready" | "error";

export function isWaitingForFirstOutput(
  messages: readonly CopilotUIMessage[],
  status: StreamStatus,
): boolean {
  // 没在跑就不是等待。⚠️ `error` 也要返回 false——一条失败的会话上面
  // 挂着「正在理解问题」，用户会一直等下去
  if (status !== "submitted" && status !== "streaming") return false;

  const last = messages.at(-1);
  // 刚发出去、助手消息还没建起来：最后一条是用户自己那句
  if (!last || last.role !== "assistant") return true;

  return (
    !messageText(last) && !messageReasoning(last) && messageTools(last).length === 0
  );
}
