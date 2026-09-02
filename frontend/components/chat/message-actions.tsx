"use client";

/**
 * 回答下面的操作条（UI_OPTIMIZATION_SPEC §17）。
 *
 * 平时接近透明，hover 或键盘聚焦才完全显形——用 opacity 而不是 display:none，
 * 后者会把这些按钮从 Tab 顺序里整个抹掉。
 *
 * 只放**真的有用的**几个。
 *
 * 「赞 / 踩」原来是缺的，理由写在这里：后端没有反馈接口，做一个点了什么都
 * 不会发生的按钮比少一个按钮更糟（§38.4）。**M11 P2 把接口做了**，
 * 所以按钮补上——而且它写进的是 `request_trace` 那张表，点一次踩，
 * 当时检索到几块、调了什么工具、rerank 多少分全都能翻出来。
 *
 * ⚠️ **没有 traceId 就不显示这两个按钮**（老会话、或者流没跑完）。
 * 宁可少一个按钮，也不要一个点下去悄悄失败的按钮——那正是当初不做它的理由。
 *
 * 「答错了」的条件是**这一轮有提问**（`question`），不是「有来源」。
 * 订正存的是问答对，键是那句提问；至于这次的答案是查出来的还是编出来的，
 * 反倒不影响——**恰恰是没查到来源、答得最离谱的那次，最需要人来改**。
 * （旧版按「有带链接的来源」判，因为那时改的是语雀原文，没有目标就无从盖起。）
 */

import { useRef, useState } from "react";
import {
  Check,
  Copy,
  Download,
  FileText,
  PencilLine,
  Printer,
  RotateCcw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

import { VerifyDialog } from "@/components/chat/verify-dialog";
import {
  downloadTextFile,
  exportFilename,
  toMarkdown,
} from "@/lib/export-answer";
import type { AnswerImage } from "@/lib/chat-types";
import {
  api,
  FEEDBACK_REASONS,
  type Citation,
  type FeedbackReason,
  type FeedbackVote,
} from "@/lib/api";

const ACTION =
  "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-muted-foreground transition-colors hover:bg-surface-subtle hover:text-foreground";

export function MessageActions({
  text,
  question,
  traceId,
  initialVote,
  citations = [],
  images = [],
  onRegenerate,
}: {
  text: string;
  /** 导出 Markdown 时要一起写进「## 来源」那一段 */
  citations?: readonly Citation[];
  /** 导出 Markdown 时用来把 `[图N]` 换成真的图片语法 */
  images?: readonly AnswerImage[];
  /** 这一轮用户问的那句话。订正以它为键——没有它就无从订正 */
  question?: string;
  /** 这一轮在 request_trace 里的行号。没有就不显示赞/踩 */
  traceId?: string | null;
  /** 之前已经点过的（翻历史时后端带回来的）。刷新后按钮要保持按下的样子 */
  initialVote?: FeedbackVote | null;
  onRegenerate?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState(false);
  // 打印时要找到这一条回答对应的那个 <article>。用 closest 而不是往下传 ref：
  // 传 ref 要穿过 MessageList → Message 两层，而这里只需要「我在谁里面」
  const rootRef = useRef<HTMLDivElement>(null);
  const [correcting, setCorrecting] = useState(false);
  const [vote, setVote] = useState<FeedbackVote | null>(initialVote ?? null);
  // 点了踩之后展开的原因条。选完就收起来
  const [askingReason, setAskingReason] = useState(false);

  function copy() {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  /**
   * ⭐ **先改本地状态，再发请求，失败了退回去。**
   * 点一下要立刻有反应——这个按钮的全部价值就是「顺手标一下」，
   * 让人等一次网络往返，他下次就不点了。
   *
   * 失败**不弹报错**：一次没记上的反馈，代价远小于在用户脸上弹一个
   * 他既不关心也无法处理的红框。按钮弹回原样已经说明了没成功。
   */
  async function send(next: FeedbackVote, reason?: FeedbackReason) {
    if (!traceId) return;
    const before = vote;
    setVote(next);
    setAskingReason(next === "down" && !reason);
    try {
      await api.sendFeedback({ traceId, vote: next, reason });
    } catch {
      setVote(before);
      setAskingReason(false);
    }
  }

  function exportMarkdown() {
    downloadTextFile(
      exportFilename(question ?? "", "md"),
      toMarkdown({ question, answer: text, citations, images }, window.location.origin),
      "text/markdown",
    );
    setExporting(false);
  }

  /**
   * ⭐ **PDF 走浏览器打印，不做服务端渲染**（理由见 `lib/export-answer.ts` 文件头：
   * 服务器 1.6GB 装不下渲染器、还缺中文字体；而 plan.md 2.4 那条许可红线
   * 已经因为 AGPL 拒过一个 PDF 库）。
   *
   * 做法是给 body 和这一条回答各打一个标记，剩下的交给 `@media print`。
   * ⚠️ **`afterprint` 必须清理标记。** 不清的话用户取消打印之后，页面上
   * 那两个属性还在——下一次 Ctrl+P 就会只打印这一条回答，而他根本没点导出。
   */
  function exportPdf() {
    const article = rootRef.current?.closest("article");
    if (!article) return;
    setExporting(false);
    document.body.setAttribute("data-printing", "");
    article.setAttribute("data-print-target", "");
    const cleanup = () => {
      document.body.removeAttribute("data-printing");
      article.removeAttribute("data-print-target");
      window.removeEventListener("afterprint", cleanup);
    };
    window.addEventListener("afterprint", cleanup);
    window.print();
  }

  if (!text) return null;

  return (
    <>
    <div
      ref={rootRef}
      // ⚠️ 这一条不进 PDF：导出的东西里不该有「导出」按钮
      data-no-print=""
      className="-mx-1.5 mt-3 flex items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100"
    >
      <button type="button" className={ACTION} onClick={copy} aria-label="复制回答">
        {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
        {copied ? "已复制" : "复制"}
      </button>

      <div className="relative">
        <button
          type="button"
          className={ACTION}
          onClick={() => setExporting((v) => !v)}
          aria-expanded={exporting}
          aria-label="导出这条回答"
        >
          <Download className="size-3.5" />
          导出
        </button>
        {exporting && (
          <div
            className="absolute bottom-full left-0 z-10 mb-1 min-w-[9rem] overflow-hidden rounded-lg border border-border bg-surface py-1"
            style={{ boxShadow: "var(--shadow-floating)" }}
          >
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-foreground hover:bg-surface-subtle"
              onClick={exportMarkdown}
            >
              <FileText className="size-3.5 text-muted-foreground" />
              Markdown
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-foreground hover:bg-surface-subtle"
              onClick={exportPdf}
            >
              <Printer className="size-3.5 text-muted-foreground" />
              PDF
            </button>
          </div>
        )}
      </div>

      {traceId && (
        <>
          {/* 已经点过的那一侧要保持亮着——刷新之后也是。
              不然用户会以为上次那一下没点上，然后再点一次 */}
          <button
            type="button"
            className={`${ACTION} ${vote === "up" ? "text-success" : ""}`}
            onClick={() => send("up")}
            aria-pressed={vote === "up"}
            aria-label="这条答得好"
          >
            <ThumbsUp className="size-3.5" />
          </button>
          <button
            type="button"
            className={`${ACTION} ${vote === "down" ? "text-warning" : ""}`}
            onClick={() => send("down")}
            aria-pressed={vote === "down"}
            aria-label="这条答得不好"
          >
            <ThumbsDown className="size-3.5" />
          </button>
        </>
      )}

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
            traceId={traceId}
          />
        </>
      )}
    </div>

    {/* ⭐ 原因条**不放在上面那个 opacity-0 的容器里**：那一条是 hover 才显形的，
        而这里是用户刚点完踩、正在等着选原因——鼠标一移开就消失的话，
        他根本选不中。它也不是必答题，不选就是一条没带原因的差评，照样算数。 */}
    {askingReason && (
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px]">
        <span className="text-muted-foreground">哪里不好？</span>
        {FEEDBACK_REASONS.map((r) => (
          <button
            key={r.value}
            type="button"
            className="rounded-full border border-border px-2 py-0.5 text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
            onClick={() => send("down", r.value)}
          >
            {r.label}
          </button>
        ))}
        <button
          type="button"
          className="px-1 text-muted-foreground hover:text-foreground"
          onClick={() => setAskingReason(false)}
        >
          跳过
        </button>
      </div>
    )}
    </>
  );
}
