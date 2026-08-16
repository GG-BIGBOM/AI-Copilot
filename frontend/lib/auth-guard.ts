"use client";

/**
 * 客户端路由守卫。
 *
 * ⚠️ **这不是安全边界。** 真正的鉴权在 FastAPI：每个受保护的接口都自己查
 * cookie 里的 JWT，未登录一律 401。这里做的只是「别让没登录的人对着一个
 * 空白聊天页发呆」——把人早点送去登录页，纯体验优化。
 *
 * 静态导出没有 middleware，也没有服务端渲染时的 cookie 可读，
 * 所以只能挂在 useEffect 里，这也决定了首屏一定有一小段 loading 态。
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, type User } from "@/lib/api";

type AuthState =
  | { status: "loading"; user: null }
  | { status: "authed"; user: User }
  | { status: "anon"; user: null };

const LOADING: AuthState = { status: "loading", user: null };

/** 受保护页面用：没登录就踢去登录页。 */
export function useRequireAuth(): AuthState {
  const router = useRouter();
  const [state, setState] = useState<AuthState>(LOADING);

  useEffect(() => {
    let alive = true;
    api
      .me()
      .then((user) => alive && setState({ status: "authed", user }))
      .catch(() => {
        if (!alive) return;
        setState({ status: "anon", user: null });
        router.replace("/login");
      });
    return () => {
      alive = false;
    };
  }, [router]);

  return state;
}

/** 登录/注册页用：已经登录了就别停在这儿，直接进聊天页。 */
export function useRedirectIfAuthed(): boolean {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .me()
      .then(() => alive && router.replace("/chat"))
      .catch(() => alive && setChecking(false));
    return () => {
      alive = false;
    };
  }, [router]);

  return checking;
}
