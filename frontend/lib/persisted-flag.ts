"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * 一个记在 localStorage 里的开关（侧栏折不折叠这类偏好）。
 *
 * 同样不用 `useEffect` + `setState` 读取：那会触发级联渲染，React 19 的
 * `set-state-in-effect` 规则直接报错，而且首帧一定是错的值、随后闪一下。
 * `useSyncExternalStore` 是专门干这个的——预渲染用 fallback，
 * 水合后读真实值，还顺带白拿跨标签页同步。
 */

const listeners = new Map<string, Set<() => void>>();

function listenersFor(key: string): Set<() => void> {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  return set;
}

function read(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : raw === "true";
  } catch {
    return fallback; // 隐私模式
  }
}

export function usePersistedFlag(
  key: string,
  fallback = false,
): readonly [boolean, (next: boolean | ((prev: boolean) => boolean)) => void] {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const set = listenersFor(key);
      set.add(onChange);
      window.addEventListener("storage", onChange);
      return () => {
        set.delete(onChange);
        window.removeEventListener("storage", onChange);
      };
    },
    [key],
  );

  const value = useSyncExternalStore(
    subscribe,
    () => read(key, fallback),
    () => fallback,
  );

  const setValue = useCallback(
    (next: boolean | ((prev: boolean) => boolean)) => {
      const resolved = typeof next === "function" ? next(read(key, fallback)) : next;
      try {
        localStorage.setItem(key, String(resolved));
      } catch {
        /* 存不了就只在本次会话生效 */
      }
      listenersFor(key).forEach((l) => l());
    },
    [key, fallback],
  );

  return [value, setValue] as const;
}

/**
 * 一个记在 localStorage 里的多选一偏好（回答档位这类）。
 *
 * 和 `usePersistedFlag` 同一套机制，只是值域从布尔换成一组字符串。
 * **不认识的值一律退回 fallback**：手改过 localStorage、或者以后删掉某个档位时，
 * 不能让界面卡在一个已经不存在的选项上。
 */
export function usePersistedChoice<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): readonly [T, (next: T) => void] {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const set = listenersFor(key);
      set.add(onChange);
      window.addEventListener("storage", onChange);
      return () => {
        set.delete(onChange);
        window.removeEventListener("storage", onChange);
      };
    },
    [key],
  );

  const readChoice = useCallback((): T => {
    try {
      const raw = localStorage.getItem(key) as T | null;
      return raw !== null && allowed.includes(raw) ? raw : fallback;
    } catch {
      return fallback; // 隐私模式
    }
  }, [key, allowed, fallback]);

  const value = useSyncExternalStore(subscribe, readChoice, () => fallback);

  const setValue = useCallback(
    (next: T) => {
      try {
        localStorage.setItem(key, next);
      } catch {
        /* 存不了就只在本次会话生效 */
      }
      listenersFor(key).forEach((l) => l());
    },
    [key],
  );

  return [value, setValue] as const;
}
