/**
 * 正文里的引用角标 `[1]` → 可点的角标锚点（ISSUES.md I-6 的前半）。
 *
 * ⚠️⚠️ **「偶发裸露」的根因：认不出的角标原样留着，而且流完之后也不再管。**
 *
 * 原来的实现写在 `markdown-content.tsx` 里，两条分支：
 *
 *     citations.length === 0   → 整段原样返回
 *     编号对不上                → 返回 `raw`，也就是那个方括号本身
 *
 * 注释说这是刻意的——"宁可留一个方括号，也不做一个点开是空的角标"。
 * 那句话对**流式过程**成立：来源清单是正文流完之后才到的，那几秒里
 * 所有角标都还对不上，删了会看见它们一个个闪回来。
 *
 * ⚠️ 但**没有人管流完之后**。两种情况下角标会永久裸在正文里：
 *
 *   1. 模型编了一个不存在的编号（`[9]` 而这轮只有 5 条来源）。
 *      后端 `cited_only` 会把它从来源清单里滤掉，于是前端永远认不出它。
 *   2. 这一轮是**拒答**。后端此时一条来源都不发（防幻觉铁律），
 *      清单恒为空，正文里任何 `[n]` 都会裸着。
 *
 * ⭐ 顺带说明为什么它和配图那半边行为**不一致**：`inlineImages` 认不出的
 * `[图N]` 是**删掉**的。同一份正文里，两种标记一个删一个留——
 * 这种不一致本身就是 bug 的温床。
 *
 * 所以判据加一个维度：**还在流的时候留着，流完就删。**
 */

import type { Citation } from "./api.ts";

/** 正文里的引用角标 [1] [2]。转成假锚点交给 a 渲染器换成角标组件 */
export const CITE_REF_RE = /\[(\d{1,2})\]/g;
export const CITE_HREF = "#copilot-cite-";

/**
 * 只替换代码围栏**外面**的文本。
 *
 * 答案里出现 ```json 代码块时，块内的 `[1]` 是数据的一部分，
 * 换成引用角标就把代码改错了。
 */
export function replaceOutsideCode(text: string, run: (segment: string) => string): string {
  return text
    .split(/(```[\s\S]*?```|```[\s\S]*$)/g)
    .map((segment) => (segment.startsWith("```") ? segment : run(segment)))
    .join("");
}

export function inlineCitations(
  content: string,
  citations: readonly Pick<Citation, "n">[],
  { streaming }: { streaming: boolean },
): string {
  const known = new Set(citations.map((c) => c.n));
  return replaceOutsideCode(content, (segment) =>
    segment.replace(CITE_REF_RE, (raw, n: string) => {
      if (known.has(Number(n))) return `[${n}](${CITE_HREF}${n})`;
      // 还在流：清单最后才到，现在对不上的等会儿多半就对上了
      // 流完了：它是真的对不上——留着就是一个永远点不开的方括号
      return streaming ? raw : "";
    }),
  );
}
