"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function Composer({
  onSend,
  onStop,
  busy,
  draft,
  onDraftChange,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  draft: string;
  onDraftChange: (text: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [rows, setRows] = useState(1);

  // 输入框自适应高度伸缩，最多 200px
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    setRows(1);
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    onDraftChange("");
  }

  return (
    <div className="w-full bg-gradient-to-t from-background via-background to-transparent pb-[env(safe-area-inset-bottom)] pt-2">
      <div className="mx-auto flex max-w-[52rem] flex-col gap-1.5 px-4 pb-3">
        {/* 输入卡片 — 克制圆角、轻 border、无花哨 glow */}
        <div
          className="relative flex flex-col rounded-[20px] border border-border/80 bg-background p-2 transition-all duration-200 focus-within:border-foreground/20 focus-within:shadow-[var(--shadow-subtle)]"
        >
          <Textarea
            ref={ref}
            rows={rows}
            value={draft}
            placeholder="问一个旺店通问题…"
            title="Enter 发送，Shift + Enter 换行"
            className="max-h-[200px] min-h-[48px] resize-none border-0 bg-transparent px-3 py-2.5 text-sm leading-relaxed shadow-none focus-visible:ring-0 placeholder:text-muted-foreground/50"
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={(e) => {
              // 中文输入法选词时会触发 Enter，isComposing 为真时不提交
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />

          <div className="flex items-center justify-end px-2 pt-0.5 pb-0.5">
            {busy ? (
              <Button
                type="button"
                size="icon"
                variant="destructive"
                className="size-8 rounded-full transition-transform active:scale-95"
                onClick={onStop}
                title="停止生成"
                aria-label="停止生成"
              >
                <Square className="size-3.5 fill-current" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={submit}
                disabled={!draft.trim()}
                title="发送问题"
                aria-label="发送问题"
                className="size-8 rounded-full bg-foreground text-background transition-all active:scale-95 disabled:opacity-20 hover:opacity-80"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>

        {/* 底部免责声明 */}
        <div className="flex items-center justify-between px-2 text-[11px] text-muted-foreground/50">
          <span>AI 回答基于知识库生成，请以最新系统设置为准</span>
          <span className="hidden sm:inline">Enter 发送 · Shift+Enter 换行</span>
        </div>
      </div>
    </div>
  );
}
