"use client";

/**
 * Smart Trace —— Agent 的工作过程 + 方案下载入口（UI_OPTIMIZATION_SPEC §14）。
 *
 * 放在正文**上方**。Agent 一轮可能检索好几次再生成方案，十几秒没有任何输出
 * 是常态，让用户看见「正在检索知识库」比盯着空白转圈安心得多。
 *
 * 做完之后**自己收起来**，只留一行摘要——过程是过程，答案才是要读的东西。
 * 用户手动展开过就听用户的，不再自动折叠。
 *
 * ⚠️ 步骤名是后端给的中文标签（见 agent/runner.py 的 TOOL_LABELS），
 * 这里不做第二份映射表，也**不显示工具返回的原始内容**——那是给模型看的。
 */

import { useState } from "react";
import { Check, ChevronRight, Download, TriangleAlert } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ToolStep } from "@/lib/chat-types";

export function AgentTrace({
  steps,
  isStreaming = false,
  citationCount = 0,
}: {
  steps: ToolStep[];
  isStreaming?: boolean;
  /** 收起后的摘要要说「参考了几条知识」，数字来自真实的引用片段 */
  citationCount?: number;
}) {
  const [manuallyExpanded, setManuallyExpanded] = useState<boolean | null>(null);

  if (!steps.length) return null;

  const failed = steps.some((s) => s.failed);
  const running = isStreaming || steps.some((s) => !s.done && !s.failed);

  const expanded = manuallyExpanded ?? running;

  // 收起后那行只报**已经拿得到的事实**：参考了几条知识、跑了几步。
  // 规范里还画了「· 3.6s」，但要拿到耗时得在渲染期读时钟，
  // React 编译器的纯度规则不允许，为一个装饰性数字不值得绕这一圈
  const summary = running
    ? "正在分析"
    : citationCount > 0
      ? `已参考 ${citationCount} 条知识内容`
      : `已完成 ${steps.length} 个步骤`;

  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setManuallyExpanded(!expanded)}
        aria-expanded={expanded}
        className={cn(
          "-mx-1.5 flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[13px] transition-colors",
          running ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        )}
      >
        {running ? (
          <BrandMark className="size-4 text-bronze" thinking />
        ) : failed ? (
          <TriangleAlert className="size-3.5 text-destructive" />
        ) : (
          <Check className="size-3.5 text-muted-foreground" />
        )}
        <span className={cn(running && "shimmer shimmer-duration-2400")}>{summary}</span>
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground/60 transition-transform duration-150",
            expanded && "rotate-90",
          )}
        />
      </button>

      {expanded && (
        <ol className="mt-1.5 space-y-1.5 border-l border-border-subtle pl-3.5">
          {steps.map((s) => (
            <li
              key={s.id}
              className={cn(
                "flex items-baseline gap-2 text-[13px]",
                s.failed ? "text-destructive" : s.done ? "text-muted-foreground" : "text-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "relative top-[-1px] size-1.5 shrink-0 rounded-full",
                  s.failed
                    ? "bg-destructive"
                    : s.done
                      ? "bg-muted-foreground/40"
                      : "bg-bronze trace-breathe",
                )}
              />
              <span>
                {s.name}
                {s.failed ? "失败，已换个方式继续" : s.done ? "" : "…"}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * 方案下载。
 *
 * ⚠️ 用 `<a download>` 而不是 fetch + blob：下载要带上 HttpOnly cookie 做鉴权
 * （接口会校验这份方案是不是你的），普通链接跳转天然带 cookie，
 * 而 fetch 得显式 `credentials: "include"` 再自己造一个 blob URL——
 * 多写十几行，还会把整个 xlsx 读进内存。
 */
export function DownloadCard({ url, name }: { url: string; name: string }) {
  return (
    <a
      href={`${API_BASE}${url}`}
      download={name}
      className="mt-4 inline-flex items-center gap-2.5 rounded-lg border border-border bg-surface px-3 py-2 text-[13px] no-underline transition-colors hover:bg-surface-subtle"
    >
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-bronze-soft text-bronze-strong">
        <Download className="size-3.5" />
      </span>
      <span className="flex flex-col leading-tight">
        <span className="font-medium text-foreground">{name}</span>
        <span className="mt-0.5 text-[11px] text-muted-foreground">
          点击下载 · 请人工复核后交付
        </span>
      </span>
    </a>
  );
}
