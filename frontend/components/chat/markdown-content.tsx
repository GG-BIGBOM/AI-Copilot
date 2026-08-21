"use client";

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";

import { CitationChip } from "@/components/chat/citations";
import { API_BASE } from "@/lib/api";
import { fallbackImages, inlineImages } from "@/lib/image-rendering";
import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/api";
import type { AnswerImage } from "@/lib/chat-types";

// 正文里的引用角标 [1] [2]。转成一个假锚点交给 a 渲染器，
// 那边再换成可悬停、可聚焦的角标组件
const CITE_REF_RE = /\[(\d{1,2})\]/g;
const CITE_HREF = "#copilot-cite-";

/**
 * 只替换代码围栏**外面**的文本。
 *
 * 答案里出现 ```json 代码块时，块内的 [1] 是数据的一部分，
 * 换成引用角标就把代码改错了。
 */
function replaceOutsideCode(text: string, run: (segment: string) => string): string {
  return text
    .split(/(```[\s\S]*?```|```[\s\S]*$)/g)
    .map((segment) => (segment.startsWith("```") ? segment : run(segment)))
    .join("");
}

/**
 * 把 `[1]` 换成指向假锚点的链接。
 *
 * 引用数据是在正文流完之后才到的（data-citations 片段），所以流式过程中
 * 这些角标就先以纯文本待着——**编号对不上的不动**，宁可留一个方括号，
 * 也不做一个点开是空的角标。
 */
function inlineCitations(content: string, citations: Citation[]): string {
  if (citations.length === 0) return content;
  const known = new Set(citations.map((c) => c.n));
  return replaceOutsideCode(content, (segment) =>
    segment.replace(CITE_REF_RE, (raw, n: string) =>
      known.has(Number(n)) ? `[${n}](${CITE_HREF}${n})` : raw,
    ),
  );
}

function CodeBlock({ language, value }: { language: string; value: string }) {
  const [copied, setCopied] = useState(false);

  function copyCode() {
    if (!value) return;
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="content-wide overflow-hidden rounded-lg border border-code-border bg-code text-code-foreground">
      <div className="flex items-center justify-between border-b border-code-border px-3 py-1.5">
        <span className="font-mono text-[11px] text-code-foreground/55">{language || "code"}</span>
        <button
          type="button"
          onClick={copyCode}
          className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] text-code-foreground/60 transition-colors hover:bg-white/8 hover:text-code-foreground"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <div className="overflow-x-auto p-3.5 font-mono text-[13px] leading-relaxed">
        <pre className="!m-0 !p-0">
          <code>{value}</code>
        </pre>
      </div>
    </div>
  );
}

/** ERP 截图。点开看大图，加载前先占好位置，别让下面的文字跳来跳去 */
function Screenshot({ src, alt }: { src: string; alt: string }) {
  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      title="点击查看大图"
      className="block w-fit max-w-full no-underline"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        className="max-h-[28rem] max-w-full rounded-lg border border-border-subtle bg-surface object-contain transition-opacity hover:opacity-90"
      />
    </a>
  );
}

/** hast 节点里只有一张图（可能夹着空白文本）—— 这种段落当成独立配图处理 */
function isLoneImage(node: unknown): boolean {
  const children = (node as { children?: { type: string; tagName?: string; value?: string }[] })
    ?.children;
  if (!children) return false;
  const meaningful = children.filter((c) => !(c.type === "text" && !c.value?.trim()));
  return meaningful.length === 1 && meaningful[0].tagName === "img";
}

export const MarkdownContent = memo(function MarkdownContent({
  content,
  images = [],
  citations = [],
  isStreaming = false,
}: {
  content: string;
  images?: AnswerImage[];
  citations?: Citation[];
  isStreaming?: boolean;
}) {
  const byNumber = new Map(citations.map((c) => [c.n, c]));
  // 图片先于正文到达；流结束前不能把“正文还没来得及写图号”误判成漏图。
  // 没有引用时也不兜底，避免 no-answer 回答挂上无关截图。
  const fallback =
    !isStreaming && citations.length > 0 ? fallbackImages(content, images) : [];

  // 地址是根相对路径 /images/…：开发时要拼上后端的 8000，线上同源留空，由 nginx 直接发
  const absolute = (src: string) => (src.startsWith("/") ? `${API_BASE}${src}` : src);

  return (
    <div className={cn("chat-prose content-grid", isStreaming && "is-streaming")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 只有一张图的段落 → 提成宽内容区的独立配图；
          // 步骤里夹带的小图仍然跟着正文走，不该跳出那一步的上下文
          p({ node, children }) {
            if (isLoneImage(node)) {
              return <figure className="content-wide">{children}</figure>;
            }
            return <p>{children}</p>;
          },
          img({ src, alt }) {
            if (typeof src !== "string" || !src) return null;
            return <Screenshot src={absolute(src)} alt={alt || "操作截图"} />;
          },
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || "");
            const isInline = !match && !String(children).includes("\n");

            if (isInline) {
              return (
                <code className={className} {...props}>
                  {children}
                </code>
              );
            }

            return (
              <CodeBlock
                language={match ? match[1] : ""}
                value={String(children).replace(/\n$/, "")}
              />
            );
          },
          // 代码块的外层 pre 由 CodeBlock 自己画，这里让它透明穿过去，
          // 否则会多套一层 pre 把 content-wide 的栅格定位吃掉
          pre({ children }) {
            return <>{children}</>;
          },
          table({ children }) {
            return (
              <div className="content-wide">
                <div className="table-scroll">
                  <table>{children}</table>
                </div>
              </div>
            );
          },
          a({ href, children }) {
            if (href?.startsWith(CITE_HREF)) {
              const citation = byNumber.get(Number(href.slice(CITE_HREF.length)));
              if (citation) return <CitationChip citation={citation} />;
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {inlineCitations(inlineImages(content, images), citations)}
      </ReactMarkdown>
      {fallback.length > 0 && (
        <details className="content-wide overflow-hidden rounded-lg border border-border-subtle bg-surface">
          <summary className="cursor-pointer select-none px-3.5 py-2.5 text-sm font-medium text-foreground marker:text-muted-foreground">
            参考材料里的操作截图
            <span className="ml-2 font-normal text-muted-foreground">{fallback.length} 张</span>
          </summary>
          <div className="grid gap-3 border-t border-border-subtle bg-surface-subtle p-3 sm:grid-cols-2">
            {fallback.map((image) => (
              <figure key={image.n} className="m-0 min-w-0">
                <Screenshot src={absolute(image.url)} alt={`操作截图 ${image.n}`} />
                <figcaption className="mt-1.5 text-xs text-muted-foreground">
                  图 {image.n}
                </figcaption>
              </figure>
            ))}
          </div>
        </details>
      )}
    </div>
  );
});
