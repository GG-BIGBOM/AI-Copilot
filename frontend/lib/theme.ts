"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * 深色模式。
 *
 * 真正的开关是 `<html>` 上的 `dark` 类，由 layout 里那段**同步内联脚本**
 * 在首次绘制之前就设好（见 THEME_INIT_SCRIPT）。这里只负责让按钮图标
 * 跟着当前状态走。
 *
 * 为什么不用 `useEffect` + `useState` 读 localStorage：
 *   - 那样主题是在首屏画完之后才应用的，深色用户每次进页面都要**闪一下白**；
 *   - React 19 的 `set-state-in-effect` 规则也会直接报错。
 * `useSyncExternalStore` 正是为「读浏览器里的外部状态」准备的：
 * 预渲染时用 server snapshot，水合后再读真实 DOM，不会有 hydration 警告。
 */

const STORAGE_KEY = "theme";

/**
 * 首次绘制前执行，避免主题闪烁。
 *
 * 放在 `<body>` 的第一个子节点，同步执行，早于任何内容绘制。
 * 整段包在 try/catch 里：隐私模式下 localStorage 会抛异常，
 * 不能因为读不到主题就让整个页面白屏。
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var t=localStorage.getItem("${STORAGE_KEY}");
var d=t?t==="dark":window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.classList.toggle("dark",d);
}catch(e){}})();`;

const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  // 另一个标签页切了主题，这边跟着变
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

// 以 DOM 为准，而不是另存一份 state——两份状态迟早会不一致
const getSnapshot = () => document.documentElement.classList.contains("dark");

// 预渲染时没有 document。返回 false 只影响图标的初始朝向，水合后立刻纠正
const getServerSnapshot = () => false;

export function useDarkMode(): readonly [boolean, () => void] {
  const isDark = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = useCallback(() => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "dark" : "light");
    } catch {
      /* 隐私模式下存不了，本次会话仍然能切，只是刷新后不记得 */
    }
    listeners.forEach((l) => l());
  }, []);

  return [isDark, toggle] as const;
}
