"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  Check,
  Copy,
  Loader2,
  RotateCcw,
} from "lucide-react";

import { AgentTrace, DownloadCard } from "@/components/chat/agent-trace";
import { Citations } from "@/components/chat/citations";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { Button } from "@/components/ui/button";
import {
  messageCitations,
  messageDownload,
  messageImages,
  messageText,
  messageTools,
  type CopilotUIMessage,
} from "@/lib/chat-types";

/* ─── 空状态场景入口（轻量行式，不是 Card） ─── */
const SUGGESTIONS = [
  { title: "京东电子面单模板怎么设置？", desc: "面单开通、模板绑定与打印控件" },
  { title: "退货入库的操作流程是什么？", desc: "退货验货、良品与不良品入库" },
  { title: "怎么配置短信策略与规则？", desc: "发货通知、催付短信与触发规则" },
  { title: "对账单生成异常怎么排查？", desc: "结算费用差异与核销异常" },
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

  /* ═══════════════════════════════════════════════
     空状态 Hero — 极简 + Dot Grid 背景
     ═══════════════════════════════════════════════ */
  if (messages.length === 0) {
    return (
      <div className="relative flex flex-1 flex-col items-center justify-center px-4 py-8 text-center overflow-hidden">
        {/* Dot Grid + radial 遮罩 */}
        <div className="absolute inset-0 dot-grid-bg" />
        <div className="absolute inset-0 bg-radial-[ellipse_at_center] from-background via-background/80 to-transparent" />

        <div className="relative z-10 flex flex-col items-center gap-8 max-w-lg">
          {/* 品牌 Icon + 标题 */}
          <div className="space-y-3 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-foreground/[0.06] text-foreground/80">
              <span className="text-xl">◈</span>
            </div>
            <div className="space-y-1">
              <h2 className="text-xl font-semibold tracking-tight text-foreground">
                旺店通助手
              </h2>
              <p className="text-muted-foreground text-sm">
                今天想解决什么问题？
              </p>
            </div>
          </div>

          {/* 场景入口 — 轻量行式，不是卡片 */}
          <div className="w-full space-y-1">
            {SUGGESTIONS.map((s) => (
              <button
                key={s.title}
                type="button"
                onClick={() => onPick(s.title)}
                className="group flex w-full items-start gap-3 rounded-xl px-4 py-3 text-left transition-colors hover:bg-foreground/[0.04]"
              >
                <span className="mt-0.5 flex size-1.5 shrink-0 rounded-full bg-foreground/20 group-hover:bg-foreground/40 transition-colors" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground group-hover:text-foreground/90">
                    {s.title}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {s.desc}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  /* ═══════════════════════════════════════════════
     消息列表
     ═══════════════════════════════════════════════ */
  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="relative flex-1 overflow-y-auto"
    >
      <div className="mx-auto flex max-w-[52rem] flex-col gap-6 px-4 py-6">
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
          <div className="flex items-center gap-3 text-muted-foreground text-sm py-3 pl-11">
            <Loader2 className="size-3.5 animate-spin shrink-0" />
            <span className="text-xs">正在检索知识库…</span>
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
          className="fixed bottom-28 right-8 z-30 size-8 rounded-full bg-background border-border/60 hover:bg-accent"
          style={{ boxShadow: "var(--shadow-subtle)" }}
          title="回到底部"
          aria-label="回到底部"
        >
          <ArrowDown className="size-3.5" />
        </Button>
      )}
    </div>
  );
}

/* ─── 复制按钮 ─── */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground/60 hover:text-muted-foreground hover:bg-foreground/[0.04] transition-colors"
      onClick={copy}
      title={copied ? "已复制" : "复制"}
      aria-label={copied ? "已复制" : "复制回答"}
    >
      {copied ? (
        <>
          <Check className="size-3 text-emerald-600 dark:text-emerald-400" />
          <span className="text-emerald-600 dark:text-emerald-400">已复制</span>
        </>
      ) : (
        <>
          <Copy className="size-3" />
          <span>复制</span>
        </>
      )}
    </button>
  );
}

/* ═══════════════════════════════════════════════
   消息气泡
   • 用户：浅色 muted 气泡（不再是重黑色）
   • AI：文档式排版，无气泡包裹
   ═══════════════════════════════════════════════ */
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
  const images = messageImages(message);
  // M7：Agent 的工具调用过程与方案下载。普通问答走直路，这两样都是空的
  const tools = messageTools(message);
  const download = messageDownload(message);

  /* ─── 用户消息：右对齐 muted 气泡 ─── */
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-tr-md bg-muted px-4 py-2.5 text-sm text-foreground leading-relaxed">
          <p className="break-words whitespace-pre-wrap">{text}</p>
        </div>
      </div>
    );
  }

  /* ─── AI 消息：文档式排版，无气泡 ─── */
  return (
    <div className="group flex gap-3 items-start">
      {/* AI 头像 */}
      <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground/[0.06] text-foreground/70 mt-0.5">
        <span className="text-sm">◈</span>
      </div>

      <div className="flex flex-col gap-1.5 min-w-0 flex-1 max-w-none">
        {/* 正文（无边框、无背景，纯文档流） */}
        <div className="text-foreground">
          {/* 工具调用过程放正文上方：Agent 十几秒没输出是常态，
              让用户看到「正在检索知识库…」比盯着空白转圈安心 */}
          <AgentTrace steps={tools} />
          <MarkdownContent content={text} images={images} isStreaming={isStreaming} />
          <Citations citations={citations} />
          {download && <DownloadCard url={download.url} name={download.name} />}
        </div>

        {/* 操作工具栏 — 悬停才可见 */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <CopyButton text={text} />
          {onRegenerate && (
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground/60 hover:text-muted-foreground hover:bg-foreground/[0.04] transition-colors"
              onClick={onRegenerate}
              title="重新生成"
              aria-label="重新生成"
            >
              <RotateCcw className="size-3" />
              <span>重新生成</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
