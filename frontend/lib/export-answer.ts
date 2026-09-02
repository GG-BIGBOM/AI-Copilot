/**
 * 把一条回答导出成 Markdown / PDF。
 *
 * ⭐⭐ **PDF 走浏览器打印，不做服务端渲染。** 这不是偷懒，是这个项目自己的
 * 两条红线各否掉了一种做法：
 *
 *   1. 内存：服务器 1.6GB（plan.md 一）。weasyprint / playwright / chromium
 *      一个都装不下，而且服务器上**没有中文字体**——装了渲染器还要再装
 *      一套 CJK 字体包，那是几十 MB 和一堆排版调试。浏览器本来就有。
 *   2. 许可：plan.md 2.4 明确因为 AGPL 拒掉过 PyMuPDF。引入任何 PDF 库
 *      之前都要再走一遍许可审查，而浏览器打印**一个新依赖都不引入**。
 *
 * 代价是 PDF 由用户在打印对话框里「另存为 PDF」，多一步点击；换来的是
 * 中文排版天然正确、图片按登录态正常加载（同源请求会带 cookie）、
 * 以及和页面上看到的完全一致的样子。
 *
 * Markdown 那一半是纯字符串拼接，所有逻辑都在这里，可以测。
 */

import type { Citation } from "./api.ts";
import type { AnswerImage } from "./chat-types.ts";

/**
 * 文件名里**真正**不能出现的字符（Windows 最严，按它来）+ 控制字符。
 *
 * ⚠️ 只摘非法的。全角冒号「：」在文件系统里完全合法，中文标题里又常见，
 * 顺手摘掉只会让文件名比原问题更难认。连字符同理——它合法且有用。
 */
const UNSAFE_IN_FILENAME = /[\\/:*?"<>|\x00-\x1f]/g;

/**
 * 用提问那句话当文件名。
 *
 * ⚠️ **要截断。** 用户的提问可以很长，而多数文件系统的单段路径上限是
 * 255 字节——中文一个字 3 字节，84 个字就顶到头了。截到 40 个字符，
 * 留足给扩展名和可能的 " (1)" 后缀。
 *
 * ⚠️ 空问题要有兜底名字：`.md` 这种以点开头的文件在 Unix 上是隐藏文件，
 * 用户下载完会以为什么都没发生。
 */
export function exportFilename(question: string, ext: "md" | "pdf"): string {
  const base = question.replace(UNSAFE_IN_FILENAME, " ").replace(/\s+/g, " ").trim().slice(0, 40);
  return `${base || "回答"}.${ext}`;
}

/**
 * 把相对地址补成绝对地址。
 *
 * 导出的 Markdown 会离开这个页面——贴进笔记软件、发给同事。留着
 * `/api/images/xxx` 这种相对路径，到了别处就是一条死链，而且**不会报错**，
 * 只是图裂了。
 */
function absolute(url: string, origin: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  return `${origin.replace(/\/$/, "")}${url.startsWith("/") ? "" : "/"}${url}`;
}

export function toMarkdown(
  {
    question,
    answer,
    citations = [],
    images = [],
  }: {
    question?: string;
    answer: string;
    citations?: readonly Citation[];
    images?: readonly AnswerImage[];
  },
  origin: string,
): string {
  const parts: string[] = [];

  if (question) parts.push(`# ${question}`);

  // ⚠️ 正文里的 `[图N]` 要换成真的图片语法，否则导出去就是一串谁都看不懂的
  // 占位符。判据和页面渲染同源：只换后端确认存在的那些编号（见 image-rendering.ts）
  const byNumber = new Map(images.map((i) => [i.n, absolute(i.url, origin)]));
  const body = answer.replace(/\[图\s*(\d{1,2})\]/g, (raw, n: string) => {
    const url = byNumber.get(Number(n));
    return url ? `\n\n![图${n}](${url})\n\n` : "";
  });
  parts.push(body.trim());

  if (citations.length > 0) {
    parts.push("## 来源");
    parts.push(
      citations
        .map((c) => {
          const label = c.heading ? `${c.title} — ${c.heading}` : c.title;
          return c.url ? `${c.n}. [${label}](${c.url})` : `${c.n}. ${label}`;
        })
        .join("\n"),
    );
  }

  return `${parts.join("\n\n")}\n`;
}

/**
 * 把一段文本存成文件。
 *
 * ⚠️ `URL.revokeObjectURL` 不能同步调用——同步撤销时 Chrome 有时还没开始
 * 读那个 blob，表现是「点了没反应」。挪到下一个宏任务里。
 */
export function downloadTextFile(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
