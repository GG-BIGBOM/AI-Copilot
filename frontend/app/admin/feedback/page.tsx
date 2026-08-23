"use client";

/**
 * 管理台 · 反馈中心（M15-A，只读）。
 *
 * ⭐ **这一页是「👍👎 写进 request_trace、不另建表」那个决定的兑现处。**
 * 每条差评旁边直接摆着当时的全链路：走的哪条路、调了什么工具、检索到几块、
 * rerank 最高分多少、首字多久、哪个模型。分表且不关联的话，这里只能显示
 * 一个计数器——「差评 → 找失败原因 → 补进评测集」这个闭环根本转不动。
 *
 * **没有「创建纠错」按钮。** 路线图第 10 节列了它，但 `answer_corrections`
 * 要到 M16 才有。放一个点了没反应的按钮比没有更糟。
 */

import { useCallback, useEffect, useState } from "react";

import { AdminShell, RangeTabs, formatMs, formatTime } from "@/components/admin/admin-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  ANSWER_SOURCE_LABEL,
  api,
  ApiError,
  FEEDBACK_REASONS,
  type AdminFeedbackPage,
  type AdminRange,
} from "@/lib/api";
import { useRequireAdmin } from "@/lib/auth-guard";
import { cn } from "@/lib/utils";

const PAGE = 20;

const KINDS = [
  { value: "down", label: "👎 差评" },
  { value: "up", label: "👍 好评" },
  { value: "all", label: "全部" },
] as const;

const REASON_LABEL = Object.fromEntries(FEEDBACK_REASONS.map((r) => [r.value, r.label]));

export default function AdminFeedbackPageView() {
  const auth = useRequireAdmin();
  const [range, setRange] = useState<AdminRange>("30d");
  const [kind, setKind] = useState<"down" | "up" | "all">("down");
  const [reason, setReason] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminFeedbackPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .adminFeedback({ kind, reason: reason || undefined, range, limit: PAGE, offset })
      .then((d) => {
        setPage(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "拉取失败"));
  }, [kind, reason, range, offset]);

  useEffect(() => {
    if (auth.status !== "authed") return;
    load();
  }, [auth.status, load]);

  if (auth.status !== "authed") {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <span className="text-[13px] text-muted-foreground">正在载入…</span>
      </main>
    );
  }

  return (
    <AdminShell
      title="反馈"
      subtitle="每条反馈都连着当时的全链路——这是把 👍👎 写进 request_trace 换来的。"
      actions={<RangeTabs value={range} onChange={setRange} />}
    >
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="text-[13px]">{error}</AlertDescription>
        </Alert>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-0.5">
          {KINDS.map((k) => (
            <button
              key={k.value}
              type="button"
              aria-pressed={kind === k.value}
              onClick={() => {
                setKind(k.value);
                setOffset(0);
              }}
              className={cn(
                "h-8 rounded-md px-2.5 text-[13px] transition-colors",
                kind === k.value
                  ? "bg-surface-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
              )}
            >
              {k.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-0.5">
          <button
            type="button"
            aria-pressed={reason === ""}
            onClick={() => {
              setReason("");
              setOffset(0);
            }}
            className={cn(
              "h-8 rounded-md px-2.5 text-[13px] transition-colors",
              reason === ""
                ? "bg-surface-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
            )}
          >
            全部原因
          </button>
          {FEEDBACK_REASONS.map((r) => (
            <button
              key={r.value}
              type="button"
              aria-pressed={reason === r.value}
              onClick={() => {
                setReason(r.value);
                setOffset(0);
              }}
              className={cn(
                "h-8 rounded-md px-2.5 text-[13px] transition-colors",
                reason === r.value
                  ? "bg-surface-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {!page ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">正在载入…</p>
      ) : page.items.length === 0 ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">
          这段时间没有符合条件的反馈。
        </p>
      ) : (
        <ul className="space-y-3">
          {page.items.map((row) => (
            <li key={row.id} className="rounded-lg border border-border-subtle bg-surface p-3.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[12px] text-muted-foreground">
                <span className={row.feedback === "down" ? "text-destructive" : "text-success"}>
                  {row.feedback === "down" ? "👎" : "👍"}
                  {row.feedback_reason ? ` ${REASON_LABEL[row.feedback_reason] ?? row.feedback_reason}` : ""}
                </span>
                <span>{formatTime(row.created_at)}</span>
                <span>{row.user_email ?? "已删除的账号"}</span>
                {row.knowledge_space && <span>版本 {row.knowledge_space}</span>}
                <span>{row.route}</span>
                <span>{ANSWER_SOURCE_LABEL[row.answer_source ?? ""] ?? "—"}</span>
              </div>

              <p className="mt-2 text-[13px] font-medium text-foreground">{row.question}</p>

              {open === row.id ? (
                <div className="mt-2 space-y-2">
                  <div className="whitespace-pre-wrap rounded-md bg-surface-subtle p-2.5 text-[13px] text-foreground">
                    {row.answer ?? "（这条回答已经被删掉了——trace 不跟着消息一起删，所以记录还在）"}
                  </div>
                  <dl className="grid gap-x-4 gap-y-1 text-[12px] text-muted-foreground sm:grid-cols-2 lg:grid-cols-3">
                    <Item k="检索块数" v={String(row.chunk_count)} />
                    <Item k="rerank 最高分" v={row.top_score === null ? "—" : row.top_score.toFixed(3)} />
                    <Item k="私有块" v={String(row.private_hits)} />
                    <Item k="工具" v={row.tools?.length ? row.tools.join("、") : "（没调）"} />
                    <Item k="首字 / 总时长" v={`${formatMs(row.ttfb_ms)} / ${formatMs(row.total_ms)}`} />
                    <Item k="模型" v={row.model ?? "—"} />
                    <Item k="引用" v={String(row.citations?.length ?? 0)} />
                    <Item k="配图" v={String(row.images?.length ?? 0)} />
                    {row.error && <Item k="错误" v={row.error} />}
                  </dl>
                </div>
              ) : null}

              <button
                type="button"
                onClick={() => setOpen(open === row.id ? null : row.id)}
                className="mt-2 text-[12px] text-bronze-strong hover:underline"
              >
                {open === row.id ? "收起" : "查看这一轮的全链路"}
              </button>
            </li>
          ))}
        </ul>
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
    </AdminShell>
  );
}

function Item({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-1.5">
      <dt className="shrink-0">{k}</dt>
      <dd className="min-w-0 truncate text-foreground">{v}</dd>
    </div>
  );
}
