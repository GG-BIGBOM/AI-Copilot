"use client";

/**
 * 处理进度（详解档等待期间）。
 *
 * **它解决的是「详解太慢」。** 实测详解档那个推理模型：
 *
 *     第一个正文字   8 ~ 60 秒
 *
 * 只收正文的话，那几十秒页面上一个字都没有——用户的原话是
 * 「详解没有回答内容」。所以这段时间由后端报**自己的阶段**：
 * 正在理解问题 / 正在检索知识库 / 已找到 N 条相关资料 / 正在整理答案。
 *
 * ⚠️⚠️ **这里显示的不是模型的推理草稿。** 2026-09-03 之前它确实是——
 * 后端把 `reasoning_content` 逐字转发过来。那段草稿是模型在**完整上下文**
 * 里自言自语，而完整上下文含 system prompt、检索到的材料原文（含用户私有
 * 文档）、以及材料里可能夹带的注入内容。防幻觉的三道闸门管的是**正文**，
 * 草稿那一路一个字都管不到。现在后端只发写死的常量
 * （`backend/src/copilot/api/progress.py`），SSE 通道没变。
 *
 * ⚠️ **默认折叠，正文一到就自动收起。** 进度是过程，答案才是要读的东西。
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

export function ReasoningPanel({
  text,
  /** 正文已经开始了。它一变 true，进度就自动收起 */
  hasAnswer,
  isStreaming,
}: {
  text: string;
  hasAnswer: boolean;
  isStreaming: boolean;
}) {
  // 用户点过之后就听用户的，不再自动开合
  const [pinned, setPinned] = useState<boolean | null>(null);
  const open = pinned ?? (isStreaming && !hasAnswer);

  if (!text) return null;

  return (
    <div className="content-grid">
      <div className="mb-3">
        <button
          type="button"
          onClick={() => setPinned(!open)}
          aria-expanded={open}
          className="inline-flex items-center gap-1 rounded-md py-0.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronRight
            className={cn("size-3 transition-transform duration-200", open && "rotate-90")}
          />
          <span className={cn(isStreaming && !hasAnswer && "shimmer shimmer-duration-2400")}>
            {isStreaming && !hasAnswer ? "正在处理" : "处理过程"}
          </span>
        </button>

        {open && (
          <div className="mt-1.5 border-l-2 border-border pl-3 text-[13px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
            {text}
          </div>
        )}
      </div>
    </div>
  );
}
