"use client";

/**
 * Smart Composer（UI_OPTIMIZATION_SPEC §11）。
 *
 * 全应用最重要的一个控件，所以它的规矩最死：
 *   Enter 发送 · Shift + Enter 换行 · **中文输入法选词时的 Enter 永远不发送**。
 *
 * 底下那一排只有一个控件：回答档位。它是真接上后端的（换模型 + 换写法）。
 * 上传、模式切换这些还没有真实功能的，一个都不放——摆一个点了没反应的图标，
 * 比少一个功能更伤信任。
 */

import { useEffect, useRef } from "react";
import { Menu } from "@base-ui/react/menu";
import { ArrowUp, Check, ChevronDown, Square } from "lucide-react";

import { ANSWER_MODES, MODE_META, type AnswerMode } from "@/lib/answer-mode";
import type { KnowledgeSpace } from "@/lib/api";
import { POPUP_LAYER } from "@/lib/layers";
import { cn } from "@/lib/utils";

/**
 * 知识版本选择器（M18）。**同一个 ERP 的三个产品版本，答案各不相同。**
 *
 * 三条规矩，每一条都对应一种会出错的样子：
 *
 * 1. **只剩一个空间时整个不显示。** §11.7：没有真实选择余地的控件就是假按钮，
 *    摆一个只有一个选项的下拉框，比少一个控件更伤信任。
 * 2. **会话一旦有了消息就变成只读标签。** 已有会话的版本在服务端是钉死的
 *    （换版本 = 新建会话）。留一个点了没反应的下拉框，用户会以为自己切过去了，
 *    而后面几轮的答案仍然来自原来那一版——**没有任何提示**。
 * 3. **标签上写清楚怎么换。** 只标一个"客户端企业版"而不说怎么换，
 *    用户会去点它、点不动、然后以为是坏的。
 */
function SpacePicker({
  spaces,
  space,
  onSpaceChange,
  locked,
}: {
  spaces: KnowledgeSpace[];
  space: string | null;
  onSpaceChange: (next: string) => void;
  locked: boolean;
}) {
  // 规矩 1：一个都没有、或者只有一个 —— 整个不显示
  if (spaces.length < 2) return null;
  const current = spaces.find((s) => s.code === space) ?? spaces[0];

  // 规矩 2 + 3：已经聊起来了就只读
  if (locked) {
    return (
      <span
        className="flex h-6 shrink-0 items-center rounded-sm px-1.5 text-[11px] text-muted-foreground"
        title="这条会话的知识版本已经定了。换版本请新建会话。"
      >
        {current.name}
      </span>
    );
  }

  return (
    <Menu.Root>
      <Menu.Trigger
        className={cn(
          "flex h-6 shrink-0 items-center gap-1 rounded-sm px-1.5 text-[11px] transition-colors",
          "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
          "data-popup-open:bg-surface-muted data-popup-open:text-foreground",
        )}
        aria-label={`知识版本：${current.name}`}
      >
        {current.name}
        <ChevronDown className="size-3 opacity-60" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner className={POPUP_LAYER} side="top" align="start" sideOffset={8}>
          <Menu.Popup
            className="w-64 origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-1 outline-hidden transition-[opacity,scale] duration-150 data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0"
            style={{ boxShadow: "var(--shadow-floating)" }}
          >
            {spaces.map((s) => (
              <Menu.Item
                key={s.code}
                className="flex cursor-default items-start gap-2 rounded-md px-2 py-1.5 outline-none select-none data-highlighted:bg-surface-muted"
                onClick={() => onSpaceChange(s.code)}
              >
                <Check
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0 text-bronze-strong",
                    s.code !== current.code && "opacity-0",
                  )}
                />
                <span className="min-w-0">
                  <span className="block text-[13px] text-foreground">{s.name}</span>
                  {s.description ? (
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {s.description}
                    </span>
                  ) : null}
                </span>
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/**
 * 回答档位选择器。
 *
 * 这是输入框下面**唯一**一个真有功能的控件（§11.7：没接上真实功能的按钮
 * 一个都不放）。它切的是后端的模型和写法：简答走 DeepSeek，详解走 Kimi。
 */
function ModePicker({
  mode,
  onModeChange,
}: {
  mode: AnswerMode;
  onModeChange: (next: AnswerMode) => void;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger
        className={cn(
          "flex h-6 shrink-0 items-center gap-1 rounded-sm px-1.5 text-[11px] transition-colors",
          "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
          "data-popup-open:bg-surface-muted data-popup-open:text-foreground",
        )}
        aria-label={`回答详细程度：${MODE_META[mode].label}`}
      >
        {MODE_META[mode].label}
        <ChevronDown className="size-3 opacity-60" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner className={POPUP_LAYER} side="top" align="start" sideOffset={8}>
          <Menu.Popup
            className="w-60 origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-1 outline-hidden transition-[opacity,scale] duration-150 data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0"
            style={{ boxShadow: "var(--shadow-floating)" }}
          >
            {ANSWER_MODES.map((m) => (
              <Menu.Item
                key={m}
                className="flex cursor-default items-start gap-2 rounded-md px-2 py-1.5 outline-none select-none data-highlighted:bg-surface-muted"
                onClick={() => onModeChange(m)}
              >
                <Check
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0 text-bronze-strong",
                    m !== mode && "opacity-0",
                  )}
                />
                <span className="min-w-0">
                  <span className="block text-[13px] text-foreground">{MODE_META[m].label}</span>
                  <span className="mt-0.5 block text-[11px] text-muted-foreground">
                    {MODE_META[m].hint}
                  </span>
                </span>
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

export function Composer({
  onSend,
  onStop,
  busy,
  draft,
  onDraftChange,
  mode,
  onModeChange,
  spaces = [],
  space = null,
  onSpaceChange,
  spaceLocked = false,
  placeholder = "问一个旺店通相关问题……",
  autoFocus = false,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  draft: string;
  onDraftChange: (text: string) => void;
  mode: AnswerMode;
  onModeChange: (next: AnswerMode) => void;
  /** 可选的知识版本。少于 2 个时选择器整个不显示（§11.7 假按钮） */
  spaces?: KnowledgeSpace[];
  space?: string | null;
  onSpaceChange?: (next: string) => void;
  /** 会话已经有消息了：版本在服务端钉死，这里降级成只读标签 */
  spaceLocked?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // 输入框自适应高度伸缩，最多 200px；外框的视觉高度保持稳定
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    onDraftChange("");
  }

  return (
    <div>
      <div
        className={cn(
          "flex flex-col rounded-2xl border border-border bg-surface transition-colors duration-150",
          // 聚焦：青铜描边 + 一层极淡的青铜底光，不用亮蓝色 ring
          "focus-within:border-bronze-border focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--bronze),transparent_88%)]",
        )}
      >
        <textarea
          ref={ref}
          rows={1}
          value={draft}
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-label="输入你的问题"
          // text-base + md:text-sm：iOS 上小于 16px 的输入框一聚焦就会放大整页
          className="max-h-[200px] min-h-12 w-full resize-none bg-transparent px-3.5 pt-3 text-base leading-relaxed outline-none placeholder:text-muted-foreground/70 md:text-[15px]"
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            // ⭐ 中文输入法选词时会触发 Enter，isComposing 为真时绝不提交
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />

        <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5 pt-1">
          <div className="flex min-w-0 items-center gap-2">
            <ModePicker mode={mode} onModeChange={onModeChange} />
            <SpacePicker
              spaces={spaces}
              space={space}
              onSpaceChange={onSpaceChange ?? (() => {})}
              locked={spaceLocked}
            />
            <span className="hidden truncate text-[11px] text-muted-foreground/60 sm:inline">
              Enter 发送 · Shift + Enter 换行
            </span>
          </div>

          {busy ? (
            <button
              type="button"
              onClick={onStop}
              title="停止生成"
              aria-label="停止生成"
              className="flex size-8 shrink-0 items-center justify-center rounded-md bg-destructive/12 text-destructive transition-colors hover:bg-destructive/20"
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!draft.trim()}
              title="发送问题"
              aria-label="发送问题"
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-md transition-colors",
                draft.trim()
                  ? "bg-primary text-primary-foreground hover:bg-[color-mix(in_oklch,var(--primary),var(--background)_12%)]"
                  : "cursor-not-allowed bg-surface-muted text-muted-foreground/50",
              )}
            >
              <ArrowUp className="size-4" />
            </button>
          )}
        </div>
      </div>

      <p className="mt-2 px-1 text-center text-[11px] text-muted-foreground/55">
        AI 回答基于知识库生成，请以最新系统设置为准
      </p>
    </div>
  );
}
