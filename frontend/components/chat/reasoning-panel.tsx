"use client";

/**
 * 推理草稿（详解档）。
 *
 * **它解决的是「详解太慢」。** 实测详解档那个推理模型：
 *
 *     第一个草稿字   1 秒
 *     第一个正文字   8 ~ 60 秒
 *
 * 模型其实一秒就开口了，只是说的是草稿。以前只收正文，那几十秒页面上
 * 一个字都没有——用户的原话是「详解没有回答内容」。把草稿显出来，
 * 等待就从"死机"变成"看得见它在想"。
 *
 * ⚠️ **默认折叠，而且和正文分开。** 草稿里全是「材料里好像没提到…」
 * 「也许是设置-快递管理？」这类自我推翻的话。混进正文就成了一条会骗人的答案，
 * 比空白更糟。所以只在**还没出正文**的时候自动展开——那时它是唯一的进度，
 * 正文一到就自动收起，让位给真正的答案。
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

export function ReasoningPanel({
  text,
  /** 正文已经开始了。它一变 true，草稿就自动收起 */
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
            {isStreaming && !hasAnswer ? "正在思考" : "思考过程"}
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
