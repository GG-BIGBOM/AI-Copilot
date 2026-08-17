"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  FileText,
  LogOut,
  MessageSquare,
  MessageSquarePlus,
  Moon,
  PanelLeftClose,
  Search,
  Sparkles,
  Sun,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/lib/theme";
import type { ConversationSummary, User } from "@/lib/api";

type DateGroup = "今天" | "昨天" | "前 7 天" | "更早";

function groupConversationsByDate(items: ConversationSummary[]): Record<DateGroup, ConversationSummary[]> {
  const groups: Record<DateGroup, ConversationSummary[]> = {
    今天: [],
    昨天: [],
    "前 7 天": [],
    更早: [],
  };

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 24 * 60 * 60 * 1000;
  const sevenDaysStart = todayStart - 7 * 24 * 60 * 60 * 1000;

  for (const item of items) {
    const itemTime = item.created_at ? new Date(item.created_at).getTime() : 0;
    if (itemTime >= todayStart) {
      groups["今天"].push(item);
    } else if (itemTime >= yesterdayStart) {
      groups["昨天"].push(item);
    } else if (itemTime >= sevenDaysStart) {
      groups["前 7 天"].push(item);
    } else {
      groups["更早"].push(item);
    }
  }

  return groups;
}

export function Sidebar({
  conversations,
  activeId,
  user,
  open,
  collapsed = false,
  onClose,
  onToggleCollapse,
  onNew,
  onPick,
  onLogout,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  user: User;
  open: boolean;
  collapsed?: boolean;
  onClose: () => void;
  onToggleCollapse?: () => void;
  onNew: () => void;
  onPick: (id: string) => void;
  onLogout: () => void;
}) {
  const [search, setSearch] = useState("");
  // 主题的应用时机在 layout 的内联脚本里（首帧之前），这里只跟着它显示图标
  const [isDark, toggleTheme] = useDarkMode();

  const filteredConversations = useMemo(() => {
    if (!search.trim()) return conversations;
    const q = search.trim().toLowerCase();
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, search]);

  const grouped = useMemo(
    () => groupConversationsByDate(filteredConversations),
    [filteredConversations],
  );

  const initialLetter = user.email ? user.email.slice(0, 1).toUpperCase() : "U";

  return (
    <>
      {/* 移动端遮罩 */}
      {open && (
        <button
          type="button"
          aria-label="关闭侧边栏"
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-xs md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-50 flex flex-col border-r border-sidebar-border transition-all duration-300 ease-in-out md:static",
          // 移动端展示状态
          open ? "translate-x-0 w-72 shadow-2xl" : "-translate-x-full md:translate-x-0",
          // 桌面端折叠状态
          collapsed ? "md:w-0 md:overflow-hidden md:border-r-0" : "md:w-68",
        )}
      >
        {/* 顶部：新建对话与折叠按钮 */}
        <div className="flex flex-col gap-2.5 border-b border-sidebar-border p-3">
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="default"
              size="sm"
              className="flex-1 justify-start gap-2 rounded-xl shadow-xs transition-transform active:scale-[0.99] font-medium"
              onClick={onNew}
            >
              <MessageSquarePlus className="size-4" />
              <span>新建对话</span>
            </Button>

            {/* 桌面端折叠收起按钮 */}
            {onToggleCollapse && (
              <Button
                variant="ghost"
                size="icon"
                className="hidden md:flex size-8 text-muted-foreground hover:text-foreground"
                onClick={onToggleCollapse}
                title="收起侧边栏"
                aria-label="收起侧边栏"
              >
                <PanelLeftClose className="size-4" />
              </Button>
            )}

            {/* 移动端关闭按钮 */}
            <Button
              variant="ghost"
              size="icon"
              className="size-8 md:hidden text-muted-foreground hover:text-foreground"
              onClick={onClose}
              title="关闭侧边栏"
              aria-label="关闭侧边栏"
            >
              <X className="size-4" />
            </Button>
          </div>

          {/* 我的文档。放在新建对话下面、会话列表之上——它是入口，不是历史记录 */}
          <Link
            href="/documents"
            onClick={onClose}
            className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-foreground"
          >
            <FileText className="size-3.5 opacity-60" />
            <span>我的文档</span>
          </Link>

          {/* 搜索框 */}
          {conversations.length > 2 && (
            <div className="relative">
              <Search className="text-muted-foreground absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索历史对话…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-8 pl-8 text-xs bg-sidebar-accent/50 border-sidebar-border rounded-lg focus-visible:ring-1"
              />
            </div>
          )}
        </div>

        {/* 会话列表：ChatGPT 风格按时间分组 */}
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-4">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 px-2 text-center text-muted-foreground">
              <div className="flex size-10 items-center justify-center rounded-xl bg-sidebar-accent/60 mb-2.5">
                <MessageSquare className="size-5 opacity-40" />
              </div>
              <p className="text-xs font-medium text-foreground/80">暂无历史对话</p>
              <p className="text-[11px] text-muted-foreground mt-1">发一句问题开启知识库探索</p>
            </div>
          ) : filteredConversations.length === 0 ? (
            <p className="text-muted-foreground px-2 py-6 text-center text-xs">未找到匹配的对话</p>
          ) : (
            (search.trim() ? (["所有结果"] as const) : (["今天", "昨天", "前 7 天", "更早"] as const)).map(
              (groupName) => {
                const list =
                  groupName === "所有结果"
                    ? filteredConversations
                    : grouped[groupName as DateGroup];
                if (!list || list.length === 0) return null;

                return (
                  <div key={groupName} className="space-y-1">
                    <div className="px-2.5 py-1 text-[10px] font-semibold tracking-wider text-muted-foreground/70 uppercase">
                      {groupName}
                    </div>
                    <ul className="space-y-0.5">
                      {list.map((c) => {
                        const isActive = c.id === activeId;
                        return (
                          <li key={c.id}>
                            <button
                              type="button"
                              onClick={() => onPick(c.id)}
                              className={cn(
                                "group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition-all duration-150",
                                isActive
                                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold shadow-2xs"
                                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                              )}
                              title={c.title}
                            >
                              <MessageSquare
                                className={cn(
                                  "size-3.5 shrink-0 transition-opacity",
                                  isActive
                                    ? "text-primary opacity-100"
                                    : "opacity-40 group-hover:opacity-75",
                                )}
                              />
                              <span className="truncate flex-1">{c.title || "未命名对话"}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              },
            )
          )}
        </nav>

        {/* 底部用户信息与快捷设置 */}
        <div className="border-t border-sidebar-border p-3 bg-sidebar-accent/20 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary font-bold text-xs border border-primary/20 shadow-2xs">
                {initialLetter}
              </div>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-foreground" title={user.email}>
                  {user.email}
                </p>
                <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Sparkles className="size-2.5 text-primary" />
                  <span>旗舰版知识库</span>
                </div>
              </div>
            </div>

            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0 text-muted-foreground hover:text-foreground rounded-lg"
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
            className="w-full justify-start gap-2 h-8 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors"
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
