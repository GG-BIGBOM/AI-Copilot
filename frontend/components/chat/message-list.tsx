"use client";

import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Check,
  Copy,
  FileText,
  Loader2,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  User as UserIcon,
} from "lucide-react";

import { Citations } from "@/components/chat/citations";
import { Button } from "@/components/ui/button";
import { messageCitations, messageText, type CopilotUIMessage } from "@/lib/chat-types";

const SUGGESTIONS = [
  {
    icon: FileText,
    category: "面单与打印",
    title: "京东电子面单模板怎么设置？",
  },
  {
    icon: RotateCcw,
    category: "仓储与售后",
    title: "退货入库的操作流程是什么？",
  },
  {
    icon: ShieldCheck,
    category: "策略配置",
    title: "怎么配置短信策略与规则？",
  },
];

export function MessageList({
  messages,
  status,
  onPick,
}: {
  messages: CopilotUIMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
  onPick: (text: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // 每次消息变化都滚到底。流式输出时 messages 会高频更新，
  // 所以依赖写成 messages 本身而不是 length——否则只在新增消息时滚一次，
  // 长答案生成到一半就滚不动了。
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-8 text-center animate-in fade-in duration-300">
        <div className="space-y-3 max-w-lg">
          <div className="mx-auto flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-md ring-8 ring-primary/10">
            <Sparkles className="size-7" />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight">问点旗舰版 ERP 的事</h2>
            <p className="text-muted-foreground text-xs sm:text-sm leading-relaxed">
              答案只来自语雀知识库，并附上可点开的原文出处。
              <br className="hidden sm:inline" />
              知识库里没有的，它会直说没有。
            </p>
          </div>
        </div>

        <div className="grid w-full max-w-lg gap-2.5 sm:grid-cols-1">
          {SUGGESTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.title}
                type="button"
                onClick={() => onPick(s.title)}
                className="group flex items-center justify-between rounded-xl border border-border/80 bg-card/60 p-3.5 text-left text-xs transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:bg-accent/40 hover:shadow-xs"
              >
                <div className="flex items-center gap-3">
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                    <Icon className="size-4" />
                  </div>
                  <div>
                    <span className="text-[10px] font-medium text-muted-foreground">
                      {s.category}
                    </span>
                    <p className="font-medium text-foreground text-xs">{s.title}</p>
                  </div>
                </div>
                <span className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 text-xs">
                  ↵
                </span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6">
        {messages.map((m) => (
          <Bubble key={m.id} message={m} />
        ))}
        {status === "submitted" && (
          <div className="flex items-center gap-2.5 text-muted-foreground text-xs py-2 px-1">
            <Loader2 className="size-4 animate-spin text-primary" />
            <span className="animate-pulse">正在检索知识库并分析匹配段落…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-6 text-muted-foreground hover:text-foreground opacity-60 hover:opacity-100 transition-opacity"
      onClick={copy}
      title={copied ? "已复制" : "复制回答"}
      aria-label={copied ? "已复制" : "复制回答"}
    >
      {copied ? <Check className="size-3 text-emerald-500" /> : <Copy className="size-3" />}
    </Button>
  );
}

function Bubble({ message }: { message: CopilotUIMessage }) {
  const isUser = message.role === "user";
  const text = messageText(message);
  const citations = messageCitations(message);

  if (isUser) {
    return (
      <div className="flex justify-end gap-2.5 items-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-xs sm:text-sm text-primary-foreground shadow-2xs leading-relaxed">
          <p className="break-words whitespace-pre-wrap">{text}</p>
        </div>
        <div className="hidden sm:flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary border border-primary/20 text-xs">
          <UserIcon className="size-3.5" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start gap-2.5 items-start group">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs mt-1">
        <Bot className="size-3.5" />
      </div>
      <div className="flex flex-col gap-1 max-w-[88%] sm:max-w-[85%]">
        <div className="rounded-2xl rounded-tl-sm border border-border/80 bg-muted/60 px-4 py-3 text-xs sm:text-sm text-foreground leading-relaxed shadow-2xs">
          {/* 后端吐的是纯文本，序号列表和「设置–策略设置」这类界面路径靠换行和空格
              表达结构。whitespace-pre-wrap 原样保留即可，不必为此引一个 markdown 渲染器。 */}
          <p className="break-words whitespace-pre-wrap">{text}</p>
          <Citations citations={citations} />
        </div>
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-1">
            <CopyButton text={text} />
          </div>
        </div>
      </div>
    </div>
  );
}
