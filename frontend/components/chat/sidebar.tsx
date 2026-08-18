"use client";

/**
 * 侧边栏 —— Linear 式工作区导航（UI_OPTIMIZATION_SPEC §9）。
 *
 * 三条规矩：
 *   1. 侧边栏是**品牌所在地**，主区顶栏只负责当前会话，两边不重复写产品名；
 *   2. 行动作（删除）平时不出现，hover 或键盘聚焦才现身；
 *   3. 折叠态只留一条图标轨，不塞会话列表——56px 宽塞进去只会变成一片噪点，
 *      要切会话就展开，或者按 Ctrl/Cmd + K。
 */

import { useMemo } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  FileText,
  LogOut,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  SunMoon,
  Trash2,
  X,
} from "lucide-react";
import { Menu } from "@base-ui/react/menu";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/lib/theme";
import type { ConversationSummary, User } from "@/lib/api";

type DateGroup = "今天" | "昨天" | "前 7 天" | "更早";

const GROUP_ORDER = ["今天", "昨天", "前 7 天", "更早"] as const;

function groupConversationsByDate(
  items: ConversationSummary[],
): Record<DateGroup, ConversationSummary[]> {
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
      className="truncate whitespace-nowrap overflow-hidden"
      style={{
        opacity: collapsed ? 0 : 1,
        filter: collapsed ? "blur(4px)" : "blur(0px)",
        width: collapsed ? 0 : "auto",
        transitionProperty: "opacity, filter, width",
        transitionDuration: "200ms",
        transitionTimingFunction: "var(--ease-out)",
      }}
    >
      {children}
    </span>
  );
}

/** 侧栏里的一行导航。32–36px 高、8px 圆角、平时没有任何边框 */
const NAV_ROW =
  "flex h-8 w-full items-center gap-2.5 rounded-md px-2 text-left text-sm text-muted-foreground transition-colors hover:bg-sidebar-hover hover:text-foreground";

const MENU_POPUP =
  "min-w-40 origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-1 text-popover-foreground outline-hidden transition-[opacity,scale] duration-150 ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

const MENU_ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-muted-foreground outline-none select-none data-highlighted:bg-surface-muted data-highlighted:text-foreground";

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
  onOpenSearch,
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
  onOpenSearch: () => void;
}) {
  // 主题的应用时机在 layout 的内联脚本里（首帧之前），这里只跟着它显示图标
  const [isDark, toggleTheme] = useDarkMode();

  const grouped = useMemo(() => groupConversationsByDate(conversations), [conversations]);
  const initialLetter = user.email ? user.email.slice(0, 1).toUpperCase() : "U";

  return (
    <>
      {/* 移动端遮罩 */}
      <AnimatePresence>
        {open && (
          <motion.button
            type="button"
            aria-label="关闭侧边栏"
            className="fixed inset-0 z-40 bg-black/35 md:hidden"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          />
        )}
      </AnimatePresence>

      {/* 宽度用 CSS 过渡而不是 motion 的 animate：inline style 的 width 会盖掉
          移动端抽屉的固定宽度，得靠读 window.innerWidth 打补丁，水合时容易错 */}
      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col",
          "border-r border-sidebar-border md:static md:w-64",
          "transition-[width,transform] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
          open ? "translate-x-0" : "-translate-x-full md:translate-x-0",
          collapsed && "md:w-14",
        )}
        style={open ? { boxShadow: "var(--shadow-floating)" } : undefined}
      >
        {/* ─── 品牌 ─── */}
        <div
          className={cn(
            "flex h-12 shrink-0 items-center gap-2 px-3",
            collapsed && "md:justify-center md:px-0",
          )}
        >
          <span className="flex size-6 shrink-0 items-center justify-center text-foreground">
            <BrandMark className="size-[18px]" />
          </span>
          <CollapsibleLabel collapsed={collapsed}>
            <span className="text-sm font-semibold tracking-tight text-foreground">旺店通助手</span>
          </CollapsibleLabel>

          <div className="ml-auto flex items-center">
            {onToggleCollapse && (
              <Button
                variant="ghost"
                size="icon-sm"
                className={cn("hidden md:flex", collapsed && "md:hidden")}
                onClick={onToggleCollapse}
                title="收起侧边栏"
                aria-label="收起侧边栏"
              >
                <PanelLeftClose />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              className="md:hidden"
              onClick={onClose}
              title="关闭侧边栏"
              aria-label="关闭侧边栏"
            >
              <X />
            </Button>
          </div>
        </div>

        {/* 折叠态：展开按钮单独占一行，否则品牌那行挤不下 */}
        {collapsed && onToggleCollapse && (
          <div className="hidden justify-center px-2 pb-1 md:flex">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onToggleCollapse}
              title="展开侧边栏"
              aria-label="展开侧边栏"
            >
              <PanelLeftOpen />
            </Button>
          </div>
        )}

        {/* ─── 主操作 ─── */}
        <div className={cn("flex flex-col gap-px px-2 pb-2", collapsed && "md:items-center md:px-1.5")}>
          <button
            type="button"
            onClick={onNew}
            className={cn(
              NAV_ROW,
              "font-medium text-foreground hover:bg-sidebar-active",
              collapsed && "md:w-8 md:justify-center md:px-0",
            )}
            title="新建对话"
          >
            <Plus className="size-4 shrink-0" />
            <CollapsibleLabel collapsed={collapsed}>新建对话</CollapsibleLabel>
          </button>

          <button
            type="button"
            onClick={onOpenSearch}
            className={cn(NAV_ROW, collapsed && "md:w-8 md:justify-center md:px-0")}
            title="搜索对话（Ctrl / Cmd + K）"
          >
            <Search className="size-4 shrink-0" />
            <CollapsibleLabel collapsed={collapsed}>搜索</CollapsibleLabel>
            {!collapsed && (
              <kbd className="ml-auto hidden shrink-0 text-[11px] tabular-nums text-muted-foreground/60 md:block">
                ⌘K
              </kbd>
            )}
          </button>

          <Link
            href="/documents"
            onClick={onClose}
            className={cn(NAV_ROW, collapsed && "md:w-8 md:justify-center md:px-0")}
            title="知识库"
          >
            <FileText className="size-4 shrink-0" />
            <CollapsibleLabel collapsed={collapsed}>知识库</CollapsibleLabel>
          </Link>
        </div>

        {/* ─── 会话列表 ─── */}
        <nav
          className={cn(
            "min-h-0 flex-1 overflow-y-auto px-2 pb-2",
            collapsed && "md:hidden",
          )}
          aria-label="历史对话"
        >
          {conversations.length === 0 ? (
            <p className="px-2 py-8 text-center text-[13px] leading-relaxed text-muted-foreground/70">
              还没有历史对话
              <br />
              发一句问题就开始了
            </p>
          ) : (
            <div className="space-y-4 pt-1">
              {GROUP_ORDER.map((groupName) => {
                const list = grouped[groupName];
                if (!list.length) return null;

                return (
                  <div key={groupName}>
                    <div className="px-2 pb-1 text-[11px] font-medium text-muted-foreground/60">
                      {groupName}
                    </div>
                    <ul>
                      {list.map((c) => {
                        const isActive = c.id === activeId;
                        return (
                          <li key={c.id} className="group relative">
                            <button
                              type="button"
                              onClick={() => onPick(c.id)}
                              className={cn(
                                "flex h-8 w-full items-center rounded-md pl-2 pr-7 text-left text-sm transition-colors",
                                isActive
                                  ? "bg-sidebar-active font-medium text-foreground"
                                  : "text-muted-foreground hover:bg-sidebar-hover hover:text-foreground",
                              )}
                              title={c.title || "未命名对话"}
                            >
                              <span className="truncate">{c.title || "未命名对话"}</span>
                            </button>

                            {/* 平时透明，hover / 键盘聚焦才现身。不能用 display:none——
                                那样键盘用户永远够不到它 */}
                            <Menu.Root>
                              <Menu.Trigger
                                className={cn(
                                  "absolute right-1 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center",
                                  "rounded-sm text-muted-foreground opacity-0 transition-opacity",
                                  "hover:bg-sidebar-active hover:text-foreground",
                                  "focus-visible:opacity-100 group-hover:opacity-100 data-popup-open:opacity-100",
                                )}
                                aria-label={`对话「${c.title || "未命名对话"}」的更多操作`}
                              >
                                <MoreHorizontal className="size-3.5" />
                              </Menu.Trigger>
                              <Menu.Portal>
                                <Menu.Positioner className="outline-hidden" side="bottom" align="end" sideOffset={4}>
                                  <Menu.Popup
                                    className={MENU_POPUP}
                                    style={{ boxShadow: "var(--shadow-floating)" }}
                                  >
                                    <Menu.Item
                                      className={cn(
                                        MENU_ITEM,
                                        "data-highlighted:bg-destructive/10 data-highlighted:text-destructive",
                                      )}
                                      onClick={() => onDelete(c)}
                                    >
                                      <Trash2 className="size-3.5" />
                                      删除对话
                                    </Menu.Item>
                                  </Menu.Popup>
                                </Menu.Positioner>
                              </Menu.Portal>
                            </Menu.Root>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
            </div>
          )}
        </nav>

        {/* ─── 账号 ─── */}
        <div className={cn("mt-auto shrink-0 p-2", collapsed && "md:flex md:justify-center md:px-1.5")}>
          <Menu.Root>
            <Menu.Trigger
              className={cn(
                NAV_ROW,
                "h-9 data-popup-open:bg-sidebar-hover",
                collapsed && "md:w-9 md:justify-center md:px-0",
              )}
              aria-label="账号与设置"
            >
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-muted text-[11px] font-semibold text-foreground">
                {initialLetter}
              </span>
              <CollapsibleLabel collapsed={collapsed}>
                <span className="text-[13px]">{user.email}</span>
              </CollapsibleLabel>
              {!collapsed && <MoreHorizontal className="ml-auto size-3.5 shrink-0 opacity-60" />}
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Positioner className="outline-hidden" side="top" align="start" sideOffset={6}>
                <Menu.Popup className={MENU_POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
                  <div className="px-2 pb-1.5 pt-1 text-[11px] text-muted-foreground/70">
                    {user.email}
                  </div>
                  <Menu.Item className={MENU_ITEM} onClick={toggleTheme}>
                    <SunMoon className="size-3.5" />
                    {isDark ? "切换为浅色" : "切换为深色"}
                  </Menu.Item>
                  <Menu.Item
                    className={cn(
                      MENU_ITEM,
                      "data-highlighted:bg-destructive/10 data-highlighted:text-destructive",
                    )}
                    onClick={onLogout}
                  >
                    <LogOut className="size-3.5" />
                    退出登录
                  </Menu.Item>
                </Menu.Popup>
              </Menu.Positioner>
            </Menu.Portal>
          </Menu.Root>
        </div>
      </aside>
    </>
  );
}
