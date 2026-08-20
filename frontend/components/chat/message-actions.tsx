"use client";

/**
 * 回答下面的操作条（UI_OPTIMIZATION_SPEC §17）。
 *
 * 平时接近透明，hover 或键盘聚焦才完全显形——用 opacity 而不是 display:none，
 * 后者会把这些按钮从 Tab 顺序里整个抹掉。
 *
 * 只放**真的有用的**几个。规范里还提到「赞 / 踩」，但后端没有反馈接口，
 * 做一个点了什么都不会发生的按钮比少一个按钮更糟（§38.4）。
 *
 * 「答错了」的条件是**这一轮有提问**（`question`），不是「有来源」。
 * 订正存的是问答对，键是那句提问；至于这次的答案是查出来的还是编出来的，
 * 反倒不影响——**恰恰是没查到来源、答得最离谱的那次，最需要人来改**。
 * （旧版按「有带链接的来源」判，因为那时改的是语雀原文，没有目标就无从盖起。）
 */

import { useState } from "react";
import { Check, Copy, PencilLine, RotateCcw } from "lucide-react";

import { VerifyDialog } from "@/components/chat/verify-dialog";

const ACTION =
  "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-muted-foreground transition-colors hover:bg-surface-subtle hover:text-foreground";

export function MessageActions({
  text,
  question,
  onRegenerate,
}: {
  text: string;
  /** 这一轮用户问的那句话。订正以它为键——没有它就无从订正 */
  question?: string;
  onRegenerate?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [correcting, setCorrecting] = useState(false);

  function copy() {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (!text) return null;

  return (
    <div className="-mx-1.5 mt-3 flex items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100">
      <button type="button" className={ACTION} onClick={copy} aria-label="复制回答">
        {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
        {copied ? "已复制" : "复制"}
      </button>

      {onRegenerate && (
        <button type="button" className={ACTION} onClick={onRegenerate} aria-label="重新生成回答">
          <RotateCcw className="size-3.5" />
          重新生成
        </button>
      )}

      {question && (
        <>
          <button
            type="button"
            className={ACTION}
            onClick={() => setCorrecting(true)}
            aria-label="这条答错了，改成正确的答案"
          >
            <PencilLine className="size-3.5" />
            答错了，我来改
          </button>
          <VerifyDialog
            open={correcting}
            onOpenChange={setCorrecting}
            question={question}
            answer={text}
          />
        </>
      )}
    </div>
  );
}
