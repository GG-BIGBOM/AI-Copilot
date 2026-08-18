"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Loader2,
  Menu,
  MessageSquarePlus,
  PanelLeftOpen,
} from "lucide-react";

import { ChatView } from "@/components/chat/chat-view";
import { Sidebar } from "@/components/chat/sidebar";
import { Button } from "@/components/ui/button";
import { api, type ConversationSummary, type StoredMessage } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth-guard";
import { usePersistedFlag } from "@/lib/persisted-flag";
import type { CopilotUIMessage } from "@/lib/chat-types";

/** 数据库里的历史消息还原成 UIMessage，让 useChat 接着往下续。 */
function toUIMessage(m: StoredMessage): CopilotUIMessage {
  const parts: CopilotUIMessage["parts"] = [{ type: "text", text: m.content, state: "done" }];
  if (m.citations?.length) {
    parts.push({ type: "data-citations", data: { citations: m.citations } });
  }
  if (m.images?.length) {
    parts.push({ type: "data-images", data: { images: m.images } });
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
  // 折叠状态存在 localStorage 里。用 useSyncExternalStore 而不是
  // useEffect+setState：后者首帧一定是错的值、随后闪一下，且 React 19 的
  // set-state-in-effect 规则会直接报错
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedFlag("sidebar-collapsed");

  function toggleSidebarCollapse() {
    setSidebarCollapsed((prev) => !prev);
  }

  const refreshConversations = useCallback(() => {
    api.conversations().then(setConversations).catch(() => {
      /* 列表拉不到不该影响提问，静默即可 */
    });
  }, []);

  // ⭐ 会话 id 在渲染期派生，既不放 useState 初始值也不放 effect。
  // 避免静态导出预渲染水合不一致与级联渲染报错
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
      <main className="flex h-full items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3 text-muted-foreground text-sm">
          <Loader2 className="size-5 animate-spin text-foreground/30" />
          <span className="font-medium text-foreground/60">正在载入…</span>
        </div>
      </main>
    );
  }

  return (
    <div className="flex h-full bg-background overflow-hidden">
      <Sidebar
        conversations={conversations}
        activeId={chatId}
        user={auth.user}
        open={drawerOpen}
        collapsed={sidebarCollapsed}
        onClose={() => setDrawerOpen(false)}
        onToggleCollapse={toggleSidebarCollapse}
        onNew={newConversation}
        onPick={openConversation}
        onLogout={logout}
      />

      <main className="flex min-w-0 flex-1 flex-col relative h-full">
        {/* 顶部导航栏 — 极简 */}
        <header className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 bg-background px-3 z-20">
          <div className="flex items-center gap-1.5 min-w-0">
            {/* 移动端打开抽屉 */}
            <Button
              variant="ghost"
              size="icon"
              className="size-7 md:hidden text-muted-foreground hover:text-foreground"
              onClick={() => setDrawerOpen(true)}
              title="打开侧边栏"
              aria-label="打开侧边栏"
            >
              <Menu className="size-4" />
            </Button>

            {/* 桌面端当侧边栏折叠时，显示展开按钮 */}
            {sidebarCollapsed && (
              <Button
                variant="ghost"
                size="icon"
                className="hidden md:flex size-7 text-muted-foreground hover:text-foreground"
                onClick={toggleSidebarCollapse}
                title="展开侧边栏"
                aria-label="展开侧边栏"
              >
                <PanelLeftOpen className="size-4" />
              </Button>
            )}

            <span className="text-sm font-semibold text-foreground truncate">
              旺店通助手
            </span>
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground rounded-lg"
            onClick={newConversation}
            title="新建对话"
          >
            <MessageSquarePlus className="size-3.5" />
            <span className="hidden sm:inline">新对话</span>
          </Button>
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
