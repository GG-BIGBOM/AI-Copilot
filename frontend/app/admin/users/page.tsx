"use client";

/**
 * 管理台 · 用户（M15-A，只读）。
 *
 * 两个决定：
 *
 * 1. **详情是同页展开，不是 `/admin/users/[id]`。** 前端是**静态导出**
 *    （`output: "export"`），动态路由段必须在 build 时就能穷举
 *    （`generateStaticParams`）——而用户 id 是运行时才有的。硬做的话
 *    要么 build 失败，要么得为每个用户预生成一个页面。代价是详情没法直接
 *    分享链接，对一个内部管理页可以接受。
 * 2. **只读。** 启用/禁用属于 M15-B，那要配审计记录。这里连个按钮都不放——
 *    放一个点了没反应的按钮比没有更糟。
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, Search, ShieldCheck } from "lucide-react";

import {
  AdminShell,
  RangeTabs,
  Stat,
  formatBytes,
  formatMs,
  formatTime,
} from "@/components/admin/admin-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ANSWER_SOURCE_LABEL,
  api,
  ApiError,
  type AdminRange,
  type AdminUserDetail,
  type AdminUserPage,
} from "@/lib/api";
import { useRequireAdmin } from "@/lib/auth-guard";
import { cn } from "@/lib/utils";

const PAGE = 25;

export default function AdminUsersPage() {
  const auth = useRequireAdmin();
  const [range, setRange] = useState<AdminRange>("7d");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminUserPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);

  const load = useCallback(
    (r: AdminRange, query: string, off: number) => {
      api
        .adminUsers({ range: r, q: query.trim(), limit: PAGE, offset: off })
        .then((d) => {
          setPage(d);
          setError(null);
        })
        .catch((e) => setError(e instanceof ApiError ? e.message : "拉取失败"));
    },
    [],
  );

  useEffect(() => {
    if (auth.status !== "authed") return;
    load(range, q, offset);
    // q 变化时故意不自动请求：搜索按回车/按钮触发，别每敲一个字打一次后端
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.status, range, offset, load]);

  // ⚠️ 展开的那一行换了、或者时间范围换了，都要重拉。
  // **不在 effect 体里同步 setState**（React 19 的 set-state-in-effect 规则），
  // 一律等 promise 回来再写；"正在载入"由 `openId && !detail` 推出来
  useEffect(() => {
    if (auth.status !== "authed" || !openId) return;
    let alive = true;
    api
      .adminUser(openId, range)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "拉不到这个人的详情"));
    return () => {
      alive = false;
    };
  }, [auth.status, openId, range]);

  /** 展开/收起某一行。清空旧详情放在事件里做，不放 effect 里 */
  function toggle(id: string) {
    setDetail(null);
    setOpenId(openId === id ? null : id);
  }

  if (auth.status !== "authed") {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <span className="text-[13px] text-muted-foreground">正在载入…</span>
      </main>
    );
  }

  const rows = page?.items ?? [];

  return (
    <AdminShell
      title="用户"
      subtitle="使用量按所选时间范围统计；「上次出现」不受范围限制。"
      actions={<RangeTabs value={range} onChange={setRange} />}
    >
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="text-[13px]">{error}</AlertDescription>
        </Alert>
      )}

      <form
        className="mb-4 flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          load(range, q, 0);
        }}
      >
        <div className="relative min-w-52 flex-1">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="按邮箱搜索…"
            aria-label="按邮箱搜索用户"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pl-8"
          />
        </div>
        <Button type="submit" variant="outline" className="h-9">
          搜索
        </Button>
      </form>

      {!page ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">正在载入…</p>
      ) : rows.length === 0 ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">没有匹配的用户。</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border-subtle">
          <table className="w-full min-w-[820px] text-[13px]">
            <thead className="bg-surface-subtle text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">邮箱</th>
                <th className="px-3 py-2 text-right font-medium">提问</th>
                <th className="px-3 py-2 text-right font-medium">Agent</th>
                <th className="px-3 py-2 text-right font-medium">👍/👎</th>
                <th className="px-3 py-2 text-right font-medium">拒答</th>
                <th className="px-3 py-2 text-right font-medium">首字 p95</th>
                <th className="px-3 py-2 text-right font-medium">token</th>
                <th className="px-3 py-2 text-right font-medium">文档</th>
                <th className="px-3 py-2 text-left font-medium">上次出现</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr
                  key={u.id}
                  onClick={() => toggle(u.id)}
                  className={cn(
                    "cursor-pointer border-t border-border-subtle transition-colors hover:bg-surface-subtle",
                    openId === u.id && "bg-surface-subtle",
                  )}
                >
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className={cn("truncate", !u.is_active && "line-through opacity-60")}>
                        {u.email}
                      </span>
                      {u.is_admin && <ShieldCheck className="size-3.5 shrink-0 text-bronze-strong" />}
                      {!u.is_active && (
                        <span className="shrink-0 text-[12px] text-muted-foreground">已停用</span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{u.requests}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{u.agent_requests}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {u.thumbs_up}/{u.thumbs_down}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{u.no_answer}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatMs(u.ttfb_p95)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {u.tokens.toLocaleString("zh-CN")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {u.uploads}
                    <span className="ml-1 text-muted-foreground">
                      {formatBytes(u.storage_bytes)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {formatTime(u.last_active_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {page && page.total > PAGE && (
        <div className="mt-3 flex items-center justify-between text-[13px] text-muted-foreground">
          <span>
            {offset + 1}–{Math.min(offset + PAGE, page.total)} / 共 {page.total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="h-8"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              className="h-8"
              disabled={offset + PAGE >= page.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* ─── 详情：同页展开，理由见文件头 ─── */}
      {openId && (
        <section className="mt-6 rounded-lg border border-border-subtle bg-surface p-4">
          {!detail ? (
            <p className="flex items-center gap-2 py-6 text-[13px] text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              正在载入详情…
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-foreground">{detail.email}</h2>
                <span className="text-[12px] text-muted-foreground">
                  注册于 {formatTime(detail.created_at)} · 每日配额{" "}
                  {detail.daily_token_quota || "不限"}
                </span>
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="提问" value={detail.questions} />
                <Stat label="👍/👎" value={`${detail.thumbs_up}/${detail.thumbs_down}`} />
                <Stat label="出错" value={detail.errors} tone={detail.errors ? "warn" : "default"} />
                <Stat
                  label="首字 p50 / p95"
                  value={
                    detail.ttfb.p50 === null ? "—" : `${detail.ttfb.p50} / ${detail.ttfb.p95}`
                  }
                  hint={`毫秒 · ${detail.ttfb.count} 次`}
                />
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-3">
                <Distribution title="答案来源" data={detail.by_source} labels={ANSWER_SOURCE_LABEL} />
                <Distribution title="路由" data={detail.by_route} />
                <Distribution title="知识版本" data={detail.by_space} />
              </div>

              <h3 className="mt-5 text-[13px] font-medium text-foreground">最近的提问</h3>
              <p className="mb-2 text-[12px] text-muted-foreground">
                概览页不显示问题原文；点进这里是一次明确的动作，所以给全文。
              </p>
              <ul className="space-y-1.5">
                {detail.recent.length === 0 && (
                  <li className="text-[13px] text-muted-foreground">这段时间没有提问。</li>
                )}
                {detail.recent.map((r) => (
                  <li
                    key={r.id}
                    className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-border-subtle pb-1.5 text-[13px]"
                  >
                    <span className="text-muted-foreground">{formatTime(r.created_at)}</span>
                    <span className="min-w-0 flex-1 truncate text-foreground">{r.question}</span>
                    <span className="text-[12px] text-muted-foreground">
                      {r.route} · {ANSWER_SOURCE_LABEL[r.answer_source ?? ""] ?? "—"} ·{" "}
                      {formatMs(r.ttfb_ms)}
                      {r.feedback === "down" && " · 👎"}
                      {!r.ok && " · 出错"}
                    </span>
                  </li>
                ))}
              </ul>

              <h3 className="mt-5 text-[13px] font-medium text-foreground">
                上传的文档（{detail.documents.length}）
              </h3>
              <ul className="mt-1 space-y-1">
                {detail.documents.length === 0 && (
                  <li className="text-[13px] text-muted-foreground">没有上传过文档。</li>
                )}
                {detail.documents.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
                    <span className="min-w-0 flex-1 truncate text-foreground">{d.title}</span>
                    <span
                      className={cn(
                        "text-[12px]",
                        d.status === "failed" ? "text-destructive" : "text-muted-foreground",
                      )}
                    >
                      {d.status} · {d.chunk_count} 片段 · {formatBytes(d.size_bytes)}
                      {d.error ? ` · ${d.error}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </AdminShell>
  );
}

function Distribution({
  title,
  data,
  labels,
}: {
  title: string;
  data: Record<string, number>;
  labels?: Record<string, string>;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <div className="rounded-lg border border-border-subtle p-3">
      <div className="text-[13px] font-medium text-foreground">{title}</div>
      {entries.length === 0 ? (
        <p className="mt-1 text-[12px] text-muted-foreground">没有数据</p>
      ) : (
        <ul className="mt-1.5 space-y-0.5 text-[13px]">
          {entries.map(([k, n]) => (
            <li key={k} className="flex justify-between gap-2">
              <span className="truncate text-muted-foreground">{labels?.[k] ?? k}</span>
              <span className="tabular-nums text-foreground">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
