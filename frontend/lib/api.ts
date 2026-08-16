/**
 * FastAPI 后端的调用封装。
 *
 * 两件事必须记牢：
 *
 * 1. **每个请求都要 `credentials: "include"`。** 登录态在 HttpOnly cookie 里，
 *    本地开发前端在 3000、后端在 8000，是跨源的，不带这个参数浏览器根本不发 cookie，
 *    结果就是登录明明成功、下一个请求却 401。
 * 2. **`API_BASE` 是构建期常量。** 静态导出没有运行时环境变量，
 *    `npm run build` 那一刻的值会被写死进 out/。线上前后端同源，留空即可。
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

export type User = {
  id: string;
  email: string;
  created_at: string;
};

/** 一条引用来源，对应答案里的 [1][2]。字段名跟后端 `Citation.to_dict()` 对齐。 */
export type Citation = {
  n: number;
  title: string;
  heading: string | null;
  url: string | null;
  score: number;
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
};

export type StoredMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  citations: Citation[] | null;
  created_at: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI 的错误体有两种形状：`{detail: "一句话"}` 和 422 的 `{detail: [{msg,...}]}`。 */
function readDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (typeof d === "object" && d !== null ? (d as { msg?: string }).msg : null))
      .filter((m): m is string => Boolean(m));
    if (msgs.length) return msgs.join("；");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch 只有在网络层挂了才 reject——后端没起、被防火墙挡了、CORS 预检失败
    throw new ApiError("连不上服务器，请确认后端已启动。", 0);
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* 错误体不是 JSON 就算了，用兜底话术 */
    }
    throw new ApiError(readDetail(body, `请求失败（HTTP ${res.status}）`), res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  register: (body: { email: string; password: string; inviteCode: string }) =>
    request<User>("/api/auth/register", { method: "POST", body: JSON.stringify(body) }),

  login: (body: { email: string; password: string }) =>
    request<User>("/api/auth/login", { method: "POST", body: JSON.stringify(body) }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  me: () => request<User>("/api/auth/me"),

  conversations: () => request<ConversationSummary[]>("/api/conversations"),

  messages: (id: string) => request<StoredMessage[]>(`/api/conversations/${id}/messages`),
};
