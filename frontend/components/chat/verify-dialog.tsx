"use client";

/**
 * 答案订正：**这条答得不对 → 我直接改 → 下次就照我改的答。**
 *
 * 它替掉了原来那个「勘误」对话框。那个要选一篇语雀文档、把整篇正文重写一遍，
 * 为了改一句话重写一整篇——重到没人会用，而没人用的订正功能等于没有。
 *
 * 所以这里做的是三件事，一件不多：
 *
 *   1. 把**现在这条回答**原样放进一个可编辑的框
 *   2. 用户改哪儿改哪儿，点保存
 *   3. 服务端当场进索引，回执里说清楚**生效没有**
 *
 * ⚠️ 「已保存」和「已生效」是两回事，这里必须分开说。只说保存成功的话，
 * 用户改完再问一遍发现答案没变，只会认定这个功能是假的（后端 `applied` 字段）。
 */

import { useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";

const POPUP =
  "fixed left-1/2 top-[8vh] z-[60] flex max-h-[84vh] w-[min(680px,calc(100vw-2rem))] " +
  "-translate-x-1/2 flex-col rounded-2xl border border-border bg-popover p-5 " +
  "text-popover-foreground outline-hidden transition-[opacity,scale] duration-200 " +
  "ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] " +
  "data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

export function VerifyDialog({
  open,
  onOpenChange,
  question,
  answer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 用户当初问的那句话。它是这条订正的键——下次问到同类问题就命中它 */
  question: string;
  /** 模型这次给的答案，作为编辑起点 */
  answer: string;
}) {
  const [draft, setDraft] = useState(answer);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // 每次打开都用**这次**的答案重置草稿。在渲染期比对 open 而不是写 effect：
  // effect 里同步 setState 会触发级联渲染，React 19 的规则直接判错
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setDraft(answer);
      setError(null);
      setDone(null);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.saveVerified({ question, answer: draft });
      setDone(r.note);
      // 生效了才自动关。没生效的话留在原地，让那句解释有人看见
      if (r.applied) setTimeout(() => onOpenChange(false), 1200);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const unchanged = draft.trim() === answer.trim();

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-[60] bg-black/25 transition-opacity duration-150 data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <Dialog.Popup className={POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
          <Dialog.Title className="text-base font-semibold text-foreground">
            改成正确的答案
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            改完保存，下次问到
            <span className="mx-1 rounded-sm bg-surface-muted px-1.5 py-0.5 font-medium text-foreground">
              {question.length > 40 ? `${question.slice(0, 40)}…` : question}
            </span>
            这类问题，就照你改的答。
          </Dialog.Description>

          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="mt-4 min-h-[16rem] flex-1 resize-none rounded-lg border border-border bg-surface-subtle px-3 py-2.5 font-mono text-[13px] leading-relaxed text-foreground outline-hidden transition-colors focus:border-ring focus:bg-background"
            aria-label="正确的答案"
          />

          <p className="mt-2 text-[12px] text-muted-foreground">
            支持 Markdown。这条订正对
            <span className="font-medium text-foreground">所有人</span>
            生效，也随时可以撤销。
          </p>

          {error && (
            <p className="mt-3 text-[13px] text-destructive" role="alert">
              {error}
            </p>
          )}
          {done && (
            <p className="mt-3 flex items-center gap-1.5 text-[13px] text-success" role="status">
              <Check className="size-3.5" />
              {done}
            </p>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button onClick={save} disabled={busy || unchanged || !draft.trim()}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              {unchanged ? "还没有改动" : "保存并生效"}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
