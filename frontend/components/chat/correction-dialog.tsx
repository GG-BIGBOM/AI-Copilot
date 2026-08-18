"use client";

/**
 * 「这条答错了」—— 在网页上直接改知识库。
 *
 * 为什么需要它：勘误层本来只有一条路——编辑 `corrections/<slug>.md` → 重新
 * ingest → 部署。那要求你有仓库、会跑命令、还得等一次上线。实施顾问在客户
 * 现场发现「原文写的上限是 100、实际是 300」时，这条路用不上。
 *
 * 三个刻意的设计：
 *
 * 1. **必须先选一篇来源。** 勘误是「盖掉某一篇语雀原文」，没有目标就无从盖起。
 *    所以入口只出现在有引用的回答上，且候选就是这次答案引用的那几篇——
 *    让人手填 URL 只会填错，而填错的表现是「保存成功但一个字都没生效」。
 * 2. **理由必填。** 半年后回来看，没有它就不知道当初为什么改，
 *    而这是**覆盖公共知识库**的东西。
 * 3. **保存后如实说生效没有。** 服务端会当场把那一篇重新入库，但可能失败
 *    （找不到原文、embedding 挂了）。只说「已保存」会让人以为改完了，
 *    回头一问发现答案没变，就会认定这个功能是假的。
 */

import { useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError, type Citation, type CorrectionSaved } from "@/lib/api";
import { cn } from "@/lib/utils";

const POPUP =
  "fixed left-1/2 top-[8vh] z-50 w-[min(680px,calc(100vw-2rem))] -translate-x-1/2 " +
  "max-h-[84vh] overflow-y-auto rounded-2xl border border-border bg-popover p-5 " +
  "text-popover-foreground outline-hidden transition-[opacity,scale] duration-200 " +
  "ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] " +
  "data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

export function CorrectionDialog({
  open,
  citations,
  onOpenChange,
}: {
  open: boolean;
  /** 这次回答引用的来源。勘误只能盖其中一篇 */
  citations: Citation[];
  onOpenChange: (open: boolean) => void;
}) {
  // 只有带 url 的来源才能被勘误——url 是和语雀原文对齐的唯一键
  const targets = citations.filter((c) => c.url);

  const [picked, setPicked] = useState<Citation | null>(targets[0] ?? null);
  const [body, setBody] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<CorrectionSaved | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 每次打开都从头开始，不接着上次那半截。在渲染期比对 open 而不是写 effect：
  // effect 里同步 setState 会触发级联渲染，React 19 直接判错
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setPicked(targets[0] ?? null);
      setBody("");
      setReason("");
      setSaved(null);
      setError(null);
    }
  }

  async function submit() {
    if (!picked?.url || !body.trim() || reason.trim().length < 2 || busy) return;
    setBusy(true);
    setError(null);
    try {
      setSaved(
        await api.saveCorrection({
          target_url: picked.url,
          title: picked.title,
          reason: reason.trim(),
          body: body.trim(),
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = Boolean(picked?.url) && body.trim().length > 0 && reason.trim().length >= 2;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/25 transition-opacity duration-150 data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <Dialog.Popup className={POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
          <Dialog.Title className="text-base font-semibold text-foreground">
            这条答错了
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            改的是<span className="font-medium text-foreground">知识库原文</span>
            ，保存后立刻生效，之后<span className="font-medium text-foreground">所有人</span>
            问到这一篇都会用你写的内容。
          </Dialog.Description>

          {saved ? (
            <div className="mt-5">
              <div
                className={cn(
                  "flex items-start gap-2 rounded-lg border p-3 text-[13px]",
                  saved.applied
                    ? "border-border-subtle bg-surface-subtle"
                    : "border-warning/40 bg-warning/8",
                )}
              >
                {saved.applied ? (
                  <CheckCircle2 className="mt-px size-4 shrink-0 text-success" />
                ) : (
                  <AlertCircle className="mt-px size-4 shrink-0 text-warning" />
                )}
                <div>
                  <p className="text-foreground">{saved.note}</p>
                  {saved.applied && saved.chunks > 0 && (
                    <p className="mt-1 text-muted-foreground">
                      已重新生成 {saved.chunks} 个知识片段。
                    </p>
                  )}
                </div>
              </div>
              <div className="mt-4 flex justify-end">
                <Button onClick={() => onOpenChange(false)}>知道了</Button>
              </div>
            </div>
          ) : targets.length === 0 ? (
            <div className="mt-5">
              <p className="text-[13px] text-muted-foreground">
                这条回答引用的来源都没有原文链接（多半来自你自己上传的文档），
                没法用勘误盖掉。上传的文档请直接在「知识库」页重新传一份。
              </p>
              <div className="mt-4 flex justify-end">
                <Button variant="outline" onClick={() => onOpenChange(false)}>
                  关闭
                </Button>
              </div>
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <div>
                <p className="mb-1.5 text-[13px] text-muted-foreground">要改哪一篇？</p>
                <div className="space-y-px">
                  {targets.map((c) => (
                    <button
                      key={c.n}
                      type="button"
                      onClick={() => setPicked(c)}
                      aria-pressed={picked?.n === c.n}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                        picked?.n === c.n
                          ? "bg-bronze-soft text-foreground"
                          : "text-muted-foreground hover:bg-surface-subtle",
                      )}
                    >
                      <span className="flex size-4 shrink-0 items-center justify-center rounded-xs bg-surface-muted text-[10px] tabular-nums">
                        {c.n}
                      </span>
                      <span className="truncate">{c.title}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label
                  htmlFor="correction-body"
                  className="mb-1.5 block text-[13px] text-muted-foreground"
                >
                  正确的内容（会整篇替换掉这篇的正文）
                </label>
                <textarea
                  id="correction-body"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={9}
                  placeholder="把这篇文档正确的内容写在这里。可以用 Markdown。"
                  className="w-full resize-y rounded-md border border-input bg-surface px-3 py-2 text-[13px] leading-relaxed outline-none transition-colors placeholder:text-muted-foreground/70 focus-visible:border-bronze-border"
                />
              </div>

              <div>
                <label
                  htmlFor="correction-reason"
                  className="mb-1.5 block text-[13px] text-muted-foreground"
                >
                  为什么改（必填，半年后你会需要它）
                </label>
                <Input
                  id="correction-reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="例：原文写的上限是 100，实际系统里是 300，已和产品确认"
                />
              </div>

              {error && (
                <p className="text-[13px] text-destructive" role="alert">
                  {error}
                </p>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                <Button variant="ghost" onClick={() => onOpenChange(false)}>
                  取消
                </Button>
                <Button onClick={submit} disabled={!canSubmit || busy}>
                  {busy && <Loader2 className="size-3.5 animate-spin" />}
                  {busy ? "正在生效…" : "保存并生效"}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
