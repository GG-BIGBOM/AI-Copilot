"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  FileText,
  LogOut,
  MessageSquare,
  MessageSquarePlus,
  Moon,
  PanelLeftClose,
  Search,
  Sun,
  Trash2,
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

/** 折叠态下文字的 blur-fade 容器 */
function CollapsibleLabel({
  collapsed,
  children,
}: {
  collapsed: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className="truncate transition-all whitespace-nowrap overflow-hidden"
      style={{
        opacity: collapsed ? 0 : 1,
        filter: collapsed ? "blur(4px)" : "blur(0px)",
        width: collapsed ? 0 : "auto",
        transitionProperty: "opacity, filter, width",
        transitionDuration: "200ms",
        transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      {children}
    </span>
  );
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
  onDelete,
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
  onDelete: (c: ConversationSummary) => void;
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
      <AnimatePresence>
        {open && (
          <motion.button
            type="button"
            aria-label="关闭侧边栏"
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] md:hidden"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
        )}
      </AnimatePresence>

      <motion.aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-50 flex flex-col border-r border-sidebar-border md:static",
          // 移动端展示状态
          open ? "translate-x-0 w-[260px] shadow-lg" : "-translate-x-full md:translate-x-0",
        )}
        animate={{
          width: open ? 260 : (collapsed ? 64 : 260),
        }}
        transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
        style={{
          // 移动端覆盖 motion animate
          ...(typeof window !== "undefined" && window.innerWidth < 768
            ? { width: open ? 260 : 0 }
            : {}),
        }}
      >
        {/* ─── 顶部：品牌 + 新建对话 ─── */}
        <div className="flex flex-col gap-2 border-b border-sidebar-border p-3">
          <div className="flex items-center justify-between gap-2">
            {/* 品牌标识 */}
            <div className={cn(
              "flex items-center gap-2 min-w-0 transition-all",
              collapsed ? "justify-center w-full" : "",
            )}>
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground text-sm font-bold">
                ◈
              </div>
              <CollapsibleLabel collapsed={collapsed}>
                <span className="text-sm font-semibold text-foreground">旺店通助手</span>
              </CollapsibleLabel>
            </div>

            {/* 桌面端折叠收起按钮 */}
            {onToggleCollapse && !collapsed && (
              <Button
                variant="ghost"
                size="icon"
                className="hidden md:flex size-7 text-muted-foreground hover:text-foreground"
                onClick={onToggleCollapse}
                title="收起侧边栏"
                aria-label="收起侧边栏"
              >
                <PanelLeftClose className="size-4" />
              </Button>
            )}

            {/* 折叠态展开按钮 */}
            {onToggleCollapse && collapsed && (
              <div /> // 占位，品牌标识已居中
            )}

            {/* 移动端关闭按钮 */}
            <Button
              variant="ghost"
              size="icon"
              className="size-7 md:hidden text-muted-foreground hover:text-foreground"
              onClick={onClose}
              title="关闭侧边栏"
              aria-label="关闭侧边栏"
            >
              <X className="size-4" />
            </Button>
          </div>

          {/* 新建对话按钮 */}
          <Button
            variant="default"
            size="sm"
            className={cn(
              "gap-2 rounded-lg font-medium transition-all active:scale-[0.98]",
              collapsed ? "justify-center px-0" : "justify-start",
            )}
            onClick={onNew}
          >
            <MessageSquarePlus className="size-4 shrink-0" />
            <CollapsibleLabel collapsed={collapsed}>
              新建对话
            </CollapsibleLabel>
          </Button>

          {/* 我的文档 */}
          <Link
            href="/documents"
            onClick={onClose}
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-foreground",
              collapsed ? "justify-center px-0" : "",
            )}
          >
            <FileText className="size-3.5 shrink-0 opacity-60" />
            <CollapsibleLabel collapsed={collapsed}>
              知识库
            </CollapsibleLabel>
          </Link>

          {/* 搜索框 — 折叠时只显示搜索图标 */}
          {!collapsed && conversations.length > 2 && (
            <div className="relative">
              <Search className="text-muted-foreground absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2" />
              <Input
                placeholder="搜索历史对话…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-7 pl-8 text-xs bg-sidebar-accent/50 border-sidebar-border rounded-md focus-visible:ring-1"
              />
            </div>
          )}
          {collapsed && (
            <button
              type="button"
              className="flex items-center justify-center rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground transition-colors"
              onClick={onToggleCollapse}
              title="展开搜索"
            >
              <Search className="size-3.5" />
            </button>
          )}
        </div>

        {/* ─── 会话列表 ─── */}
        <nav className={cn("flex-1 overflow-y-auto px-2 py-3", collapsed ? "px-1.5" : "")}>
          {collapsed ? (
            /* 折叠态：不显示列表 */
            <div className="flex flex-col items-center gap-1 py-2">
              {conversations.slice(0, 5).map((c) => {
                const isActive = c.id === activeId;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => onPick(c.id)}
                    className={cn(
                      "flex size-8 items-center justify-center rounded-lg transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                    )}
                    title={c.title || "未命名对话"}
                  >
                    <MessageSquare className="size-3.5" />
                  </button>
                );
              })}
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 px-2 text-center text-muted-foreground">
              <div className="flex size-9 items-center justify-center rounded-lg bg-sidebar-accent/60 mb-2">
                <MessageSquare className="size-4 opacity-40" />
              </div>
              <p className="text-xs font-medium text-foreground/80">暂无历史对话</p>
              <p className="text-[11px] text-muted-foreground mt-1">发一句问题开启知识库探索</p>
            </div>
          ) : filteredConversations.length === 0 ? (
            <p className="text-muted-foreground px-2 py-6 text-center text-xs">未找到匹配的对话</p>
          ) : (
            <div className="space-y-3">
              {(search.trim() ? (["所有结果"] as const) : (["今天", "昨天", "前 7 天", "更早"] as const)).map(
                (groupName) => {
                  const list =
                    groupName === "所有结果"
                      ? filteredConversations
                      : grouped[groupName as DateGroup];
                  if (!list || list.length === 0) return null;

                  return (
                    <div key={groupName} className="space-y-0.5">
                      <div className="px-2 py-1 text-[10px] font-semibold tracking-wider text-muted-foreground/60 uppercase">
                        {groupName}
                      </div>
                      <ul className="space-y-px">
                        {list.map((c) => {
                          const isActive = c.id === activeId;
                          return (
                            <li key={c.id} className="group relative">
                              <button
                                type="button"
                                onClick={() => onPick(c.id)}
                                className={cn(
                                  "flex w-full items-center gap-2 rounded-lg py-1.5 pl-2 pr-8 text-left text-[13px] transition-colors",
                                  isActive
                                    ? "bg-sidebar-accent text-foreground font-medium"
                                    : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
                                )}
                                title={c.title}
                              >
                                <MessageSquare
                                  className={cn(
                                    "size-3.5 shrink-0",
                                    isActive ? "text-foreground" : "opacity-40 group-hover:opacity-60",
                                  )}
                                />
                                <span className="truncate flex-1">{c.title || "未命名对话"}</span>
                              </button>
                              {/* 删除。平时透明，hover/聚焦才现身——常驻一排垃圾桶会把列表
                                  变成一片图标。但 `focus-visible:opacity-100` 不能省：
                                  只靠 hover 的话键盘用户永远够不着它 */}
                              <button
                                type="button"
                                onClick={() => onDelete(c)}
                                className={cn(
                                  "absolute right-1 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center",
                                  "rounded-md text-muted-foreground opacity-0 transition-opacity",
                                  "hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100",
                                  "group-hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                                )}
                                title="删除对话"
                                aria-label={`删除对话 ${c.title || "未命名对话"}`}
                              >
                                <Trash2 className="size-3.5" />
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  );
                },
              )}
            </div>
          )}
        </nav>

        {/* ─── 底部用户信息 ─── */}
        <div className={cn(
          "border-t border-sidebar-border p-3 space-y-2",
          collapsed ? "p-2 space-y-1.5" : "",
        )}>
          <div className="flex items-center justify-between gap-2">
            <div className={cn(
              "flex items-center gap-2 min-w-0",
              collapsed ? "justify-center w-full" : "",
            )}>
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-foreground font-semibold text-[11px]">
                {initialLetter}
              </div>
              {!collapsed && (
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-foreground" title={user.email}>
                    {user.email}
                  </p>
                </div>
              )}
            </div>

            {!collapsed && (
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
            )}
          </div>

          {collapsed ? (
            <div className="flex flex-col items-center gap-1">
              <button
                type="button"
                className="flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground transition-colors"
                onClick={toggleTheme}
                title={isDark ? "浅色" : "深色"}
              >
                {isDark ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
              </button>
              <button
                type="button"
                className="flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                onClick={onLogout}
                title="退出登录"
              >
                <LogOut className="size-3.5" />
              </button>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start gap-2 h-7 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors"
              onClick={onLogout}
            >
              <LogOut className="size-3.5" />
              <span>退出登录</span>
            </Button>
          )}
        </div>
      </motion.aside>
    </>
  );
}
