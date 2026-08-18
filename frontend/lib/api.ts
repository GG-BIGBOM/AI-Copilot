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

/** 一条人工勘误：语雀原文写错了，用这条盖掉它。字段跟后端 `CorrectionOut` 对齐。 */
export type Correction = {
  id: string;
  target_url: string;
  title: string;
  reason: string;
  body: string;
  retired: boolean;
  created_at: string;
  updated_at: string;
};

export type CorrectionSaved = {
  correction: Correction;
  /** 落库了不等于生效了——找不到对应的语雀原文、或重新入库挂了，都会是 false */
  applied: boolean;
  chunks: number;
  note: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
};

/** 「我的文档」列表里的一行。字段名跟后端 `DocumentOut` 对齐。 */
export type DocumentSummary = {
  id: string;
  title: string;
  original_filename: string | null;
  size_bytes: number | null;
  /** pending 排队中 · running 解析中 · done 已完成 · failed 失败 */
  status: "pending" | "running" | "done" | "failed";
  error: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type UploadResult = {
  document: DocumentSummary;
  /** 这份文件之前就传过了，服务端沿用了原来那一篇 */
  duplicate: boolean;
};

/** 解析还没结束的状态。有这类文档在，列表就要继续轮询。 */
export const DOC_IN_PROGRESS: DocumentSummary["status"][] = ["pending", "running"];

export type StoredMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  citations: Citation[] | null;
  /** 正文里 [图1] 的对照表。没有它，历史消息里的图号就成了裸标记 */
  images: { n: number; url: string }[] | null;
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

/**
 * 上传文件。**不能走 `request()`**——它给每个请求都写死了
 * `Content-Type: application/json`，而 multipart 的 Content-Type 里含一个随机
 * boundary，必须让浏览器自己生成。手写这个头的话，后端会因为找不到 boundary
 * 直接 422，而错误信息完全指不到这里。
 */
async function upload(path: string, file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: "POST", credentials: "include", body: form });
  } catch {
    throw new ApiError("连不上服务器，请确认后端已启动。", 0);
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* 413 之类可能根本不是 JSON（nginx 自己挡下的） */
    }
    throw new ApiError(
      readDetail(body, res.status === 413 ? "文件太大了。" : `上传失败（HTTP ${res.status}）`),
      res.status,
    );
  }
  return (await res.json()) as UploadResult;
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

  deleteConversation: (id: string) =>
    request<void>(`/api/conversations/${id}`, { method: "DELETE" }),

  /**
   * 批量删除。返回**真的删掉了几条**——服务端对不属于你的 id 是静默跳过的，
   * 所以 deleted 可能小于你传的数量，那不是错误。
   */
  bulkDeleteConversations: (ids: string[]) =>
    request<{ deleted: number }>("/api/conversations/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  corrections: () => request<Correction[]>("/api/corrections"),

  /**
   * 写一条勘误。服务端会**当场**把那一篇重新入库，所以回执里的 `applied`
   * 才是「现在提问会不会用上」——只看 201 会骗人。
   */
  saveCorrection: (body: { target_url: string; title: string; reason: string; body: string }) =>
    request<CorrectionSaved>("/api/corrections", { method: "POST", body: JSON.stringify(body) }),

  deleteCorrection: (id: string) =>
    request<void>(`/api/corrections/${id}`, { method: "DELETE" }),

  documents: () => request<DocumentSummary[]>("/api/documents"),

  uploadDocument: (file: File) => upload("/api/documents", file),

  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
};
