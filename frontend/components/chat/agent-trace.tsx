"use client";

/**
 * M7：Agent 的工具调用过程 + 方案下载入口。
 *
 * 放在正文**上方**。用户看到「正在检索知识库…」比看着一个空白的转圈安心得多——
 * Agent 一轮可能检索好几次、再生成方案，十几秒没有任何输出是常态。
 *
 * 有意做得很轻：一行小字加一个图标。这是过程信息，不是答案；
 * 做成大卡片会把真正要读的内容挤下去。
 */

import { CheckCircle2, Download, Loader2, XCircle } from "lucide-react";

import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ToolStep } from "@/lib/chat-types";

export function AgentTrace({ steps }: { steps: ToolStep[] }) {
  if (!steps.length) return null;
  return (
    <div className="mb-2 flex flex-col gap-1">
      {steps.map((s) => {
        const Icon = s.failed ? XCircle : s.done ? CheckCircle2 : Loader2;
        return (
          <div
            key={s.id}
            className={cn(
              "flex items-center gap-1.5 text-[11px]",
              s.failed ? "text-destructive/80" : "text-muted-foreground",
            )}
          >
            <Icon className={cn("size-3 shrink-0", !s.done && !s.failed && "animate-spin")} />
            <span>
              {s.name}
              {s.failed ? "（失败，已换个方式继续）" : s.done ? "" : "…"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * 下载按钮。
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
      className="mt-2 inline-flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-muted"
    >
      <span className="flex size-6 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Download className="size-3.5" />
      </span>
      <span className="flex flex-col leading-tight">
        <span>{name}</span>
        <span className="text-[10px] font-normal text-muted-foreground">
          点击下载 · 请人工复核后交付
        </span>
      </span>
    </a>
  );
}
