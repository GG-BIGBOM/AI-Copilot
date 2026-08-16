"use client";

import { useEffect, useState } from "react";
import {
  LogOut,
  MessageSquare,
  MessageSquarePlus,
  Moon,
  Search,
  Sun,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ConversationSummary, User } from "@/lib/api";

export function Sidebar({
  conversations,
  activeId,
  user,
  open,
  onClose,
  onNew,
  onPick,
  onLogout,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  user: User;
  open: boolean;
  onClose: () => void;
  onNew: () => void;
  onPick: (id: string) => void;
  onLogout: () => void;
}) {
  const [search, setSearch] = useState("");
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const savedTheme = localStorage.getItem("theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const shouldBeDark = savedTheme === "dark" || (!savedTheme && prefersDark);
    if (shouldBeDark) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }, []);

  function toggleTheme() {
    const isDarkNow = document.documentElement.classList.toggle("dark");
    setIsDark(isDarkNow);
    localStorage.setItem("theme", isDarkNow ? "dark" : "light");
  }

  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(search.trim().toLowerCase()),
  );

  const initialLetter = user.email ? user.email.slice(0, 1).toUpperCase() : "U";

  return (
    <>
      {/* 移动端：抽屉打开时压一层半透明遮罩，点它关闭 */}
      {open && (
        <button
          type="button"
          aria-label="关闭侧栏"
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-xs md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-40 flex w-68 flex-col border-r transition-transform duration-200 ease-in-out md:static md:translate-x-0",
          open ? "translate-x-0 shadow-2xl" : "-translate-x-full",
        )}
      >
        {/* 顶部操作区 */}
        <div className="flex flex-col gap-2 border-b p-3">
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="default"
              size="sm"
              className="flex-1 justify-start gap-2 shadow-xs transition-transform active:scale-[0.99]"
              onClick={onNew}
            >
              <MessageSquarePlus className="size-4" />
              <span>新对话</span>
            </Button>
            <Button variant="ghost" size="icon" className="size-8 md:hidden" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </div>

          {conversations.length > 3 && (
            <div className="relative">
              <Search className="text-muted-foreground absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索历史会话…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-8 pl-8 text-xs bg-sidebar-accent/40 border-sidebar-border"
              />
            </div>
          )}
        </div>

        {/* 会话列表 */}
        <nav className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 px-2 text-center text-muted-foreground">
              <MessageSquare className="size-8 opacity-20 mb-2" />
              <p className="text-xs">还没有历史对话</p>
              <p className="text-[11px] opacity-75 mt-1">发一句问题开启新旅程</p>
            </div>
          ) : filteredConversations.length === 0 ? (
            <p className="text-muted-foreground px-2 py-4 text-center text-xs">无匹配的历史会话</p>
          ) : (
            <ul className="space-y-1">
              {filteredConversations.map((c) => {
                const isActive = c.id === activeId;
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => onPick(c.id)}
                      className={cn(
                        "group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition-all duration-150",
                        isActive
                          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium shadow-2xs"
                          : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                      )}
                      title={c.title}
                    >
                      <MessageSquare
                        className={cn(
                          "size-3.5 shrink-0 transition-opacity",
                          isActive ? "text-primary opacity-100" : "opacity-40 group-hover:opacity-80",
                        )}
                      />
                      <span className="truncate flex-1">{c.title}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        {/* 底部用户信息 & 设置 */}
        <div className="space-y-2.5 border-t border-sidebar-border p-3 bg-sidebar-accent/10">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary font-semibold text-xs border border-primary/20">
                {initialLetter}
              </div>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium" title={user.email}>
                  {user.email}
                </p>
                <p className="text-[10px] text-muted-foreground">旗舰版知识库</p>
              </div>
            </div>

            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0 text-muted-foreground hover:text-foreground"
              onClick={toggleTheme}
              title={isDark ? "切换为浅色模式" : "切换为深色模式"}
              aria-label={isDark ? "切换为浅色模式" : "切换为深色模式"}
            >
              {isDark ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
            </Button>
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start gap-2 h-8 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10"
            onClick={onLogout}
          >
            <LogOut className="size-3.5" />
            <span>退出登录</span>
          </Button>
        </div>
      </aside>
    </>
  );
}
