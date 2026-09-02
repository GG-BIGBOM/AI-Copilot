import assert from "node:assert/strict";
import test from "node:test";

import type { CopilotUIMessage } from "./chat-types.ts";
import { isWaitingForFirstOutput } from "./waiting.ts";

/** 造一条消息。`parts` 直接照后端发的形状写，不走任何辅助函数。 */
function msg(role: "user" | "assistant", parts: unknown[]): CopilotUIMessage {
  return { id: "m", role, parts } as unknown as CopilotUIMessage;
}

const USER = msg("user", [{ type: "text", text: "快递拦截怎么配" }]);
const EMPTY_ASSISTANT = msg("assistant", []);

test("刚发出去、助手消息还没建起来：等待", () => {
  assert.equal(isWaitingForFirstOutput([USER], "submitted"), true);
});

test("⭐ 已经 streaming、但助手消息还是空的：仍然等待", () => {
  // ⚠️ 这就是那个空窗：useChat 收到流里第一个 part（可能只是 `start`）
  // 就把 status 翻成 streaming，而正文一个字都还没有。
  // 老判据 `status === "submitted"` 在这一刻就把思考态摘掉了。
  assert.equal(isWaitingForFirstOutput([USER, EMPTY_ASSISTANT], "streaming"), true);
});

test("正文来了第一个字：不再等待", () => {
  const withText = msg("assistant", [{ type: "text", text: "第" }]);
  assert.equal(isWaitingForFirstOutput([USER, withText], "streaming"), false);
});

test("详解档的推理草稿算「在动」，不叠第二个转圈", () => {
  // ReasoningPanel 自己会显示「正在思考」并把草稿流出来
  const withReasoning = msg("assistant", [{ type: "reasoning", text: "材料里好像没提到…" }]);
  assert.equal(isWaitingForFirstOutput([USER, withReasoning], "streaming"), false);
});

test("工具步骤算「在动」，AgentTrace 会显示「正在分析」", () => {
  const withTool = msg("assistant", [
    { type: "tool-检索知识库", toolCallId: "c1", state: "input-available" },
  ]);
  assert.equal(isWaitingForFirstOutput([USER, withTool], "streaming"), false);
});

test("跑完了就不等待", () => {
  assert.equal(isWaitingForFirstOutput([USER, EMPTY_ASSISTANT], "ready"), false);
});

test("⚠️ 出错了也不等待——否则失败的会话上会一直挂着「正在理解问题」", () => {
  assert.equal(isWaitingForFirstOutput([USER, EMPTY_ASSISTANT], "error"), false);
});

test("空会话不会显示思考态", () => {
  assert.equal(isWaitingForFirstOutput([], "ready"), false);
});
