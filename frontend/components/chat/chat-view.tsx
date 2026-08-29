"use client";

import { useEffect, useMemo, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";

import { BrandMark } from "@/components/brand-mark";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { PromptStarters } from "@/components/chat/prompt-starters";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAnswerMode } from "@/lib/answer-mode";
import { API_BASE, api, type KnowledgeSpace } from "@/lib/api";
import type { CopilotUIMessage } from "@/lib/chat-types";

/**
 * 一次会话视图。父组件用 `key={chatId}` 强制重挂，切换历史对话时状态干净。
 *
 * ⭐ `chatId` 必须是 UUID。useChat 默认自己生成的是 nanoid，后端解析不出来
 * 就会每轮另开一条会话，多轮对话全散了——见 plan.md M3 的接口约定。
 *
 * 空会话和进行中的会话是**两套排版**：空的时候输入框在视觉中心偏上，
 * 是这一屏唯一的主角（§10）；一旦有了消息，它就退到底部停靠。
 */
export function ChatView({
  chatId,
  initialMessages,
  onConversationTouched,
}: {
  chatId: string;
  initialMessages: CopilotUIMessage[];
  onConversationTouched: () => void;
}) {
  const [draft, setDraft] = useState("");
  // 档位记在 localStorage 里，不进会话——它是"我习惯要多详细"，不是这段对话的属性
  const [mode, setMode] = useAnswerMode();

  // ⭐ 知识版本（M18）。**和档位不一样，它是这段对话的属性，不是个人偏好**：
  // 一条会话的版本在服务端是钉死的（换版本 = 新建会话）。所以它只是个
  // 本地 state，跟着 `key={chatId}` 一起重挂，不进 localStorage。
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [space, setSpace] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // ⚠️ 拉不到就当只有一个版本（选择器不显示），**不弹错**：
    // 知识版本是个可选功能，它的接口挂了不该让一个能正常提问的页面
    // 顶一条红色报错。后端不传 space 时会落到默认版本。
    api
      .knowledgeSpaces()
      .then((rows) => {
        if (alive) setSpaces(rows);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // 每次 render 都 new 一个 transport 会让 useChat 反复重建内部状态
  const transport = useMemo(
    () =>
      new DefaultChatTransport<CopilotUIMessage>({
        api: `${API_BASE}/api/chat`,
        // ⭐ 登录态在 HttpOnly cookie 里。本地开发 3000→8000 是跨源的，
        // 少了这行 cookie 根本不发，后端一律回 401
        credentials: "include",
      }),
    [],
  );

  const { messages, sendMessage, status, error, stop, regenerate } = useChat<CopilotUIMessage>({
    id: chatId,
    messages: initialMessages,
    transport,
    onFinish: onConversationTouched, // 新会话生成完，刷新左侧列表
  });

  const busy = status === "submitted" || status === "streaming";

  // ⭐ 档位要**每次发送时**带上，不能塞进 transport 的静态 body：
  // transport 是 useMemo 出来的，档位变了它不会重建，用户切换后
  // 下一句话还会按旧档位回答
  // ⚠️ `space` 只在**新会话的第一句**起作用：服务端对已有会话一律按会话
  // 原有的版本走（`_resolve_conversation`）。这里照样每次都带上，
  // 因为前端不知道服务端那条会话建没建——判据在服务端，只许有一处。
  const send = (text: string) => sendMessage({ text }, { body: { mode, space } });

  const composer = (
    <Composer
      draft={draft}
      onDraftChange={setDraft}
      busy={busy}
      onStop={stop}
      onSend={send}
      mode={mode}
      onModeChange={setMode}
      spaces={spaces}
      space={space}
      onSpaceChange={setSpace}
      // 已经聊起来了 = 版本钉死。选择器降级成只读标签，见 SpacePicker
      spaceLocked={messages.length > 0}
      placeholder={messages.length === 0 ? "问一个旺店通相关问题……" : "继续提问……"}
    />
  );

  /* ─── 空状态：品牌符号 + 一句话 + 输入框 + 场景入口 ─── */
  if (messages.length === 0) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* 视觉重心落在 42–45% 高度上：正正好居中会让下半屏显得空（§10.1） */}
        <div className="mx-auto flex min-h-full w-full max-w-[var(--content-text-max)] flex-col justify-center gap-7 px-4 pt-10 pb-[max(12vh,env(safe-area-inset-bottom))] sm:px-6">
          <div className="flex flex-col items-center gap-3 text-center">
            <BrandMark className="size-7 text-foreground/75" />
            <p className="text-[26px] font-semibold leading-tight tracking-tight text-foreground">
              今天想解决什么问题？
            </p>
          </div>

          {composer}

          <PromptStarters
            onPick={(text) => {
              setDraft("");
              send(text);
            }}
          />
        </div>
      </div>
    );
  }

  /* ─── 进行中的会话：正文在上，输入框停靠在底部 ─── */
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageList
        messages={messages}
        status={status}
        mode={mode}
        onRegenerate={() => regenerate({ body: { mode, space } })}
      />

      {/* pb 里带 safe-area：iPhone 上不加这个，输入框会被底部的横条压住 */}
      <div className="shrink-0 px-4 pt-1 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
        <div className="content-grid">
          <div>
            {error && (
              <Alert variant="destructive" className="mb-2">
                <AlertDescription className="flex items-center justify-between gap-3 text-[13px]">
                  <span>{error.message || "出错了，请稍后重试。"}</span>
                  <Button
                    size="xs"
                    variant="outline"
                    className="shrink-0"
                    onClick={() => regenerate({ body: { mode, space } })}
                  >
                    重试
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {composer}
          </div>
        </div>
      </div>
    </div>
  );
}
