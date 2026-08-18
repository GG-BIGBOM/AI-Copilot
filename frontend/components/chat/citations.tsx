"use client";

import { useState } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";

import type { Citation } from "@/lib/api";

/**
 * 引用来源 — 可折叠列表（默认收起）。
 *
 * 设计原则：答案是一级信息，来源是二级信息。
 * 默认只显示「▸ 使用了 N 个知识来源」，点击展开列表。
 */
export function Citations({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!citations || citations.length === 0) return null;

  return (
    <div className="mt-4 pt-3 border-t border-border/40">
      {/* 折叠触发器 */}
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors group"
      >
        <ChevronRight
          className={`size-3.5 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
        />
        <span className="font-medium">
          使用了 {citations.length} 个知识来源
        </span>
      </button>

      {/* 展开的来源列表 */}
      {expanded && (
        <ul className="mt-2 space-y-1 pl-5">
          {citations.map((c) => {
            const inner = (
              <div className="flex items-center gap-2 py-1.5 text-xs min-w-0 group/item">
                <span className="flex size-5 shrink-0 items-center justify-center rounded bg-foreground/[0.05] font-mono text-[10px] font-semibold text-muted-foreground">
                  {c.n}
                </span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium text-foreground group-hover/item:text-foreground/80 truncate block">
                    {c.title}
                  </span>
                  {c.heading && (
                    <span className="text-[11px] text-muted-foreground truncate block">
                      {c.heading}
                    </span>
                  )}
                </div>
                {c.url && (
                  <ExternalLink className="size-3 shrink-0 text-muted-foreground/40 group-hover/item:text-muted-foreground transition-colors" />
                )}
              </div>
            );

            if (c.url) {
              return (
                <li key={c.n}>
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`${c.title}${c.heading ? ` · ${c.heading}` : ""}\n点击查看原文`}
                    className="block rounded-lg px-2 -mx-2 no-underline hover:bg-foreground/[0.03] transition-colors"
                  >
                    {inner}
                  </a>
                </li>
              );
            }

            return (
              <li key={c.n} className="px-2 -mx-2">
                {inner}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
