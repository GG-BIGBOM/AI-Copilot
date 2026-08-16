import { ExternalLink } from "lucide-react";

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
    <div className="mt-3 space-y-1.5 border-t pt-3">
      <p className="text-muted-foreground text-xs font-medium">来源</p>
      <ol className="space-y-1">
        {citations.map((c) => {
          const label = c.heading ? `${c.title} · ${c.heading}` : c.title;
          return (
            <li key={c.n} className="flex gap-2 text-xs leading-relaxed">
              <span className="text-muted-foreground shrink-0 tabular-nums">[{c.n}]</span>
              {c.url ? (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-foreground text-muted-foreground inline-flex min-w-0 items-start gap-1 underline decoration-dotted underline-offset-2"
                >
                  <span className="min-w-0 break-words">{label}</span>
                  <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden />
                </a>
              ) : (
                <span className="text-muted-foreground min-w-0 break-words">{label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
