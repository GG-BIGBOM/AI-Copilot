"use client";

/**
 * Smart Composer（UI_OPTIMIZATION_SPEC §11）。
 *
 * 全应用最重要的一个控件，所以它的规矩最死：
 *   Enter 发送 · Shift + Enter 换行 · **中文输入法选词时的 Enter 永远不发送**。
 *
 * 不放任何还没有真实功能的按钮（上传 / 模型选择 / 模式切换）——
 * 摆一个点了没反应的图标，比少一个功能更伤信任。
 */

import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";

import { cn } from "@/lib/utils";

export function Composer({
  onSend,
  onStop,
  busy,
  draft,
  onDraftChange,
  placeholder = "问一个旺店通相关问题……",
  autoFocus = false,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  draft: string;
  onDraftChange: (text: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // 输入框自适应高度伸缩，最多 200px；外框的视觉高度保持稳定
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    onDraftChange("");
  }

  return (
    <div>
      <div
        className={cn(
          "flex flex-col rounded-2xl border border-border bg-surface transition-colors duration-150",
          // 聚焦：青铜描边 + 一层极淡的青铜底光，不用亮蓝色 ring
          "focus-within:border-bronze-border focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--bronze),transparent_88%)]",
        )}
      >
        <textarea
          ref={ref}
          rows={1}
          value={draft}
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-label="输入你的问题"
          // text-base + md:text-sm：iOS 上小于 16px 的输入框一聚焦就会放大整页
          className="max-h-[200px] min-h-12 w-full resize-none bg-transparent px-3.5 pt-3 text-base leading-relaxed outline-none placeholder:text-muted-foreground/70 md:text-[15px]"
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            // ⭐ 中文输入法选词时会触发 Enter，isComposing 为真时绝不提交
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />

        <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5 pt-1">
          <span className="truncate pl-1 text-[11px] text-muted-foreground/60">
            Enter 发送 · Shift + Enter 换行
          </span>

          {busy ? (
            <button
              type="button"
              onClick={onStop}
              title="停止生成"
              aria-label="停止生成"
              className="flex size-8 shrink-0 items-center justify-center rounded-md bg-destructive/12 text-destructive transition-colors hover:bg-destructive/20"
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!draft.trim()}
              title="发送问题"
              aria-label="发送问题"
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-md transition-colors",
                draft.trim()
                  ? "bg-primary text-primary-foreground hover:bg-[color-mix(in_oklch,var(--primary),var(--background)_12%)]"
                  : "cursor-not-allowed bg-surface-muted text-muted-foreground/50",
              )}
            >
              <ArrowUp className="size-4" />
            </button>
          )}
        </div>
      </div>

      <p className="mt-2 px-1 text-center text-[11px] text-muted-foreground/55">
        AI 回答基于知识库生成，请以最新系统设置为准
      </p>
    </div>
  );
}
