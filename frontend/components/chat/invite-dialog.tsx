"use client";

/**
 * 邀请码：管理员自助生成，发给要开账号的同事。
 *
 * 原来只能命令行 `copilot invite -n 3`，要登服务器。
 *
 * ⚠️ 这个入口**只对管理员显示**（`user.is_admin`）。摆一个点了就 403 的
 * 菜单项比不摆更糟——用户会以为自己该有这个权限，然后来问为什么用不了。
 * 第一个管理员由命令行指定：`uv run copilot admin <邮箱>`。
 */

import { useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Check, Copy, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError, type InviteState } from "@/lib/api";
import { cn } from "@/lib/utils";

const POPUP =
  "fixed left-1/2 top-[14vh] z-[60] w-[min(460px,calc(100vw-2rem))] -translate-x-1/2 " +
  "max-h-[72vh] overflow-y-auto rounded-2xl border border-border bg-popover p-5 " +
  "text-popover-foreground outline-hidden transition-[opacity,scale] duration-200 " +
  "ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] " +
  "data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

function CodeRow({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <li className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-surface-subtle">
      <code className="flex-1 font-mono text-[13px] tracking-wider text-foreground">{code}</code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard.writeText(code);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
        className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
        aria-label={`复制邀请码 ${code}`}
      >
        {copied ? <Check className="size-3 text-success" /> : <Copy className="size-3" />}
        {copied ? "已复制" : "复制"}
      </button>
    </li>
  );
}

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, setState] = useState<InviteState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 打开时拉一次。在渲染期比对 open 而不是写 effect——effect 里同步 setState
  // 会触发级联渲染，React 19 的规则直接判错
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setError(null);
      setBusy(true);
      api
        .invites()
        .then(setState)
        .catch((e) => setError(e instanceof ApiError ? e.message : "拉取失败"))
        .finally(() => setBusy(false));
    }
  }

  async function generate(count: number) {
    setBusy(true);
    setError(null);
    try {
      setState(await api.createInvites(count));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-[60] bg-black/25 transition-opacity duration-150 data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <Dialog.Popup className={POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
          <Dialog.Title className="text-base font-semibold text-foreground">邀请码</Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            发给要开账号的同事，注册时填。每个码<span className="font-medium text-foreground">只能用一次</span>。
          </Dialog.Description>

          <div className="mt-4 flex items-center gap-2">
            <Button onClick={() => generate(1)} disabled={busy}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              生成 1 个
            </Button>
            <Button variant="outline" onClick={() => generate(5)} disabled={busy}>
              生成 5 个
            </Button>
            {state && (
              <span className="ml-auto text-[13px] text-muted-foreground">
                未使用 {state.unused} 个
              </span>
            )}
          </div>

          {error && (
            <p className="mt-3 text-[13px] text-destructive" role="alert">
              {error}
            </p>
          )}

          <div className="mt-3">
            {!state ? (
              <p className="py-6 text-center text-[13px] text-muted-foreground">正在载入…</p>
            ) : state.codes.length === 0 ? (
              <p className="py-6 text-center text-[13px] text-muted-foreground">
                还没有未使用的邀请码，点上面生成。
              </p>
            ) : (
              <ul className={cn("-mx-2 space-y-px", busy && "opacity-60")}>
                {state.codes.map((c) => (
                  <CodeRow key={c} code={c} />
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4 flex justify-end">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
