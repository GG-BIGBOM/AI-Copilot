"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Sparkles, Square } from "lucide-react";

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

  // 输入框自适应高度伸缩，最多 7 行
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    setRows(1);
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    onDraftChange("");
  }

  return (
    <div className="w-full bg-gradient-to-t from-background via-background/95 to-transparent pb-[env(safe-area-inset-bottom)] pt-2">
      <div className="mx-auto flex max-w-3xl flex-col gap-1.5 px-4 pb-3">
        {/* ChatGPT 风格悬浮大圆角胶囊输入卡片 */}
        <div className="relative flex flex-col rounded-3xl border border-border/80 bg-muted/40 p-2 shadow-xs transition-all duration-200 focus-within:border-primary/50 focus-within:bg-background focus-within:ring-4 focus-within:ring-primary/10 focus-within:shadow-md">
          <Textarea
            ref={ref}
            rows={rows}
            value={draft}
            placeholder="问点旗舰版 ERP 的事（如：退货入库流程、面单打印配置、策略规则）…"
            title="Enter 发送，Shift + Enter 换行"
            className="max-h-44 min-h-12 resize-none border-0 bg-transparent px-3 py-2 text-xs sm:text-sm leading-relaxed shadow-none focus-visible:ring-0 placeholder:text-muted-foreground/60"
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={(e) => {
              // 中文输入法选词时会触发 Enter，isComposing 为真时不提交
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />

          <div className="flex items-center justify-between px-2 pt-1 pb-0.5">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/80">
              <Sparkles className="size-3 text-primary" />
              <span className="hidden sm:inline">语雀企业知识库检索</span>
            </div>

            <div className="flex items-center gap-2">
              {busy ? (
                <Button
                  type="button"
                  size="icon"
                  variant="destructive"
                  className="size-8 rounded-full shadow-xs transition-transform active:scale-95 animate-pulse"
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
                  className="size-8 rounded-full bg-primary text-primary-foreground shadow-xs transition-all active:scale-95 disabled:opacity-30 hover:opacity-90"
                >
                  <ArrowUp className="size-4" />
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* 底部免责声明与快捷键提示 */}
        <div className="flex items-center justify-between px-2 text-[11px] text-muted-foreground/60">
          <span>AI 回答基于知识库生成，请以 ERP 最新系统设置为准</span>
          <span className="hidden sm:inline">Enter 发送 · Shift + Enter 换行</span>
        </div>
      </div>
    </div>
  );
}
