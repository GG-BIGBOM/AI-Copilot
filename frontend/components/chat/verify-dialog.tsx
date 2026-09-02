"use client";

/**
 * 答案纠错：**这条答得不对 → 我直接改 → 提交给管理员审核。**
 *
 * ⚠️⚠️ **M16 改掉了这里最要紧的一句话：提交不再等于生效。**
 * 在那之前，任何登录用户点一下保存，那段文字就对全站立刻生效、无人审核——
 * 任何注册用户都能往公共知识库里塞内容，而站上没有任何地方看得出来。
 * 现在它进审核队列，管理员通过并发布之后才是这个知识版本下的标准答案。
 *
 * 所以这个弹窗的文案有一条硬要求：**不许让人以为改完就生效了。**
 * 说错的代价不是体验问题——他会改完就走，以为下次就对了，而实际上
 * 那条纠错可能永远没人审。按钮上写「提交纠错」，回执里写「已提交，等待审核」。
 *
 * 它做四件事，一件不多：
 *
 *   1. 把**现在这条回答**原样放进一个可编辑的框
 *   2. 收一句「哪里不对」——审核的人需要它，否则只能把两段文字读一遍自己猜
 *   3. 让人贴截图（ERP 的操作步骤里，图往往就是那一步本身）
 *   4. 提交（带 traceId，原问答快照由服务端自己取）
 *   5. 回执里说清楚**这只是提交**
 *
 * ⚠️ **截图区的唯一事实来源是正文本身。** 缩略图是从草稿里的
 * `/api/images/{id}` 现解出来的，删除就是把那段 Markdown 从正文里删掉——
 * 单独存一份"我传过哪些图"的状态，迟早会和正文对不上：用户手动删掉一行
 * 图片语法之后，缩略图还在，他以为图还会跟着提交。
 */

import { useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Check, ImagePlus, Loader2, X } from "lucide-react";

import { insertShot } from "@/lib/insert-shot";

import { Button } from "@/components/ui/button";
import { api, API_BASE, ApiError } from "@/lib/api";

/** 正文里一段图片语法：`![截图](/api/images/{uuid})` */
const SHOT_RE = /!\[[^\]]*\]\((\/api\/images\/[0-9a-fA-F-]{36})\)/g;

/** 草稿里现在引用着哪几张截图。**从正文解，不另存一份状态**（见文件头） */
function shotsIn(draft: string): string[] {
  const out: string[] = [];
  for (const m of draft.matchAll(SHOT_RE)) {
    if (!out.includes(m[1])) out.push(m[1]);
  }
  return out;
}

const POPUP =
  "fixed left-1/2 top-[8vh] z-[60] flex max-h-[84vh] w-[min(680px,calc(100vw-2rem))] " +
  "-translate-x-1/2 flex-col overflow-y-auto rounded-2xl border border-border bg-popover p-5 " +
  "text-popover-foreground outline-hidden transition-[opacity,scale] duration-200 " +
  "ease-[cubic-bezier(0.16,1,0.3,1)] data-starting-style:scale-[0.98] " +
  "data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0";

export function VerifyDialog({
  open,
  onOpenChange,
  question,
  answer,
  traceId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 用户当初问的那句话。只用来显示——服务端会自己从会话里取一份权威的 */
  question: string;
  /** 模型这次给的答案，作为编辑起点 */
  answer: string;
  /** 这一轮在 request_trace 里的行号。**没有它就提交不了**（见下） */
  traceId?: string | null;
}) {
  const [draft, setDraft] = useState(answer);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 用户有没有点进过正文。**决定贴图插在哪儿**（见 insertAtCursor）。
  //
  // ⚠️ **不能用 `selectionStart === 0` 反推。** 从未聚焦过的 textarea 和
  // 「用户特意把光标点到开头」是同一个值 0，反推会把后者判错。
  // 只有 onFocus 这一手是准的。
  //
  // ⚠️ 是 state 不是 ref：它要跟着「弹窗重开」一起重置，而那个重置发生在
  // 渲染期（见下面 wasOpen 那段），渲染期写 ref 是 React 明令禁止的
  // （`react-hooks/refs`，lint 会当场变红）。setState 在那儿反而是对的。
  const [touched, setTouched] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const shots = shotsIn(draft);

  /** 把一段 Markdown 插到光标处；**用户从没点进正文时接在末尾**。
   *
   * 位置计算在 `lib/insert-shot.ts`，那里有测试（前端只跑 `lib/*.test.ts`，
   * 组件里的分支盖不到——这个「没点正文」的分支正是这么错掉的）。
   */
  function insertAtCursor(snippet: string) {
    const el = textareaRef.current;
    const cursor = el && touched ? el.selectionStart : null;
    setDraft((current) => insertShot(current, snippet, cursor));
  }

  async function attach(file: File | null | undefined) {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const shot = await api.uploadCorrectionImage(file);
      insertAtCursor(shot.markdown);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "图片上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = ""; // 同一张图能再选一次
    }
  }

  /** 从正文里删掉这张图。**删的是文字，不是某个列表里的一项** */
  function removeShot(url: string) {
    setDraft((current) =>
      current.replace(new RegExp(`!\\[[^\\]]*\\]\\(${url}\\)\\n*`, "g"), ""),
    );
  }

  // 每次打开都用**这次**的答案重置草稿。在渲染期比对 open 而不是写 effect：
  // effect 里同步 setState 会触发级联渲染，React 19 的规则直接判错
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setDraft(answer);
      setReason("");
      setError(null);
      setDone(null);
      // ⚠️ 这一项必须跟着重置：草稿换成了新答案，上一次的光标位置
      // 在新正文里指向的是另一段话，照着插会插到莫名其妙的地方。
      setTouched(false);
    }
  }

  async function submit() {
    if (!traceId) return;
    setBusy(true);
    setError(null);
    try {
      await api.submitCorrection({
        traceId,
        correctedAnswer: draft,
        reason,
      });
      setDone("已提交，等待管理员审核。审核通过并发布后，所有人都会用你改的这版。");
      setTimeout(() => onOpenChange(false), 2200);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  const unchanged = draft.trim() === answer.trim();
  // ⚠️ **在这儿拦，不是在提交时拦**：让人写完整段答案、点了提交才说
  // "这一轮没法纠"，比一开始就说糟得多。
  // 没有 traceId 的来路：老会话，或者这一轮的台账没记上消息 id
  const cannot = !traceId;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-[60] bg-black/25 transition-opacity duration-150 data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <Dialog.Popup className={POPUP} style={{ boxShadow: "var(--shadow-floating)" }}>
          <Dialog.Title className="text-base font-semibold text-foreground">
            纠错这条回答
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            {cannot ? (
              <>这一轮是从历史里读出来的，没有可追溯的记录，没法纠错。可以重新问一次再改。</>
            ) : (
              <>
                改完提交给管理员审核。通过并发布后，问到
                <span className="mx-1 rounded-sm bg-surface-muted px-1.5 py-0.5 font-medium text-foreground">
                  {question.length > 40 ? `${question.slice(0, 40)}…` : question}
                </span>
                的人都会拿到你改的这版。
              </>
            )}
          </Dialog.Description>

          {!cannot && (
            <>
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onFocus={() => setTouched(true)}
                onPaste={(e) => {
                  // 截图基本都是粘贴进来的（QQ / 微信 / Win+Shift+S），
                  // 让人先存成文件再"选择文件"是多余的两步
                  const item = Array.from(e.clipboardData.items).find((i) =>
                    i.type.startsWith("image/"),
                  );
                  if (!item) return;
                  e.preventDefault();
                  void attach(item.getAsFile());
                }}
                spellCheck={false}
                className="mt-4 min-h-[14rem] resize-none rounded-lg border border-border bg-surface-subtle px-3 py-2.5 font-mono text-[13px] leading-relaxed text-foreground outline-hidden transition-colors focus:border-ring focus:bg-background"
                aria-label="正确的答案"
              />

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
                  className="hidden"
                  onChange={(e) => void attach(e.target.files?.[0])}
                />
                <Button
                  variant="ghost"
                  className="h-8 px-2 text-[13px]"
                  disabled={uploading}
                  onClick={() => fileRef.current?.click()}
                >
                  {uploading ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <ImagePlus className="size-3.5" />
                  )}
                  贴张截图
                </Button>
                <span className="text-[12px] text-muted-foreground">
                  也可以直接 Ctrl+V 粘贴。截图只有你和管理员看得到，
                  <span className="font-medium text-foreground">发布之后所有人可见</span>。
                </span>
              </div>

              {shots.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-2">
                  {shots.map((url) => (
                    <li key={url} className="relative">
                      {/* eslint-disable-next-line @next/next/no-img-element -- 用户刚传上来的图，没有尺寸信息 */}
                      <img
                        src={`${API_BASE}${url}`}
                        alt="已贴的截图"
                        className="size-16 rounded-md border border-border object-cover"
                      />
                      <button
                        type="button"
                        onClick={() => removeShot(url)}
                        aria-label="从正文里删掉这张截图"
                        className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full border border-border bg-surface text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <X className="size-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <label
                className="mt-3 text-[13px] font-medium text-foreground"
                htmlFor="correction-reason"
              >
                哪里不对
              </label>
              <input
                id="correction-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="例如：第二步的菜单路径错了，实际在【物流管理】下面"
                className="mt-1 rounded-lg border border-border bg-surface-subtle px-3 py-2 text-[13px] text-foreground outline-hidden transition-colors focus:border-ring focus:bg-background"
              />
              <p className="mt-2 text-[12px] text-muted-foreground">
                支持 Markdown。
                <span className="font-medium text-foreground">提交不等于生效</span>
                ——要管理员审核通过并发布之后，它才会对这个知识版本下的所有人生效。
              </p>
            </>
          )}

          {error && (
            <p className="mt-3 text-[13px] text-destructive" role="alert">
              {error}
            </p>
          )}
          {done && (
            <p className="mt-3 flex items-center gap-1.5 text-[13px] text-success" role="status">
              <Check className="size-3.5" />
              {done}
            </p>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              {cannot ? "知道了" : "取消"}
            </Button>
            {!cannot && (
              <Button onClick={submit} disabled={busy || unchanged || !draft.trim() || !reason.trim()}>
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                {unchanged ? "还没有改动" : !reason.trim() ? "还差一句「哪里不对」" : "提交纠错"}
              </Button>
            )}
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
