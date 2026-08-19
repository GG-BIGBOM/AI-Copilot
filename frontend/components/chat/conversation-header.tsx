"use client";

/**
 * 主区顶栏（UI_OPTIMIZATION_SPEC §8.2 / §18）。
 *
 * **只负责当前会话**：标题 + 这段会话的操作。产品名归侧边栏，
 * 两处都写一遍「旺店通助手」只会让人觉得这个界面在自我介绍。
 *
 * 溢出菜单里目前只有「删除对话」——重命名 / 导出 / 分享后端都还没有，
 * 摆上去就是假按钮。
 */

import { Menu } from "@base-ui/react/menu";
import { Menu as MenuIcon, MoreHorizontal, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { POPUP_LAYER } from "@/lib/layers";
import { cn } from "@/lib/utils";

export function ConversationHeader({
  title,
  canDelete,
  onOpenDrawer,
  onDelete,
}: {
  title: string;
  /** 一段还没发过消息的新会话，服务端根本没有它，删不了也不用删 */
  canDelete: boolean;
  onOpenDrawer: () => void;
  onDelete: () => void;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border-subtle px-2 sm:px-3">
      <Button
        variant="ghost"
        size="icon-sm"
        className="md:hidden"
        onClick={onOpenDrawer}
        title="打开侧边栏"
        aria-label="打开侧边栏"
      >
        <MenuIcon />
      </Button>

      <h1 className="min-w-0 flex-1 truncate px-1.5 text-[15px] font-medium text-foreground">
        {title}
      </h1>

      {canDelete && (
        <Menu.Root>
          <Menu.Trigger
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors",
              "hover:bg-surface-muted hover:text-foreground data-popup-open:bg-surface-muted",
            )}
            aria-label="当前对话的更多操作"
          >
            <MoreHorizontal className="size-4" />
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className={POPUP_LAYER} side="bottom" align="end" sideOffset={6}>
              <Menu.Popup
                className="min-w-40 origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-1 text-popover-foreground outline-hidden transition-[opacity,scale] duration-150 ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0"
                style={{ boxShadow: "var(--shadow-floating)" }}
              >
                <Menu.Item
                  className="flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-muted-foreground outline-none select-none data-highlighted:bg-destructive/10 data-highlighted:text-destructive"
                  onClick={onDelete}
                >
                  <Trash2 className="size-3.5" />
                  删除对话
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      )}
    </header>
  );
}
