import { BookOpenText, ExternalLink } from "lucide-react";

import type { Citation } from "@/lib/api";

/**
 * 引用来源列表。每条可点开跳语雀原文。
 *
 * 这里**不判**答案是不是「知识库暂无此内容」——后端在那种情况下根本不会
 * 下发 `data-citations` 片段。防幻觉那条规则收在服务端一处，前端再判一次
 * 只会制造两份可能不一致的实现。
 */
export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  return (
    <div className="mt-3.5 space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center gap-1.5 text-muted-foreground text-xs font-medium">
        <BookOpenText className="size-3.5" />
        <span>引用知识库来源 ({citations.length})</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {citations.map((c) => {
          const content = (
            <div className="group flex max-w-full items-center gap-1.5 rounded-lg border border-border/80 bg-background/80 px-2.5 py-1.5 text-xs text-foreground/90 shadow-2xs transition-all hover:border-primary/40 hover:bg-accent/40">
              <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
                {c.n}
              </span>
              <span className="truncate font-medium">{c.title}</span>
              {c.heading && (
                <>
                  <span className="text-muted-foreground/60 text-[10px]">/</span>
                  <span className="truncate text-muted-foreground text-[11px]">{c.heading}</span>
                </>
              )}
              {c.url && (
                <ExternalLink className="size-3 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
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
                title={`${c.title}${c.heading ? ` · ${c.heading}` : ""}\n点击查看语雀原文`}
                className="inline-block max-w-full no-underline focus:outline-none"
              >
                {content}
              </a>
            );
          }

          return (
            <div
              key={c.n}
              title={`${c.title}${c.heading ? ` · ${c.heading}` : ""}`}
              className="inline-block max-w-full"
            >
              {content}
            </div>
          );
        })}
      </div>
    </div>
  );
}
