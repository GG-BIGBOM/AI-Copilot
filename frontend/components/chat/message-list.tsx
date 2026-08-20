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
import type { AnswerMode } from "@/lib/answer-mode";
import { BrandMark } from "@/components/brand-mark";
import { Citations } from "@/components/chat/citations";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { MessageActions } from "@/components/chat/message-actions";
import { ReasoningPanel } from "@/components/chat/reasoning-panel";
import {
  messageCitations,
  messageDownload,
  messageImages,
  messageReasoning,
  messageText,
  messageTools,
  type CopilotUIMessage,
} from "@/lib/chat-types";

export function MessageList({
  messages,
  status,
  mode,
  onRegenerate,
}: {
  messages: CopilotUIMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
  /** 详解档要等得久得多，等待文案得说清楚，不能干晾着 */
  mode?: AnswerMode;
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
            // 「答错了，我来改」要拿这一轮的提问当键。往前找最近的一条用户消息，
            // 而不是 index-1——重新生成之后中间可能夹着别的东西
            const prevUser = messages
              .slice(0, index)
              .findLast((x) => x.role === "user");
            return (
              <Message
                key={m.id}
                message={m}
                question={prevUser ? messageText(prevUser) : undefined}
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
                <span className="shimmer shimmer-duration-2400">
                {mode === "deep" ? "正在深入分析" : "正在理解问题"}
              </span>
              {mode === "deep" && (
                // kimi-k2.6 是推理模型：它先在心里打三千字草稿，正文首字要一分钟。
                // 不说这一句，用户只会以为卡住了
                <span className="text-muted-foreground">详解档要想得久一些，请稍候</span>
              )}
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
  question,
  isStreaming,
  onRegenerate,
}: {
  message: CopilotUIMessage;
  /** 这一轮用户问的那句话，传给「答错了，我来改」 */
  question?: string;
  isStreaming: boolean;
  onRegenerate?: () => void;
}) {
  const text = messageText(message);
  const reasoning = messageReasoning(message);
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
      {/* 草稿排在最前：详解档的那几十秒里，它是页面上**唯一**在动的东西 */}
      <ReasoningPanel text={reasoning} hasAnswer={Boolean(text)} isStreaming={isStreaming} />

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
          {!isStreaming && (
            <MessageActions text={text} question={question} onRegenerate={onRegenerate} />
          )}
        </div>
      </div>
    </article>
  );
}
