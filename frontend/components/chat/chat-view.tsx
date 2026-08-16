"use client";

import { useMemo, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";

import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";
import type { CopilotUIMessage } from "@/lib/chat-types";

/**
 * 一次会话视图。父组件用 `key={chatId}` 强制重挂，切换历史对话时状态干净。
 *
 * ⭐ `chatId` 必须是 UUID。useChat 默认自己生成的是 nanoid，后端解析不出来
 * 就会每轮另开一条会话，多轮对话全散了——见 plan.md M3 的接口约定。
 */
export function ChatView({
  chatId,
  initialMessages,
  onConversationTouched,
}: {
  chatId: string;
  initialMessages: CopilotUIMessage[];
  onConversationTouched: () => void;
}) {
  const [draft, setDraft] = useState("");

  // 每次 render 都 new 一个 transport 会让 useChat 反复重建内部状态
  const transport = useMemo(
    () =>
      new DefaultChatTransport<CopilotUIMessage>({
        api: `${API_BASE}/api/chat`,
        // ⭐ 登录态在 HttpOnly cookie 里。本地开发 3000→8000 是跨源的，
        // 少了这行 cookie 根本不发，后端一律回 401
        credentials: "include",
      }),
    [],
  );

  const { messages, sendMessage, status, error, stop, regenerate } = useChat<CopilotUIMessage>({
    id: chatId,
    messages: initialMessages,
    transport,
    onFinish: onConversationTouched, // 新会话生成完，刷新左侧列表
  });

  const busy = status === "submitted" || status === "streaming";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageList
        messages={messages}
        status={status}
        onPick={(text) => {
          setDraft("");
          sendMessage({ text });
        }}
        onRegenerate={() => regenerate()}
      />

      {error && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <Alert variant="destructive" className="rounded-2xl border-destructive/40 shadow-xs">
            <AlertDescription className="flex items-center justify-between gap-3 text-xs">
              <span>{error.message || "出错了，请稍后重试。"}</span>
              <Button size="sm" variant="outline" className="h-7 text-xs rounded-lg" onClick={() => regenerate()}>
                重试
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      )}

      <Composer
        draft={draft}
        onDraftChange={setDraft}
        busy={busy}
        onStop={stop}
        onSend={(text) => sendMessage({ text })}
      />
    </div>
  );
}
