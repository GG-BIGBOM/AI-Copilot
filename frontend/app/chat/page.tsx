"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Menu } from "lucide-react";

import { ChatView } from "@/components/chat/chat-view";
import { Sidebar } from "@/components/chat/sidebar";
import { Button } from "@/components/ui/button";
import { api, type ConversationSummary, type StoredMessage } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth-guard";
import type { CopilotUIMessage } from "@/lib/chat-types";

/** 数据库里的历史消息还原成 UIMessage，让 useChat 接着往下续。 */
function toUIMessage(m: StoredMessage): CopilotUIMessage {
  const parts: CopilotUIMessage["parts"] = [{ type: "text", text: m.content, state: "done" }];
  if (m.citations?.length) {
    parts.push({ type: "data-citations", data: { citations: m.citations } });
  }
  return { id: m.id, role: m.role === "assistant" ? "assistant" : "user", parts };
}

export default function ChatPage() {
  const router = useRouter();
  const auth = useRequireAuth();

  const [chatId, setChatId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<CopilotUIMessage[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const refreshConversations = useCallback(() => {
    api.conversations().then(setConversations).catch(() => {
      /* 列表拉不到不该影响提问，静默即可 */
    });
  }, []);

  // ⭐ 会话 id 在渲染期派生，既不放 useState 初始值也不放 effect。
  //
  // 放 useState 初始值：静态导出会在构建时预渲染本页，那个 UUID 会被写死进
  // HTML，浏览器再算一个就是 hydration mismatch。
  // 放 effect：同步 setState 会触发级联渲染，React 的 lint 直接报错。
  //
  // 渲染期派生两头都躲开了——预渲染时 auth.status 还是 loading，这段根本不执行。
  if (auth.status === "authed" && chatId === null) {
    setChatId(crypto.randomUUID());
  }

  useEffect(() => {
    if (auth.status !== "authed") return;
    refreshConversations();
  }, [auth.status, refreshConversations]);

  async function openConversation(id: string) {
    setDrawerOpen(false);
    try {
      const stored = await api.messages(id);
      setInitialMessages(stored.map(toUIMessage));
      setChatId(id);
    } catch {
      /* 会话被删了之类，忽略 */
    }
  }

  function newConversation() {
    setDrawerOpen(false);
    setInitialMessages([]);
    setChatId(crypto.randomUUID());
  }

  async function logout() {
    await api.logout().catch(() => {});
    router.replace("/login");
  }

  if (auth.status !== "authed" || !chatId) {
    return (
      <main className="flex h-full items-center justify-center">
        <p className="text-muted-foreground text-sm">正在加载…</p>
      </main>
    );
  }

  return (
    <div className="flex h-full">
      <Sidebar
        conversations={conversations}
        activeId={chatId}
        user={auth.user}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onNew={newConversation}
        onPick={openConversation}
        onLogout={logout}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-4 py-2 md:hidden">
          <Button variant="ghost" size="icon" onClick={() => setDrawerOpen(true)}>
            <Menu className="size-4" />
          </Button>
          <span className="truncate text-sm font-medium">旗舰版 ERP 知识库助手</span>
        </header>

        {/* key 一变就整棵重挂，切换历史对话时不会串台 */}
        <ChatView
          key={chatId}
          chatId={chatId}
          initialMessages={initialMessages}
          onConversationTouched={refreshConversations}
        />
      </main>
    </div>
  );
}
