"use client";

/**
 * 会话正文（UI_OPTIMIZATION_SPEC §12 / §13）。
 *
 * 两种消息两种排版：
 *   用户  右对齐的半气泡，最宽 ~600px
 *   AI    没有气泡的文档流——它是一份生成出来的方案，不是一条聊天记录
 *
 * 横向用 `.content-grid`：正文待在中间 820px 的列里，表格和截图可以撑到
 * 两侧（最宽 1080px）。用户气泡、正文、输入区用的是同一套栅格，中轴永远对齐。
 */

import { useEffect, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";

import { AgentTrace, DownloadCard } from "@/components/chat/agent-trace";
import { BrandMark } from "@/components/brand-mark";
import { Citations } from "@/components/chat/citations";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { MessageActions } from "@/components/chat/message-actions";
import {
  messageCitations,
  messageDownload,
  messageImages,
  messageText,
  messageTools,
  type CopilotUIMessage,
} from "@/lib/chat-types";

export function MessageList({
  messages,
  status,
  onRegenerate,
}: {
  messages: CopilotUIMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
  onRegenerate?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 用户往上翻看历史时，别把他一路拽回底部
  const stickToBottom = useRef(true);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = distanceToBottom < 80;
    setShowScrollBottom(distanceToBottom > 200);
  }

  function scrollToBottom() {
    stickToBottom.current = true;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }

  useEffect(() => {
    if (!stickToBottom.current) return;
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages, status]);

  return (
    // 外层只负责给「回到底部」当定位锚：它要贴在可视区底部，
    // 而不是跟着内容一起滚走
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={containerRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-8 px-4 py-8 sm:px-6">
          {messages.map((m, index) => {
            const isLast = index === messages.length - 1;
            return (
              <Message
                key={m.id}
                message={m}
                isStreaming={isLast && status === "streaming"}
                onRegenerate={isLast && status === "ready" ? onRegenerate : undefined}
              />
            );
          })}

          {/* 已经发出去、模型还没吐第一个字。Agent 这一段可能十几秒没输出 */}
          {status === "submitted" && (
            <div className="content-grid">
              <div className="flex items-center gap-1.5 text-[13px] text-foreground">
                <BrandMark className="size-4 text-bronze" thinking />
                <span className="shimmer shimmer-duration-2400">正在理解问题</span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {showScrollBottom && (
        <button
          type="button"
          onClick={scrollToBottom}
          className="absolute bottom-4 left-1/2 z-10 flex size-8 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-surface text-muted-foreground transition-colors hover:text-foreground"
          style={{ boxShadow: "var(--shadow-floating)" }}
          title="回到底部"
          aria-label="回到底部"
        >
          <ArrowDown className="size-3.5" />
        </button>
      )}
    </div>
  );
}

function Message({
  message,
  isStreaming,
  onRegenerate,
}: {
  message: CopilotUIMessage;
  isStreaming: boolean;
  onRegenerate?: () => void;
}) {
  const text = messageText(message);
  const citations = messageCitations(message);
  const images = messageImages(message);
  // M7：Agent 的工具调用过程与方案下载。普通问答走直路，这两样都是空的
  const tools = messageTools(message);
  const download = messageDownload(message);

  /* ─── 用户消息：右对齐半气泡 ─── */
  if (message.role === "user") {
    return (
      <div className="content-grid">
        <div className="flex justify-end">
          <div className="max-w-[min(38rem,88%)] rounded-xl bg-surface-muted px-3.5 py-2.5 text-[15px] leading-relaxed text-foreground">
            <p className="whitespace-pre-wrap break-words">{text}</p>
          </div>
        </div>
      </div>
    );
  }

  /* ─── AI 消息：文档式排版，没有气泡 ─── */
  return (
    <article className="group">
      {tools.length > 0 && (
        <div className="content-grid">
          <div>
            <AgentTrace
              steps={tools}
              isStreaming={isStreaming}
              citationCount={citations.length}
            />
          </div>
        </div>
      )}

      <MarkdownContent
        content={text}
        images={images}
        citations={citations}
        isStreaming={isStreaming}
      />

      <div className="content-grid">
        <div>
          {download && <DownloadCard url={download.url} name={download.name} />}
          <Citations citations={citations} />
          {!isStreaming && <MessageActions text={text} onRegenerate={onRegenerate} />}
        </div>
      </div>
    </article>
  );
}
