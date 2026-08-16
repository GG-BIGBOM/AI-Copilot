"use client";

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";

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
  isStreaming = false,
}: {
  content: string;
  isStreaming?: boolean;
}) {
  return (
    <div className="chat-prose max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
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
        {content}
      </ReactMarkdown>
      {isStreaming && <span className="chat-cursor" />}
    </div>
  );
});
