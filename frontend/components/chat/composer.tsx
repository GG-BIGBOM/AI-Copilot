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

  // 跟着内容长高，最多 6 行。先归零再读 scrollHeight，否则删字时收不回去。
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
    setRows(1);
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    onDraftChange("");
  }

  return (
    <div className="bg-background border-t">
      <div className="mx-auto flex max-w-3xl items-end gap-2 px-4 py-3">
        <Textarea
          ref={ref}
          rows={rows}
          value={draft}
          // 390px 的手机屏放不下完整提示，会被截成「…Shift+En」。短的就够了，
          // Enter 发送本来也是聊天框的通例
          placeholder="问点什么…"
          title="Enter 发送，Shift+Enter 换行"
          className="max-h-42 min-h-10 resize-none"
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            // 中文输入法选词时也会触发 Enter。isComposing 为真说明用户还在选字，
            // 这时候发出去就会把半截拼音当问题提交——中文界面必须挡这一下。
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <Button type="button" size="icon" variant="secondary" onClick={onStop} title="停止生成">
            <Square className="size-4" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            onClick={submit}
            disabled={!draft.trim()}
            title="发送"
          >
            <ArrowUp className="size-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
