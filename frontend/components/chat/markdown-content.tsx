"use client";

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";
import type { AnswerImage } from "@/lib/chat-types";

// 模型在步骤末尾写的配图引用，如「1. 进入【设置】[图1]」。
// 容忍中间多打一个空格——模型偶尔会写成 [图 1]，漏掉的话用户就看见一个裸标记。
const IMAGE_REF_RE = /\[图\s*(\d{1,2})\]/g;

/**
 * 把答案里的 `[图1]` 换成 Markdown 图片语法，交给 react-markdown 正常渲染。
 *
 * **图号对不上的一律删掉。** 模型偶尔会引用一个材料里不存在的编号
 * （本轮只有 3 张图却写了 [图5]）。留着它，用户看到的是一个意义不明的
 * 方括号；换成图，就是配了一张错的截图。两者都比直接抹掉差。
 */
function inlineImages(content: string, images: AnswerImage[]): string {
  if (images.length === 0) return content.replace(IMAGE_REF_RE, "");
  const byNumber = new Map(images.map((img) => [img.n, img.url]));
  return content.replace(IMAGE_REF_RE, (_, n: string) => {
    const url = byNumber.get(Number(n));
    return url ? `![图${n}](${url})` : "";
  });
}

function CodeBlock({
  language,
  value,
}: {
  language: string;
  value: string;
}) {
  const [copied, setCopied] = useState(false);

  function copyCode() {
    if (!value) return;
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="relative my-3 overflow-hidden rounded-xl border border-border/80 bg-neutral-950 text-neutral-100 shadow-sm">
      <div className="flex items-center justify-between border-b border-neutral-800 bg-neutral-900/90 px-3.5 py-1.5 text-xs text-neutral-400">
        <div className="flex items-center gap-1.5 font-mono text-[11px]">
          <Terminal className="size-3.5" />
          <span>{language || "code"}</span>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={copyCode}
          className="h-6 gap-1 px-2 text-[11px] text-neutral-300 hover:bg-neutral-800 hover:text-white"
        >
          {copied ? (
            <>
              <Check className="size-3 text-emerald-400" />
              <span className="text-emerald-400">已复制</span>
            </>
          ) : (
            <>
              <Copy className="size-3" />
              <span>复制代码</span>
            </>
          )}
        </Button>
      </div>
      <div className="overflow-x-auto p-3.5 font-mono text-xs leading-relaxed">
        <pre className="!m-0 !p-0">
          <code>{value}</code>
        </pre>
      </div>
    </div>
  );
}

export const MarkdownContent = memo(function MarkdownContent({
  content,
  images = [],
  isStreaming = false,
}: {
  content: string;
  images?: AnswerImage[];
  isStreaming?: boolean;
}) {
  return (
    <div className="chat-prose max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img({ src, alt }) {
            if (typeof src !== "string" || !src) return null;
            // 地址是根相对路径 /images/…：开发时要拼上后端的 8000，
            // 线上同源留空，由 nginx 直接发
            const href = src.startsWith("/") ? `${API_BASE}${src}` : src;
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                title="点击查看大图"
                className="mt-2 mb-3 block w-fit max-w-full no-underline"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={href}
                  alt={alt || "操作截图"}
                  loading="lazy"
                  className="max-h-96 max-w-full rounded-xl border border-border/80 object-contain shadow-2xs transition-opacity hover:opacity-90"
                />
              </a>
            );
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
          table({ children }) {
            return (
              <div className="my-2 overflow-x-auto rounded-lg border border-border/80">
                <table>{children}</table>
              </div>
            );
          },
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary underline underline-offset-4 hover:opacity-80 transition-opacity"
              >
                {children}
              </a>
            );
          },
        }}
      >
        {inlineImages(content, images)}
      </ReactMarkdown>
      {isStreaming && <span className="chat-cursor" />}
    </div>
  );
});
