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
  /** 管理员能生成邀请码。非管理员根本看不到那个入口 */
  is_admin?: boolean;
};

export type InviteState = { codes: string[]; unused: number };

/** 一条答案订正：这个问题以后照这个答。字段跟后端 `VerifiedOut` 对齐。 */
export type Verified = {
  id: string;
  question: string;
  answer: string;
  created_at: string;
  updated_at: string;
};

export type VerifiedSaved = {
  verified: Verified;
  /** 落库了不等于进索引了。只看 201 会骗人——用户改完再问一遍会发现答案没变 */
  applied: boolean;
  note: string;
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
export const DOC_IN_PROGRESS: DocumentSummary["status"][] = [
  "pending",
  "running",
];

export type StoredMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  citations: Citation[] | null;
  /** 正文里 [图1] 的对照表。没有它，历史消息里的图号就成了裸标记 */
  images: { n: number; url: string }[] | null;
  created_at: string;
  /**
   * 这条回答对应的追踪记录（M11 P2）。👍👎 打给它。
   *
   * ⚠️ **历史消息也要有**：trace id 平时是随 SSE 发过来的，刷新一次就没了。
   * 而用户很常见的行为恰恰是回头翻历史、看到一条当时没细看的烂答案才想点踩。
   */
  trace_id: string | null;
  /** 已经点过的赞/踩。刷新后按钮要保持按下的样子，否则用户会以为没点上 */
  feedback: FeedbackVote | null;
};

export type FeedbackVote = "up" | "down";

/**
 * 点👎时可以选的原因。**是枚举不是自由文本**：
 * 自由文本收上来的是「不好」「不对」这类没有信息量的话，而这六个选项
 * 每一个都直接对应一种排查方向——「知识库明明有」查检索、「答错了」查生成、
 * 「缺少截图」查配图带出率。
 */
export const FEEDBACK_REASONS = [
  { value: "wrong", label: "答错了" },
  { value: "incomplete", label: "没答到重点" },
  { value: "should_know", label: "知识库明明有" },
  { value: "bad_source", label: "来源不对" },
  { value: "unclear", label: "步骤不清楚" },
  { value: "no_image", label: "缺少截图" },
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASONS)[number]["value"];


/* ─────────────── 管理台（M15-A，只读）───────────────
 *
 * ⚠️ 这一段的每个接口在后端都挂着 `CurrentAdmin`。**前端这边不做鉴权**，
 * `useRequireAdmin` 只是别让非管理员对着一屏 403 发呆——真正的门在服务端，
 * 任何一个会开控制台的人都绕得过前端那道。
 */

export type AdminRange = "24h" | "7d" | "30d";

export type AdminLatency = { p50: number | null; p95: number | null; count: number };

/** 概览。⚠️ **刻意没有任何问题原文**——一个人问过什么连起来就是他在处理
 *  哪个客户、哪个故障，那不该出现在一眼扫过去的仪表盘上。 */
export type AdminOverview = {
  range: AdminRange;
  since: string;
  questions: number;
  active_users: number;
  by_source: Record<string, number>;
  thumbs_up: number;
  thumbs_down: number;
  feedback_rate: string;
  agent_requests: number;
  agent_without_tools: number;
  tool_bypass: number;
  interrupted: number;
  errors: number;
  ttfb: AdminLatency;
  duration: AdminLatency;
  tokens: number;
  uploaded_documents: number;
  failed_jobs: number;
  verified_answers: number;
  users_total: number;
  documents_total: number;
};

export type AdminUserRow = {
  id: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_active_at: string | null;
  requests: number;
  thumbs_up: number;
  thumbs_down: number;
  no_answer: number;
  agent_requests: number;
  ttfb_p95: number | null;
  tokens: number;
  uploads: number;
  storage_bytes: number;
};

export type AdminUserPage = {
  total: number;
  limit: number;
  offset: number;
  range: AdminRange;
  items: AdminUserRow[];
};

export type AdminUserDetail = {
  id: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  daily_token_quota: number;
  range: AdminRange;
  questions: number;
  by_source: Record<string, number>;
  by_route: Record<string, number>;
  by_space: Record<string, number>;
  thumbs_up: number;
  thumbs_down: number;
  errors: number;
  ttfb: AdminLatency;
  duration: AdminLatency;
  tokens: number;
  trend: { day: string; requests: number }[];
  /** ⚠️ 详情页**有**问题原文：管理员点进来是一次明确的动作，不是仪表盘顺带展示 */
  recent: {
    id: string;
    created_at: string;
    route: string;
    answer_source: string | null;
    question: string;
    ttfb_ms: number | null;
    total_ms: number | null;
    ok: boolean;
    feedback: FeedbackVote | null;
  }[];
  documents: {
    id: string;
    title: string;
    status: DocumentSummary["status"];
    chunk_count: number;
    size_bytes: number | null;
    created_at: string;
    error: string | null;
  }[];
};

export type AdminFeedbackRow = {
  id: string;
  created_at: string;
  feedback: FeedbackVote;
  feedback_reason: FeedbackReason | null;
  feedback_at: string | null;
  user_email: string | null;
  question: string;
  answer: string | null;
  knowledge_space: string | null;
  route: string;
  answer_source: string | null;
  tools: string[] | null;
  chunk_count: number;
  top_score: number | null;
  private_hits: number;
  citations: Citation[] | null;
  images: { n: number; url: string }[] | null;
  ttfb_ms: number | null;
  total_ms: number | null;
  model: string | null;
  ok: boolean;
  error: string | null;
};

export type AdminFeedbackPage = {
  total: number;
  limit: number;
  offset: number;
  items: AdminFeedbackRow[];
};

/** 答案来源在页面上的说法。后端的枚举是给程序看的，这里是给人看的 */
export const ANSWER_SOURCE_LABEL: Record<string, string> = {
  // 人写定、经审核发布的标准答案，命中时**没有经过模型改写**
  verified: "标准答案（人写定）",
  kb: "知识库回答",
  general_knowledge: "常识回答（无出处）",
  no_answer: "拒答",
  canned: "寒暄",
  tool: "工具（出方案/查文档）",
};


/* ─────────────── 答案纠错（M16）───────────────
 *
 * ⚠️ **提交 ≠ 生效。** 用户提交的是一条 `pending` 的纠错，进审核队列；
 * 管理员通过并发布之后，它才变成这个知识版本下所有人共用的标准答案。
 * 在 M16 之前这里是"提交即公共生效、无人审核"的——任何注册用户都能往公共
 * 知识库里塞内容。文案上必须说清这一点，否则用户会以为自己改完就生效了。
 */

export type CorrectionStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "published";

export const CORRECTION_STATUS_LABEL: Record<CorrectionStatus, string> = {
  pending: "待审核",
  approved: "已通过，待发布",
  rejected: "已拒绝",
  withdrawn: "已撤回",
  published: "已发布",
};

export type AnswerCorrection = {
  id: string;
  status: CorrectionStatus;
  version: number;
  trace_id: string | null;
  message_id: string | null;
  knowledge_space_id: string | null;
  original_question: string;
  original_answer: string;
  original_citations: Citation[] | null;
  original_images: { n: number; url: string }[] | null;
  corrected_answer_markdown: string;
  reason: string;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
};

/** 审核队列里的一行。列表只给「该不该点进去」需要的东西 */
export type AdminCorrectionRow = {
  id: string;
  status: CorrectionStatus;
  version: number;
  created_at: string;
  updated_at: string;
  submitted_by_email: string | null;
  knowledge_space: string | null;
  original_question: string;
  reason: string;
  reviewed_at: string | null;
};

export type AdminCorrectionDetail = AdminCorrectionRow & {
  original_answer: string;
  original_citations: Citation[] | null;
  original_images: { n: number; url: string }[] | null;
  corrected_answer_markdown: string;
  review_note: string | null;
  trace_id: string | null;
  message_id: string | null;
  /** 审核快照。可以整段粘走、进 Git、拿两版做 diff */
  markdown: string;
  /**
   * 提交人贴在修正稿里的截图。
   *
   * ⚠️ `public=false` 时它还是**只有提交人自己能看的私有图**，发布会把它
   * 变成全站可见——审核界面必须在按下发布之前说清楚这件事。
   */
  images: { id: string; url: string; public: boolean }[];
};

export type AdminCorrectionPage = {
  total: number;
  limit: number;
  offset: number;
  items: AdminCorrectionRow[];
};

/** 纠错里贴的一张截图。`markdown` 直接插进光标处即可 */
export type CorrectionImage = {
  id: string;
  url: string;
  markdown: string;
};

export type PublishResult = {
  correction_id: string;
  verified_id: string;
  verified_version: number;
  knowledge_space: string | null;
  chunks: number;
  /** 发布了不等于进索引了。只看 200 会骗人——同 `CorrectionSaved.applied` */
  applied: boolean;
  note: string;
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
      .map((d) =>
        typeof d === "object" && d !== null
          ? (d as { msg?: string }).msg
          : null,
      )
      .filter((m): m is string => Boolean(m))
      // 后端自定义校验抛 ValueError 时，Pydantic 会在我们写的中文前面拼一句
      // `Value error, `。那是给开发者看的，不该出现在用户眼前
      .map((m) => m.replace(/^Value error,\s*/, ""));
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
    throw new ApiError(
      readDetail(body, `请求失败（HTTP ${res.status}）`),
      res.status,
    );
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
async function upload<T = UploadResult>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
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
      readDetail(
        body,
        res.status === 413 ? "文件太大了。" : `上传失败（HTTP ${res.status}）`,
      ),
      res.status,
    );
  }
  return (await res.json()) as T;
}

/**
 * 一个可选的知识版本（M18）。字段跟后端 `SpaceOut` 对齐。
 *
 * ⚠️ **只有 code，没有 id。** id 每套环境都不一样（本机、服务器各建各的），
 * 把它写进前端就等于把两套环境绑死。会话和空间的绑定在服务端完成。
 */
export type KnowledgeSpace = {
  code: string;
  name: string;
  description: string | null;
};

export const api = {
  register: (body: { email: string; password: string; inviteCode: string }) =>
    request<User>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  login: (body: { email: string; password: string }) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  me: () => request<User>("/api/auth/me"),

  conversations: () => request<ConversationSummary[]>("/api/conversations"),

  /**
   * 用户能选的知识版本。**只列 active 且可选的**——后端已经滤过
   * （`spaces.selectable`），前端不再自己判断，否则会出现两处判据。
   */
  knowledgeSpaces: () => request<KnowledgeSpace[]>("/api/knowledge-spaces"),

  messages: (id: string) =>
    request<StoredMessage[]>(`/api/conversations/${id}/messages`),

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

  invites: () => request<InviteState>("/api/invites"),

  createInvites: (count: number) =>
    request<InviteState>("/api/invites", {
      method: "POST",
      body: JSON.stringify({ count }),
    }),

  verified: () => request<Verified[]>("/api/verified"),

  /** 订正一条答案。服务端**当场**把它写进索引，所以回执里的 `applied` 才是真话。 */
  saveVerified: (body: { question: string; answer: string }) =>
    request<VerifiedSaved>("/api/verified", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteVerified: (id: string) =>
    request<void>(`/api/verified/${id}`, { method: "DELETE" }),

  corrections: () => request<Correction[]>("/api/corrections"),

  /**
   * 写一条勘误。服务端会**当场**把那一篇重新入库，所以回执里的 `applied`
   * 才是「现在提问会不会用上」——只看 201 会骗人。
   */
  saveCorrection: (body: {
    target_url: string;
    title: string;
    reason: string;
    body: string;
  }) =>
    request<CorrectionSaved>("/api/corrections", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteCorrection: (id: string) =>
    request<void>(`/api/corrections/${id}`, { method: "DELETE" }),

  /**
   * 给某一轮问答点赞/点踩（M11 P2）。
   *
   * 写的是 `request_trace` 那张表的两列，**不是独立的 feedback 表**——
   * 这样点开一条差评能直接看到当时检索到几块、调了什么工具、rerank 多少分。
   * 分表的话一个 👎 就只是个计数器，「差评 → 找原因 → 补评测题」这个闭环
   * 根本转不动。
   */
  sendFeedback: (body: {
    traceId: string;
    vote: FeedbackVote;
    reason?: FeedbackReason;
  }) =>
    request<{ trace_id: string; vote: FeedbackVote }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  documents: () => request<DocumentSummary[]>("/api/documents"),

  uploadDocument: (file: File) => upload("/api/documents", file),

  deleteDocument: (id: string) =>
    request<void>(`/api/documents/${id}`, { method: "DELETE" }),

  // ─────────────── 管理台（只读）───────────────

  adminOverview: (range: AdminRange) =>
    request<AdminOverview>(`/api/admin/overview?range=${range}`),

  adminUsers: (p: { range: AdminRange; q?: string; limit: number; offset: number }) =>
    request<AdminUserPage>(
      `/api/admin/users?range=${p.range}&limit=${p.limit}&offset=${p.offset}` +
        (p.q ? `&q=${encodeURIComponent(p.q)}` : ""),
    ),

  adminUser: (id: string, range: AdminRange) =>
    request<AdminUserDetail>(`/api/admin/users/${id}?range=${range}`),

  adminFeedback: (p: {
    kind: "down" | "up" | "all";
    reason?: string;
    range: AdminRange;
    limit: number;
    offset: number;
  }) =>
    request<AdminFeedbackPage>(
      `/api/admin/feedback?kind=${p.kind}&range=${p.range}&limit=${p.limit}&offset=${p.offset}` +
        (p.reason ? `&reason=${p.reason}` : ""),
    ),

  // ─────────────── 答案纠错（M16）───────────────

  /**
   * 提交一条纠错。**进审核队列，不立刻生效。**
   *
   * 只传 `traceId` 和改成什么样：原问题、原回答、原引用、原配图、知识版本
   * 都由服务端从会话里取。把原答案一起传上去的话，客户端就能伪造一段
   * 从未存在过的"原答案"，而审核界面上看不出真假。
   */
  submitCorrection: (body: { traceId: string; correctedAnswer: string; reason: string }) =>
    request<AnswerCorrection>("/api/answer-corrections", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  myCorrections: () => request<AnswerCorrection[]>("/api/answer-corrections/mine"),

  /**
   * 往纠错稿里贴一张截图。**先传、后绑**：提交那一刻服务端才把正文里
   * 引用到的图挂到这条纠错上，没提交的那些由 `prune-images` 按时清掉。
   *
   * 返回的 `markdown` 直接插进光标处——格式不该由前端自己拼，
   * 拼错一个感叹号，正文里出现的就是一段裸链接而不是一张图。
   */
  uploadCorrectionImage: (file: File) =>
    upload<CorrectionImage>("/api/answer-corrections/images", file),

  withdrawCorrection: (id: string) =>
    request<AnswerCorrection>(`/api/answer-corrections/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ action: "withdraw" }),
    }),

  adminCorrections: (p: { status: string; limit: number; offset: number }) =>
    request<AdminCorrectionPage>(
      `/api/admin/corrections?status=${p.status}&limit=${p.limit}&offset=${p.offset}`,
    ),

  adminCorrection: (id: string) =>
    request<AdminCorrectionDetail>(`/api/admin/corrections/${id}`),

  /** 通过 / 拒绝。`correctedAnswer` 是管理员的二次修改，可以顺手改一版再通过 */
  reviewCorrection: (
    id: string,
    body: {
      decision: "approve" | "reject";
      note?: string;
      corrected_answer_markdown?: string;
      version?: number;
    },
  ) =>
    request<AdminCorrectionDetail>(`/api/admin/corrections/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 发布成标准答案：同一个知识版本下的所有人，下次问到就拿这个答案 */
  publishCorrection: (id: string, version?: number) =>
    request<PublishResult>(`/api/admin/corrections/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
};
