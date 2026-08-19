/**
 * 浮层的堆叠层级。
 *
 * ⚠️ **这个常量是一次线上故障的产物，别随手改。**
 *
 * 侧边栏 `<aside>` 写着 `z-50`，而且它是外层 flex 容器的**子项**——
 * **z-index 对 flex 子项是生效的**，和 `position` 是不是 `static` 无关
 * （CSS 里 z-index 只对定位元素生效，但 flex/grid 子项是明确的例外）。
 *
 * 而 Base UI 的菜单、悬浮卡片都通过 portal 挂到 `<body>` 上，默认
 * `z-index: auto`（也就是 0）。于是侧边栏里的每一个菜单都被侧边栏**盖住**：
 * DOM 里确实打开了、`opacity` 也是 1，但屏幕上什么都看不到，
 * 点下去打到的是侧边栏本身。
 *
 * 表现就是用户说的「三个点点不开」「删不掉」。
 * 排查时最容易被骗的一点是：`document.querySelector('[role=menuitem]')` 找得到，
 * 一切看起来都正常——真正的证据是 `document.elementsFromPoint()`，
 * 它会告诉你那个坐标上最上层的是 `ASIDE`，不是菜单。
 *
 * 所以**所有 portal 出去的浮层定位层都要用这个类**，别再各写各的。
 */
export const POPUP_LAYER = "z-[60] outline-hidden";
