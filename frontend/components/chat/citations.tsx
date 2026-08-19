"use client";

/**
 * Smart Citation —— 正文里的行内角标 + 答案末尾的来源清单（UI_OPTIMIZATION_SPEC §15）。
 *
 * 引用是这个产品的信任基础，所以它要**看得见**；但答案才是主角，所以它又要
 * 足够安静：角标是一个小方块，来源清单只列标题，细节收在悬浮预览里。
 *
 * ⚠️ 后端目前只给 title / heading / url —— 没有正文摘录。所以预览卡片展示的是
 * 「这条知识在文档里的位置」，而不是编出来的一段摘要。要真正的原文片段，
 * 得先让 `Citation.to_dict()` 带上 snippet 字段。
 *
 * 没有引用数据时**什么都不渲染**：模型答「知识库暂无此内容」时后端根本不发
 * 这个片段，前端更不能凭空造一个空的「来源」区块出来。
 */

import { PreviewCard } from "@base-ui/react/preview-card";
import { ArrowUpRight } from "lucide-react";

import { POPUP_LAYER } from "@/lib/layers";
import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/api";

const POPUP =
  "z-50 w-[min(400px,calc(100vw-2rem))] origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-3.5 text-popover-foreground outline-hidden transition-[transform,opacity] duration-150 ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

/** 预览卡片的内容。行内角标和末尾清单共用一份，两处的信息不会打架 */
function SourcePreview({ citation }: { citation: Citation }) {
  return (
    <>
      <div className="flex items-start gap-2">
        <span className="mt-px flex size-5 shrink-0 items-center justify-center rounded-sm bg-bronze-soft text-[11px] font-semibold tabular-nums text-bronze-strong">
          {citation.n}
        </span>
        <span className="text-sm font-medium leading-snug text-foreground">{citation.title}</span>
      </div>

      {citation.heading && (
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{citation.heading}</p>
      )}

      {citation.url && (
        <span className="mt-3 flex items-center gap-1 text-[13px] font-medium text-bronze-strong">
          查看原文
          <ArrowUpRight className="size-3.5" />
        </span>
      )}
    </>
  );
}

/**
 * 正文里的行内角标。
 *
 * hover、键盘聚焦、点击都能出预览——只做 hover 的话键盘用户永远看不到它。
 */
export function CitationChip({ citation }: { citation: Citation }) {
  const label = `来源 ${citation.n}：${citation.title}${citation.heading ? ` · ${citation.heading}` : ""}`;

  const triggerClass = cn(
    "relative -top-px mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-sm px-1",
    "align-baseline text-[11px] font-medium tabular-nums no-underline",
    "bg-surface-muted text-muted-foreground transition-colors",
    "hover:bg-bronze-soft hover:text-bronze-strong data-popup-open:bg-bronze-soft data-popup-open:text-bronze-strong",
  );

  // 有原文链接就是一个真链接，没有就退成一个只负责唤出预览的按钮。
  // 写成两个分支而不是展开一个联合类型的 props 对象，TS 才收得住
  const trigger = citation.url ? (
    <PreviewCard.Trigger
      className={triggerClass}
      aria-label={label}
      href={citation.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {citation.n}
    </PreviewCard.Trigger>
  ) : (
    <PreviewCard.Trigger className={triggerClass} aria-label={label} render={<button type="button" />}>
      {citation.n}
    </PreviewCard.Trigger>
  );

  return (
    <PreviewCard.Root>
      {trigger}
      <PreviewCard.Portal>
        <PreviewCard.Positioner className={POPUP_LAYER} sideOffset={8} collisionPadding={12}>
          <PreviewCard.Popup className={POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
            <SourcePreview citation={citation} />
          </PreviewCard.Popup>
        </PreviewCard.Positioner>
      </PreviewCard.Portal>
    </PreviewCard.Root>
  );
}

/** 来源清单里的一行。有 url 就是链接，没有就是纯预览触发器 */
function SourceRowTrigger({ citation }: { citation: Citation }) {
  const className = cn(
    "group -mx-1.5 flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[13px] no-underline transition-colors",
    "text-muted-foreground hover:bg-surface-subtle hover:text-foreground data-popup-open:bg-surface-subtle",
  );

  const inner = (
    <>
      <span className="flex size-4 shrink-0 items-center justify-center rounded-xs bg-surface-muted text-[10px] font-medium tabular-nums text-muted-foreground">
        {citation.n}
      </span>
      <span className="truncate">{citation.title}</span>
      {citation.url && (
        <ArrowUpRight className="size-3 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground" />
      )}
    </>
  );

  return citation.url ? (
    <PreviewCard.Trigger
      className={className}
      href={citation.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {inner}
    </PreviewCard.Trigger>
  ) : (
    <PreviewCard.Trigger className={className} render={<button type="button" />}>
      {inner}
    </PreviewCard.Trigger>
  );
}

/** 答案末尾的来源清单：只列标题，细节留给预览卡片 */
export function Citations({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;

  return (
    <section className="mt-6 border-t border-border-subtle pt-3">
      <h3 className="text-[11px] font-medium text-muted-foreground/70">
        来源 · {citations.length}
      </h3>

      <ul className="mt-1.5 space-y-px">
        {citations.map((c) => (
          <li key={c.n}>
            <PreviewCard.Root>
              <SourceRowTrigger citation={c} />
              <PreviewCard.Portal>
                <PreviewCard.Positioner className={POPUP_LAYER} sideOffset={8} collisionPadding={12}>
                  <PreviewCard.Popup
                    className={POPUP}
                    style={{ boxShadow: "var(--shadow-floating)" }}
                  >
                    <SourcePreview citation={c} />
                  </PreviewCard.Popup>
                </PreviewCard.Positioner>
              </PreviewCard.Portal>
            </PreviewCard.Root>
          </li>
        ))}
      </ul>
    </section>
  );
}
