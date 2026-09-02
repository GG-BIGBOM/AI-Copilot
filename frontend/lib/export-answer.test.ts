import assert from "node:assert/strict";
import test from "node:test";

import { exportFilename, toMarkdown } from "./export-answer.ts";

const ORIGIN = "https://liushun666.cn";
const CITES = [
  { n: 1, title: "快递拦截配置", heading: "三、支持的快递公司", url: "https://y.cn/a", score: 1 },
  { n: 2, title: "自动审核设置方式", heading: null, url: null, score: 1 },
];

// ─────────── 文件名 ───────────

test("用提问当文件名，只摘掉文件系统不认的字符", () => {
  // ⚠️ 全角冒号「：」是合法的，不摘——Windows 只禁 ASCII 的 `:`。
  // 摘掉只会让文件名比原问题更难认
  assert.equal(exportFilename("快递/拦截：怎么配?", "md"), "快递 拦截：怎么配.md");
});

test("⚠️ 长提问要截断——多数文件系统单段路径上限 255 字节，中文一个字 3 字节", () => {
  const name = exportFilename("退货".repeat(60), "pdf");
  assert.equal(name.length, 40 + ".pdf".length);
  assert.ok(name.endsWith(".pdf"));
});

test("⚠️ 空提问要有兜底名字，不能产出 `.md` 这种 Unix 下的隐藏文件", () => {
  assert.equal(exportFilename("", "md"), "回答.md");
  assert.equal(exportFilename("///", "md"), "回答.md");
});

// ─────────── Markdown ───────────

test("问题当标题，来源列在最后", () => {
  const md = toMarkdown(
    { question: "快递拦截怎么配", answer: "在设置—物流里配置 [1]。", citations: CITES },
    ORIGIN,
  );
  assert.match(md, /^# 快递拦截怎么配\n/);
  assert.match(md, /## 来源/);
  assert.match(md, /1\. \[快递拦截配置 — 三、支持的快递公司\]\(https:\/\/y\.cn\/a\)/);
});

test("没有链接的来源不写成空链接", () => {
  const md = toMarkdown({ answer: "见来源。", citations: CITES }, ORIGIN);
  assert.match(md, /2\. 自动审核设置方式$/m);
  assert.doesNotMatch(md, /\]\(\)/);
});

test("⭐ [图N] 换成真的图片语法，且补成绝对地址", () => {
  // 导出的 md 会离开这个页面（贴进笔记、发给同事），相对路径到了别处就是死链，
  // 而且不报错——只是图裂了
  const md = toMarkdown(
    { answer: "第一步[图1]。", images: [{ n: 1, url: "/api/images/abc" }] },
    ORIGIN,
  );
  assert.match(md, /!\[图1\]\(https:\/\/liushun666\.cn\/api\/images\/abc\)/);
});

test("⚠️ 后端没有的图号要删掉，和页面渲染同一条判据", () => {
  const md = toMarkdown(
    { answer: "第一步[图1]，第二步[图9]。", images: [{ n: 1, url: "/api/images/abc" }] },
    ORIGIN,
  );
  assert.doesNotMatch(md, /图9/);
});

test("已经是绝对地址的图不重复加前缀", () => {
  const md = toMarkdown(
    { answer: "[图1]", images: [{ n: 1, url: "https://cdn.example.com/x.png" }] },
    ORIGIN,
  );
  assert.match(md, /\(https:\/\/cdn\.example\.com\/x\.png\)/);
  assert.doesNotMatch(md, /liushun666/);
});

test("没有来源就不写「## 来源」这个空段", () => {
  const md = toMarkdown({ answer: "知识库暂无此内容。" }, ORIGIN);
  assert.doesNotMatch(md, /## 来源/);
});

test("没有问题时不写空标题", () => {
  const md = toMarkdown({ answer: "正文。" }, ORIGIN);
  assert.doesNotMatch(md, /^#\s*$/m);
  assert.equal(md, "正文。\n");
});
