"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  Bot,
  Check,
  Coins,
  Copy,
  FileText,
  Loader2,
  Package,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  User as UserIcon,
} from "lucide-react";

import { Citations } from "@/components/chat/citations";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { Button } from "@/components/ui/button";
import { messageCitations, messageText, type CopilotUIMessage } from "@/lib/chat-types";

const SUGGESTIONS = [
  {
    icon: FileText,
    category: "面单与打印",
    title: "京东电子面单模板怎么设置？",
    desc: "查看电子面单开通、模板绑定与打印控件配置",
  },
  {
    icon: Package,
    category: "仓储与售后",
    title: "退货入库的操作流程是什么？",
    desc: "了解退货验货、良品/不良品入库及单据流转步骤",
  },
  {
    icon: ShieldCheck,
    category: "策略配置",
    title: "怎么配置短信策略与规则？",
    desc: "配置发货通知、催付短信与自动化触发规则",
  },
  {
    icon: Coins,
    category: "财务与对账",
    title: "对账单生成异常怎么排查？",
    desc: "排查结算费用差异、单据同步状态与核销异常",
  },
];

const QUICK_TAGS = [
  "退货入库流程",
  "京东面单配置",
  "短信策略规则",
  "库存盘点步骤",
  "对账单核销",
];

export function MessageList({
  messages,
  status,
  onPick,
  onRegenerate,
}: {
  messages: CopilotUIMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
  onPick: (text: string) => void;
  onRegenerate?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  // 监听滚动位置，判断是否展示「回到底部」按钮
  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBottom(distanceToBottom > 150);
  }

  function scrollToBottom() {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }

  // 消息变化时自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages, status]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-8 text-center animate-in fade-in duration-300">
        <div className="space-y-3.5 max-w-xl">
          <div className="mx-auto flex size-14 items-center justify-center rounded-2xl bg-gradient-to-tr from-primary to-primary/80 text-primary-foreground shadow-md ring-8 ring-primary/10">
            <Sparkles className="size-7" />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-bold tracking-tight text-foreground">
              今天能帮您解答什么？
            </h2>
            <p className="text-muted-foreground text-xs sm:text-sm leading-relaxed">
              基于企业语雀知识库，精准解答 ERP 业务流程、单据策略与配置规范。
              <br className="hidden sm:inline" />
              知识库里没有的，助手会明确告知。
            </p>
          </div>
        </div>

        {/* 2x2 场景卡片矩阵 */}
        <div className="grid w-full max-w-2xl gap-3 sm:grid-cols-2">
          {SUGGESTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.title}
                type="button"
                onClick={() => onPick(s.title)}
                className="group relative flex flex-col justify-between rounded-2xl border border-border/80 bg-card/60 p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:bg-accent/40 hover:shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                    <Icon className="size-4.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <span className="text-[11px] font-semibold text-primary/80 tracking-wide">
                      {s.category}
                    </span>
                    <p className="font-semibold text-foreground text-xs sm:text-sm mt-0.5 group-hover:text-primary transition-colors">
                      {s.title}
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1 line-clamp-1">
                      {s.desc}
                    </p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* 快捷标签胶囊 */}
        <div className="flex flex-wrap items-center justify-center gap-2 max-w-xl">
          <span className="text-[11px] text-muted-foreground font-medium">热门探索：</span>
          {QUICK_TAGS.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => onPick(tag)}
              className="rounded-full border border-border/70 bg-muted/40 px-3 py-1 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground hover:bg-accent/50 transition-colors"
            >
              #{tag}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="relative flex-1 overflow-y-auto"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6">
        {messages.map((m, index) => {
          const isLast = index === messages.length - 1;
          const isStreaming = isLast && status === "streaming";
          return (
            <Bubble
              key={m.id}
              message={m}
              isStreaming={isStreaming}
              onRegenerate={isLast ? onRegenerate : undefined}
            />
          );
        })}

        {status === "submitted" && (
          <div className="flex items-center gap-3 text-muted-foreground text-xs py-3 px-2 rounded-xl bg-muted/30 border border-border/50 animate-pulse">
            <Loader2 className="size-4 animate-spin text-primary shrink-0" />
            <span>正在检索语雀知识库并分析匹配段落…</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* 悬浮回到底部按钮 */}
      {showScrollBottom && (
        <Button
          type="button"
          size="icon"
          variant="outline"
          onClick={scrollToBottom}
          className="fixed bottom-28 right-8 z-30 size-9 rounded-full shadow-md bg-background/90 backdrop-blur-xs border-border/80 hover:bg-accent"
          title="回到底部"
          aria-label="回到底部"
        >
          <ArrowDown className="size-4" />
        </Button>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
      onClick={copy}
      title={copied ? "已复制" : "复制回答"}
      aria-label={copied ? "已复制" : "复制回答"}
    >
      {copied ? (
        <>
          <Check className="size-3 text-emerald-500" />
          <span className="text-emerald-500 text-[11px]">已复制</span>
        </>
      ) : (
        <>
          <Copy className="size-3" />
          <span className="text-[11px]">复制</span>
        </>
      )}
    </Button>
  );
}

function Bubble({
  message,
  isStreaming = false,
  onRegenerate,
}: {
  message: CopilotUIMessage;
  isStreaming?: boolean;
  onRegenerate?: () => void;
}) {
  const isUser = message.role === "user";
  const text = messageText(message);
  const citations = messageCitations(message);

  if (isUser) {
    return (
      <div className="flex justify-end gap-3 items-start animate-in fade-in duration-200">
        <div className="max-w-[85%] rounded-3xl rounded-tr-md bg-primary px-4.5 py-3 text-xs sm:text-sm text-primary-foreground shadow-2xs leading-relaxed">
          <p className="break-words whitespace-pre-wrap">{text}</p>
        </div>
        <div className="hidden sm:flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary border border-primary/20 text-xs font-semibold">
          <UserIcon className="size-4" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start gap-3 items-start group animate-in fade-in duration-200">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs mt-0.5">
        <Bot className="size-4" />
      </div>

      <div className="flex flex-col gap-2 min-w-0 flex-1 max-w-[92%] sm:max-w-[88%]">
        <div className="rounded-3xl rounded-tl-md border border-border/80 bg-muted/40 px-4.5 py-3.5 text-foreground leading-relaxed shadow-2xs">
          <MarkdownContent content={text} isStreaming={isStreaming} />
          <Citations citations={citations} />
        </div>

        {/* 消息底部操作工具栏 */}
        <div className="flex items-center justify-between px-1 text-muted-foreground text-xs">
          <div className="flex items-center gap-1">
            <CopyButton text={text} />
            {onRegenerate && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={onRegenerate}
                title="重新生成"
                aria-label="重新生成"
              >
                <RotateCcw className="size-3" />
                <span className="text-[11px]">重新生成</span>
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
