"use client";

import { BookOpen, ExternalLink } from "lucide-react";

import type { Citation } from "@/lib/api";

/**
 * 引用来源卡片列表（ChatGPT Search / 权威知识库引用风格）。
 * 每条可直接点击跳转语雀知识库原文。
 */
export function Citations({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;

  return (
    <div className="mt-4 space-y-2.5 border-t border-border/60 pt-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
        <BookOpen className="size-3.5 text-primary/80" />
        <span>参考知识库来源 ({citations.length})</span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {citations.map((c) => {
          const cardContent = (
            <div className="group relative flex items-start gap-2.5 rounded-xl border border-border/70 bg-card/70 p-2.5 text-xs text-foreground transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:bg-accent/40 hover:shadow-xs">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 font-mono text-[11px] font-bold text-primary">
                {c.n}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-foreground group-hover:text-primary transition-colors">
                  {c.title}
                </p>
                {c.heading && (
                  <p className="truncate text-[11px] text-muted-foreground mt-0.5">
                    {c.heading}
                  </p>
                )}
              </div>
              {c.url && (
                <ExternalLink className="size-3.5 shrink-0 text-muted-foreground opacity-60 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:opacity-100" />
              )}
            </div>
          );

          if (c.url) {
            return (
              <a
                key={c.n}
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                title={`${c.title}${c.heading ? ` · ${c.heading}` : ""}\n点击在语雀知识库中查看原文`}
                className="block no-underline focus:outline-none"
              >
                {cardContent}
              </a>
            );
          }

          return (
            <div
              key={c.n}
              title={`${c.title}${c.heading ? ` · ${c.heading}` : ""}`}
              className="block"
            >
              {cardContent}
            </div>
          );
        })}
      </div>
    </div>
  );
}
