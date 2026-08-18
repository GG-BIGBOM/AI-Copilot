/**
 * 回答档位。
 *
 * 值必须和后端 `ChatRequest.mode` 的 Literal 一字不差（fast / deep）——
 * 对不上的话 pydantic 会 422，而前端只会收到一句没头没尾的报错。
 *
 * 两档的**防幻觉规则完全一样**，差别只在写法：简答直接给步骤，
 * 详解把前置条件、注意事项、常见错误都展开。
 */

import { usePersistedChoice } from "@/lib/persisted-flag";

export const ANSWER_MODES = ["fast", "deep"] as const;

export type AnswerMode = (typeof ANSWER_MODES)[number];

export const MODE_META: Record<AnswerMode, { label: string; hint: string }> = {
  fast: { label: "简答", hint: "直接给步骤，快" },
  deep: { label: "详解", hint: "展开前置条件与注意事项，慢一些" },
};

/** 记在 localStorage 里，换一台设备重新选一次就行 */
export function useAnswerMode() {
  return usePersistedChoice<AnswerMode>("answer-mode", ANSWER_MODES, "fast");
}
