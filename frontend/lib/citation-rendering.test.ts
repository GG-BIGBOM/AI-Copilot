import assert from "node:assert/strict";
import test from "node:test";

import { inlineCitations, replaceOutsideCode } from "./citation-rendering.ts";

const CITES = [{ n: 1 }, { n: 2 }, { n: 3 }];
const DONE = { streaming: false };
const STREAMING = { streaming: true };

test("认得出的角标变成锚点", () => {
  assert.equal(
    inlineCitations("见 [1] 和 [3]。", CITES, DONE),
    "见 [1](#copilot-cite-1) 和 [3](#copilot-cite-3)。",
  );
});

test("⭐ 流完之后，认不出的角标要删掉——不能永久裸在正文里", () => {
  // 模型编了个 [9]，而这一轮只有 3 条来源。后端 cited_only 会把它滤掉，
  // 于是前端永远认不出它。留着就是一个永远点不开的方括号。
  assert.equal(inlineCitations("见 [9]。", CITES, DONE), "见 。");
});

test("⭐ 拒答那一轮一条来源都没有，正文里的角标同样要清干净", () => {
  // 后端拒答时一条来源都不发（防幻觉铁律），清单恒为空。
  // 老实现在 citations.length === 0 时**整段原样返回**，于是全裸。
  assert.equal(inlineCitations("知识库暂无此内容 [2]。", [], DONE), "知识库暂无此内容 。");
});

test("⚠️ 还在流的时候要留着，否则角标会一个个闪回来", () => {
  // 来源清单是正文流完之后才到的（data-citations 片段），那几秒里
  // 所有角标都还对不上。这一档的行为和改之前完全一样。
  assert.equal(inlineCitations("见 [1] 和 [9]。", [], STREAMING), "见 [1] 和 [9]。");
});

test("流式中已经认得出的照样立刻变锚点", () => {
  assert.equal(
    inlineCitations("见 [1] 和 [9]。", CITES, STREAMING),
    "见 [1](#copilot-cite-1) 和 [9]。",
  );
});

test("⚠️ 代码块里的 [1] 是数据，一个字都不许动", () => {
  const md = '前面 [1]。\n```json\n{"a": [1], "b": [9]}\n```\n后面 [9]。';
  const out = inlineCitations(md, CITES, DONE);
  assert.match(out, /```json\n\{"a": \[1\], "b": \[9\]\}\n```/);
  assert.match(out, /^前面 \[1\]\(#copilot-cite-1\)。/);
  assert.match(out, /后面 。$/);
});

test("没闭合的代码围栏也当代码，不然流式中途会把半个块改坏", () => {
  const out = replaceOutsideCode("正文 [1]\n```py\nx = [1]", (s) => s.replace(/\[1\]/g, "X"));
  assert.equal(out, "正文 X\n```py\nx = [1]");
});

test("空正文不炸", () => {
  assert.equal(inlineCitations("", CITES, DONE), "");
});
