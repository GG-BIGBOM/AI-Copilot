"use client";

/**
 * 会话搜索（Ctrl / Cmd + K）。
 *
 * 替掉了侧边栏里那个常驻的搜索输入框：它一年用不上两次，却天天占着
 * 一整行宽度（UI_OPTIMIZATION_SPEC §9.5）。
 *
 * 只搜**已经在左侧列表里的会话标题**——没有新接口，不做假功能。
 */

import { useMemo, useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { MessageSquare, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/lib/api";

export function ConversationSearch({
  open,
  conversations,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  conversations: ConversationSummary[];
  onOpenChange: (open: boolean) => void;
  onPick: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? conversations.filter((c) => (c.title || "").toLowerCase().includes(q))
      : conversations;
    return list.slice(0, 50);
  }, [conversations, query]);

  // 每次打开都从头开始，别接着上次的关键词和光标位置。
  // 在渲染期比对上一次的 open 而不是写 effect：effect 里同步 setState
  // 会触发级联渲染，React 19 的规则直接判错
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setQuery("");
      setCursor(0);
    }
  }

  // 光标不能停在被过滤掉的那一行上
  const active = Math.min(cursor, Math.max(results.length - 1, 0));

  function choose(id: string) {
    onOpenChange(false);
    onPick(id);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/25 transition-opacity duration-150 data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <Dialog.Popup
          className={cn(
            "fixed left-1/2 top-[18vh] z-50 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2",
            "overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground",
            "outline-hidden transition-[opacity,scale] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
            "data-starting-style:scale-[0.98] data-starting-style:opacity-0",
            "data-ending-style:scale-[0.98] data-ending-style:opacity-0",
          )}
          style={{ boxShadow: "var(--shadow-floating)" }}
          initialFocus={inputRef}
        >
          <Dialog.Title className="sr-only">搜索历史对话</Dialog.Title>

          <div className="flex items-center gap-2.5 border-b border-border-subtle px-3.5">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setCursor(0);
              }}
              onKeyDown={(e) => {
                // 中文输入法选词时的方向键 / 回车不该走这套导航
                if (e.nativeEvent.isComposing) return;
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setCursor((i) => Math.min(i + 1, results.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setCursor((i) => Math.max(i - 1, 0));
                } else if (e.key === "Enter" && results[active]) {
                  e.preventDefault();
                  choose(results[active].id);
                }
              }}
              placeholder="搜索历史对话…"
              aria-label="搜索历史对话"
              className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
            />
            <kbd className="hidden shrink-0 rounded-sm border border-border-subtle px-1.5 py-0.5 text-[11px] text-muted-foreground sm:block">
              Esc
            </kbd>
          </div>

          {results.length === 0 ? (
            <p className="px-4 py-8 text-center text-[13px] text-muted-foreground">
              {conversations.length === 0 ? "还没有历史对话" : "没有匹配的对话"}
            </p>
          ) : (
            <ul className="max-h-[46vh] overflow-y-auto p-1.5">
              {results.map((c, i) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => choose(c.id)}
                    onMouseMove={() => setCursor(i)}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors",
                      i === active
                        ? "bg-surface-muted text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    <MessageSquare className="size-3.5 shrink-0 opacity-50" />
                    <span className="truncate">{c.title || "未命名对话"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
