"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandMark } from "@/components/brand-mark";
import { ChatView } from "@/components/chat/chat-view";
import { ConversationHeader } from "@/components/chat/conversation-header";
import { ConversationSearch } from "@/components/chat/conversation-search";
import { Sidebar } from "@/components/chat/sidebar";
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
  const [searchOpen, setSearchOpen] = useState(false);
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

  // Ctrl / Cmd + K 打开会话搜索
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

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

  async function deleteConversation(c: ConversationSummary) {
    if (!window.confirm(`删除对话「${c.title || "未命名对话"}」？消息记录无法恢复。`)) return;

    // 先从列表里拿掉，失败再放回去——和「知识库」同一套做法，
    // 删除很快，转个圈反而显得卡
    const before = conversations;
    setConversations((list) => list.filter((x) => x.id !== c.id));
    try {
      await api.deleteConversation(c.id);
    } catch {
      setConversations(before);
      return;
    }
    // ⭐ 删的正好是当前打开的那段：必须换一个新会话 id。
    // 不换的话输入框还指着一个已经不存在的 id，下一句话会以这个 id 重新
    // 建一段会话——用户眼里就是「删掉的对话自己回来了」
    if (c.id === chatId) newConversation();
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
        <div className="flex flex-col items-center gap-3">
          <BrandMark className="size-6 text-bronze" thinking />
          <span className="text-[13px] text-muted-foreground">正在载入…</span>
        </div>
      </main>
    );
  }

  // 新会话还没落库，列表里查不到——顶栏显示「新对话」，也不给删除入口
  const current = conversations.find((c) => c.id === chatId) ?? null;

  return (
    <div className="flex h-full overflow-hidden bg-background">
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
        onDelete={deleteConversation}
        onLogout={logout}
        onOpenSearch={() => setSearchOpen(true)}
      />

      <ConversationSearch
        open={searchOpen}
        conversations={conversations}
        onOpenChange={setSearchOpen}
        onPick={openConversation}
      />

      <main className="flex h-full min-w-0 flex-1 flex-col">
        <ConversationHeader
          title={current?.title || "新对话"}
          canDelete={Boolean(current)}
          onOpenDrawer={() => setDrawerOpen(true)}
          onDelete={() => current && deleteConversation(current)}
        />

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
