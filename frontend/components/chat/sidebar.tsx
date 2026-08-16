"use client";

import { LogOut, MessageSquarePlus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
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
  return (
    <>
      {/* 移动端：抽屉打开时压一层半透明遮罩，点它关闭 */}
      {open && (
        <button
          type="button"
          aria-label="关闭侧栏"
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r transition-transform md:static md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b p-3">
          <Button variant="outline" size="sm" className="flex-1 justify-start" onClick={onNew}>
            <MessageSquarePlus className="size-4" />
            新对话
          </Button>
          <Button variant="ghost" size="icon" className="md:hidden" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>

        <nav className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 ? (
            <p className="text-muted-foreground px-2 py-4 text-xs">还没有历史对话</p>
          ) : (
            <ul className="space-y-1">
              {conversations.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onPick(c.id)}
                    className={cn(
                      "hover:bg-sidebar-accent w-full truncate rounded-md px-2 py-2 text-left text-sm transition-colors",
                      c.id === activeId && "bg-sidebar-accent font-medium",
                    )}
                    title={c.title}
                  >
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>

        <div className="space-y-2 border-t p-3">
          <p className="text-muted-foreground truncate text-xs" title={user.email}>
            {user.email}
          </p>
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={onLogout}>
            <LogOut className="size-4" />
            退出登录
          </Button>
        </div>
      </aside>
    </>
  );
}
