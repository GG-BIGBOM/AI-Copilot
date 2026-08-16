"use client";

import { useEffect, useRef } from "react";

import { Citations } from "@/components/chat/citations";
import { cn } from "@/lib/utils";
import { messageCitations, messageText, type CopilotUIMessage } from "@/lib/chat-types";

const SUGGESTIONS = [
  "京东电子面单模板怎么设置？",
  "退货入库的操作流程是什么？",
  "怎么配置短信策略？",
];

export function MessageList({
  messages,
  status,
  onPick,
}: {
  messages: CopilotUIMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
  onPick: (text: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // 每次消息变化都滚到底。流式输出时 messages 会高频更新，
  // 所以依赖写成 messages 本身而不是 length——否则只在新增消息时滚一次，
  // 长答案生成到一半就滚不动了。
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4 text-center">
        <div className="space-y-2">
          <h2 className="text-lg font-medium">问点旗舰版 ERP 的事</h2>
          <p className="text-muted-foreground text-sm">
            答案只来自语雀知识库，并附上可点开的原文出处。
            <br />
            知识库里没有的，它会直说没有。
          </p>
        </div>
        <div className="flex w-full max-w-md flex-col gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onPick(s)}
              className="hover:bg-accent rounded-lg border px-3 py-2 text-left text-sm transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
        {messages.map((m) => (
          <Bubble key={m.id} message={m} />
        ))}
        {status === "submitted" && (
          <div className="text-muted-foreground animate-pulse text-sm">正在检索知识库…</div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function Bubble({ message }: { message: CopilotUIMessage }) {
  const isUser = message.role === "user";
  const text = messageText(message);
  const citations = messageCitations(message);

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {/* 后端吐的是纯文本，序号列表和「设置–策略设置」这类界面路径靠换行和空格
            表达结构。whitespace-pre-wrap 原样保留即可，不必为此引一个 markdown 渲染器。 */}
        <p className="break-words whitespace-pre-wrap">{text}</p>
        {!isUser && <Citations citations={citations} />}
      </div>
    </div>
  );
}
