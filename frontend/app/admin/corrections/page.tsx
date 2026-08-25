"use client";

/**
 * 管理台 · 纠错审核（M16）。
 *
 * ⭐ **这一页是「未审核不进 RAG」那条门禁的人机接口。** 用户提交的纠错停在
 * 这里，谁都不会用到它；只有在这一页点了「通过」再点「发布」，它才变成这个
 * 知识版本下所有人共用的标准答案。
 *
 * 三个决定：
 *
 * 1. **左右对比，原答案在左。** 审的是「原来错在哪」，不是「新的写得好不好」。
 *    只给新答案的话，管理员得自己回想原来长什么样——他没有那个上下文。
 * 2. **通过和发布是两个按钮。** 合成一个的话，一旦发布出问题，你分不清是
 *    「审得不对」还是「发布这一步炸了」。而且管理员常常要先通过、
 *    过一会儿再统一发布。
 * 3. **管理员能在通过之前顺手改一版**（路线图 21.1）。用户写的十有八九不能
 *    直接发布——错别字、少一步、把客户名写了进去。只给通过/拒绝的话，
 *    为了改一个字只能拒绝再让人重提。
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

import { AdminShell, formatTime } from "@/components/admin/admin-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  api,
  API_BASE,
  ApiError,
  CORRECTION_STATUS_LABEL,
  type AdminCorrectionDetail,
  type AdminCorrectionPage,
  type CorrectionStatus,
} from "@/lib/api";
import { useRequireAdmin } from "@/lib/auth-guard";
import { cn } from "@/lib/utils";

const PAGE = 20;

// `all` 不是状态，是「别过滤」。后端对拼错的状态是 422 而不是空列表——
// 静默返回空的话，你会以为「没有待审的」，而其实是查错了
const TABS: { value: string; label: string }[] = [
  { value: "pending", label: "待审核" },
  { value: "approved", label: "已通过，待发布" },
  { value: "published", label: "已发布" },
  { value: "rejected", label: "已拒绝" },
  { value: "all", label: "全部" },
];

export default function AdminCorrectionsPage() {
  const auth = useRequireAdmin();
  const [tab, setTab] = useState("pending");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminCorrectionPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminCorrectionDetail | null>(null);
  const [edited, setEdited] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .adminCorrections({ status: tab, limit: PAGE, offset })
      .then((d) => {
        setPage(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "拉取失败"));
  }, [tab, offset]);

  useEffect(() => {
    if (auth.status !== "authed") return;
    load();
  }, [auth.status, load]);

  // ⚠️ 不在 effect 体里同步 setState（React 19 的 set-state-in-effect 规则）：
  // 一律等 promise 回来再写，"正在载入"由 `openId && !detail` 推出来
  useEffect(() => {
    if (auth.status !== "authed" || !openId) return;
    let alive = true;
    api
      .adminCorrection(openId)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        setEdited(d.corrected_answer_markdown);
        setNote(d.review_note ?? "");
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "拉不到这条纠错"));
    return () => {
      alive = false;
    };
  }, [auth.status, openId]);

  function toggle(id: string) {
    setDetail(null);
    setNotice(null);
    setOpenId(openId === id ? null : id);
  }

  async function act(kind: "approve" | "reject" | "publish") {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "publish") {
        const r = await api.publishCorrection(detail.id, detail.version);
        setNotice(r.note);
      } else {
        const changed = edited.trim() !== detail.corrected_answer_markdown.trim();
        await api.reviewCorrection(detail.id, {
          decision: kind,
          note: note.trim() || undefined,
          // 只在真改过的时候才传：没改却传一遍，会在审计里留下
          // 「管理员改过内容」的假记录
          corrected_answer_markdown: changed ? edited : undefined,
          version: detail.version,
        });
        setNotice(kind === "approve" ? "已通过。还没发布——发布之后才对所有人生效。" : "已拒绝。");
      }
      const fresh = await api.adminCorrection(detail.id);
      setDetail(fresh);
      setEdited(fresh.corrected_answer_markdown);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
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
      title="纠错审核"
      subtitle="用户提交的纠错停在这里，通过并发布之后才会影响答案。"
    >
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="text-[13px]">{error}</AlertDescription>
        </Alert>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-0.5">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            aria-pressed={tab === t.value}
            onClick={() => {
              setTab(t.value);
              setOffset(0);
              setOpenId(null);
            }}
            className={cn(
              "h-8 rounded-md px-2.5 text-[13px] transition-colors",
              tab === t.value
                ? "bg-surface-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!page ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">正在载入…</p>
      ) : rows.length === 0 ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">
          {tab === "pending" ? "没有待审核的纠错。" : "这一档下没有记录。"}
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((c) => (
            <li key={c.id} className="rounded-lg border border-border-subtle bg-surface">
              <button
                type="button"
                onClick={() => toggle(c.id)}
                className="flex w-full flex-col gap-1 p-3.5 text-left transition-colors hover:bg-surface-subtle"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 text-[12px] text-muted-foreground">
                  <span
                    className={cn(
                      "font-medium",
                      c.status === "pending" && "text-bronze-strong",
                      c.status === "published" && "text-success",
                      c.status === "rejected" && "text-destructive",
                    )}
                  >
                    {CORRECTION_STATUS_LABEL[c.status as CorrectionStatus] ?? c.status}
                  </span>
                  <span>{formatTime(c.created_at)}</span>
                  <span>{c.submitted_by_email ?? "已删除的账号"}</span>
                  {c.knowledge_space && <span>版本 {c.knowledge_space}</span>}
                </div>
                <span className="text-[13px] font-medium text-foreground">
                  {c.original_question}
                </span>
                <span className="text-[12px] text-muted-foreground">理由：{c.reason}</span>
              </button>

              {openId === c.id && (
                <div className="border-t border-border-subtle p-3.5">
                  {!detail ? (
                    <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
                      <Loader2 className="size-3.5 animate-spin" />
                      正在载入…
                    </p>
                  ) : (
                    <>
                      <div className="grid gap-3 lg:grid-cols-2">
                        <section>
                          <h3 className="mb-1 text-[13px] font-medium text-muted-foreground">
                            原回答
                          </h3>
                          <div className="whitespace-pre-wrap rounded-md bg-surface-subtle p-2.5 text-[13px] leading-relaxed text-foreground">
                            {detail.original_answer}
                          </div>
                          {detail.original_citations?.length ? (
                            <ul className="mt-1.5 space-y-0.5 text-[12px] text-muted-foreground">
                              {detail.original_citations.map((cite) => (
                                <li key={cite.n} className="truncate">
                                  [{cite.n}] {cite.title}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <p className="mt-1.5 text-[12px] text-muted-foreground">
                              这一轮没有引用来源
                            </p>
                          )}
                        </section>

                        <section>
                          <h3 className="mb-1 text-[13px] font-medium text-muted-foreground">
                            修正后（可以直接改）
                          </h3>
                          <textarea
                            value={edited}
                            onChange={(e) => setEdited(e.target.value)}
                            spellCheck={false}
                            disabled={detail.status !== "pending"}
                            className="min-h-[12rem] w-full resize-y rounded-md border border-border bg-surface-subtle px-3 py-2.5 font-mono text-[13px] leading-relaxed text-foreground outline-hidden transition-colors focus:border-ring focus:bg-background disabled:opacity-70"
                            aria-label="修正后的答案"
                          />
                          {detail.status !== "pending" && (
                            <p className="mt-1 text-[12px] text-muted-foreground">
                              已经审过的纠错不能再改内容——管理员看过的和最终发布的必须是同一段文字。
                            </p>
                          )}

                          {detail.images.length > 0 && (
                            <div className="mt-2">
                              <ul className="flex flex-wrap gap-2">
                                {detail.images.map((shot) => (
                                  <li key={shot.id}>
                                    <a
                                      href={`${API_BASE}${shot.url}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                    >
                                      {/* eslint-disable-next-line @next/next/no-img-element -- 用户传的截图，没有尺寸信息 */}
                                      <img
                                        src={`${API_BASE}${shot.url}`}
                                        alt="提交人贴的截图"
                                        className="size-20 rounded-md border border-border object-cover"
                                      />
                                    </a>
                                  </li>
                                ))}
                              </ul>
                              {/* ⚠️⚠️ 这句话必须出现在**按下发布之前**：截图里可能有客户名、
                                  订单号、他自己的后台账号，而发布会把它变成全站可见 */}
                              <p className="mt-1.5 text-[12px] text-warning">
                                {detail.images.some((shot) => !shot.public)
                                  ? `这 ${detail.images.filter((shot) => !shot.public).length} 张截图现在只有提交人和管理员看得到。发布之后所有人都能看到——发布前确认里面没有客户名、订单号之类的信息。`
                                  : "这些截图已经随发布变成公开的了。"}
                              </p>
                            </div>
                          )}
                        </section>
                      </div>

                      <label
                        className="mt-3 block text-[13px] font-medium text-foreground"
                        htmlFor={`note-${c.id}`}
                      >
                        审核备注
                      </label>
                      <input
                        id={`note-${c.id}`}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        placeholder="改了什么、为什么通过或拒绝。提交人回头看得到"
                        disabled={detail.status !== "pending"}
                        className="mt-1 w-full rounded-md border border-border bg-surface-subtle px-3 py-2 text-[13px] text-foreground outline-hidden focus:border-ring focus:bg-background disabled:opacity-70"
                      />

                      {notice && (
                        <p className="mt-3 text-[13px] text-success" role="status">
                          {notice}
                        </p>
                      )}

                      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                        <details className="mr-auto text-[12px] text-muted-foreground">
                          <summary className="cursor-pointer select-none">审核快照</summary>
                          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface-subtle p-2.5 text-[12px]">
                            {detail.markdown}
                          </pre>
                        </details>

                        {detail.status === "pending" && (
                          <>
                            <Button
                              variant="outline"
                              disabled={busy}
                              onClick={() => act("reject")}
                            >
                              <X className="size-3.5" />
                              拒绝
                            </Button>
                            <Button disabled={busy} onClick={() => act("approve")}>
                              {busy && <Loader2 className="size-3.5 animate-spin" />}
                              <Check className="size-3.5" />
                              通过
                            </Button>
                          </>
                        )}
                        {detail.status === "approved" && (
                          <Button disabled={busy} onClick={() => act("publish")}>
                            {busy && <Loader2 className="size-3.5 animate-spin" />}
                            发布为标准答案
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}
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
