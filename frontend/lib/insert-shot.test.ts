import assert from "node:assert/strict";
import test from "node:test";

import { insertShot } from "./insert-shot.ts";

const SHOT = "![截图](/api/images/abc)";
const ANSWER = "第一步打开设置。\n第二步点保存。";

test("插在光标处，前后各补一个空行", () => {
  // 光标停在「第一步打开设置。」这一行末尾（第 8 个字符后）
  assert.equal(insertShot(ANSWER, SHOT, 8), `第一步打开设置。\n\n${SHOT}\n\n\n第二步点保存。`);
});

test("⭐ 从没点进正文时接在末尾，不是插到最前面", () => {
  // ⚠️ 这就是那个坑：从未聚焦过的 textarea，selectionStart 是 0。
  // 直接拿它当光标用，图会跑到整段答案前面——用户看到的是
  // 「一张图，然后才是答案」，而他以为自己贴在了读到的那一段旁边。
  assert.equal(insertShot(ANSWER, SHOT, null), `${ANSWER}\n\n${SHOT}\n\n`);
});

test("光标真的在第 0 位时，仍然插到最前面", () => {
  // ⚠️ 和上一条是**同一个 selectionStart 值、不同的意图**。用户特意把光标
  // 点到开头，就该插在开头。这也是为什么调用方必须自己记「聚焦过没有」，
  // 而不能用 `selectionStart === 0` 去反推——那会把这条用例判错。
  assert.equal(insertShot(ANSWER, SHOT, 0), `${SHOT}\n\n${ANSWER}`);
});

test("前面已经是空行就不再补，避免越贴空行越多", () => {
  assert.equal(insertShot("上一段。\n\n", SHOT, 6), `上一段。\n\n${SHOT}\n\n`);
});

test("空草稿里贴图不会在开头留空行", () => {
  assert.equal(insertShot("", SHOT, null), `${SHOT}\n\n`);
});

test("连着贴两张图，第二张不会挤进第一张的语法里", () => {
  const once = insertShot(ANSWER, SHOT, null);
  const twice = insertShot(once, "![截图](/api/images/def)", null);
  assert.match(twice, /!\[截图\]\(\/api\/images\/abc\)\n\n!\[截图\]\(\/api\/images\/def\)/);
});
