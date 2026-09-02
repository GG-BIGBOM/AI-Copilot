/**
 * 往纠错草稿里插一张截图的位置计算。
 *
 * ⚠️ 拆成纯函数是为了**能被测**：这段逻辑原来长在 `verify-dialog.tsx` 里，
 * 而前端的测试只跑 `lib/*.test.ts`（见 `package.json` 的 `test`），
 * 组件里的分支一条都盖不到——于是下面那个「没点正文」的分支错了半年没人发现。
 */

/**
 * 把 `snippet` 插到 `cursor` 处，前后补空行让它单独成行。
 *
 * @param cursor 光标位置；**`null` 表示用户从没点进正文**，这时接在末尾。
 *
 * ⚠️⚠️ **`null` 和 `0` 必须是两件事。** 一个从未聚焦过的 `<textarea>`，
 * 它的 `selectionStart` 就是 `0`——和"用户特意把光标放到最开头"完全同形。
 * 原来的实现直接读 `selectionStart`，于是打开弹窗、看都不看正文就贴图，
 * 图会插到**整段答案的最前面**；而代码注释写的是"没有光标就接在末尾"。
 * 注释描述的是那个几乎不会发生的 `ref` 为空的分支，不是真实行为。
 *
 * 所以调用方必须自己记「有没有聚焦过」（`onFocus` 里置一个 ref），
 * 不能靠 `selectionStart === 0` 反推——用户真的把光标点在第 0 位时，
 * 那个反推会把他的选择当成"没选过"。
 */
export function insertShot(current: string, snippet: string, cursor: number | null): string {
  const at = cursor ?? current.length;
  const before = current.slice(0, at);
  const after = current.slice(at);
  // 图片语法贴在一段文字中间时不会单独成行，渲染出来是一张挤在句子里的小图
  const gap = before && !before.endsWith("\n\n") ? "\n\n" : "";
  return `${before}${gap}${snippet}\n\n${after}`;
}
