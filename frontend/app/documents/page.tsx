"use client";

/**
 * 知识库工作台：上传、看解析状态、删除（UI_OPTIMIZATION_SPEC §19）。
 *
 * 三个设计决定：
 *
 * 1. **状态靠轮询，不上 WebSocket。** 只有「排队中/解析中」的文档在时才轮询，
 *    全都跑完就自己停。服务器 1.6GB，为一个几十秒才变一次的状态常驻一条
 *    WebSocket 不划算（plan.md 七·5：不为假想需求先付工程费）。
 * 2. **上传后立刻把返回的那一行插进列表**，不等下一轮轮询。否则用户点完上传，
 *    要盯着一个没有任何变化的页面等三秒——会以为没传上去，然后再点一次。
 * 3. **上传区不常驻。** 拖拽提示只在真的拖着文件进来时才铺满整页，
 *    平时那块地方留给文档列表本身。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Menu } from "@base-ui/react/menu";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  MoreHorizontal,
  Search,
  Trash2,
  Upload,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError, DOC_IN_PROGRESS, type DocumentSummary } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth-guard";
import { POPUP_LAYER } from "@/lib/layers";
import { cn } from "@/lib/utils";

// 图片和扫描件 PDF 走视觉模型转写，比文本解析慢（一页约 5–10 秒），
// 但对用户是同一条路：传上去、等状态转绿。
// ⚠️ 要和后端 `upload_allowed_suffixes` 保持一致——这里多写一个类型，
// 用户传上去只会收到一句「不支持的文件类型」，而他明明是照着提示传的
const ACCEPT = ".md,.txt,.docx,.pptx,.xlsx,.pdf,.png,.jpg,.jpeg,.webp,.bmp";
const POLL_MS = 3000;

/** 状态词面向用户，不是后端枚举（§19.5） */
const STATUS: Record<
  DocumentSummary["status"],
  { label: string; icon: typeof Clock; className: string }
> = {
  pending: { label: "排队中", icon: Clock, className: "text-muted-foreground" },
  running: { label: "解析中", icon: Loader2, className: "text-bronze-strong" },
  done: { label: "可用", icon: CheckCircle2, className: "text-success" },
  failed: { label: "失败", icon: AlertCircle, className: "text-destructive" },
};

const FILTERS = [
  { key: "all", label: "全部" },
  { key: "progress", label: "处理中" },
  { key: "done", label: "可用" },
  { key: "failed", label: "失败" },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

function formatSize(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export default function DocumentsPage() {
  const auth = useRequireAuth();

  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // 串行上传队列：[0] 是正在传的那份，剩下的在等
  const [queue, setQueue] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  const busy = queue.length > 0;

  // 写成 `.then()` 而不是 async/await：`set-state-in-effect` 那条 React 规则
  // 认的是「effect 体里同步调了 setState」，async 函数会被它算进去
  const refresh = useCallback(() => {
    api
      .documents()
      .then((list) => {
        setDocs(list);
        setLoaded(true);
      })
      .catch(() => {
        /* 拉不到列表不该弹错——下一轮轮询会自己好 */
      });
  }, []);

  useEffect(() => {
    if (auth.status !== "authed") return;
    refresh();
  }, [auth.status, refresh]);

  // 只在还有文档没解析完时轮询，全好了就停。空转的定时器是白烧服务器的
  const pending = docs.some((d) => DOC_IN_PROGRESS.includes(d.status));
  useEffect(() => {
    if (auth.status !== "authed" || !pending) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [auth.status, pending, refresh]);

  const chunkTotal = useMemo(() => docs.reduce((sum, d) => sum + (d.chunk_count || 0), 0), [docs]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return docs.filter((d) => {
      if (q && !d.title.toLowerCase().includes(q)) return false;
      if (filter === "progress") return DOC_IN_PROGRESS.includes(d.status);
      if (filter === "done") return d.status === "done";
      if (filter === "failed") return d.status === "failed";
      return true;
    });
  }, [docs, filter, search]);

  async function send(files: FileList | File[] | null) {
    const list = Array.from(files ?? []);
    if (!list.length) return;

    setNotice(null);
    setQueue(list.map((f) => f.name));
    const failures: string[] = [];
    let duplicates = 0;

    // 一份一份传。并发上去会让 1.6GB 的服务器同时收好几个 20MB 的流
    for (const file of list) {
      try {
        const result = await api.uploadDocument(file);
        if (result.duplicate) duplicates += 1;
        // 立刻插进列表，别让用户对着没变化的页面等下一轮轮询
        setDocs((prev) => [result.document, ...prev.filter((d) => d.id !== result.document.id)]);
      } catch (e) {
        failures.push(`${file.name}：${e instanceof ApiError ? e.message : "上传失败"}`);
      }
      setQueue((prev) => prev.slice(1));
    }

    setLoaded(true);
    if (failures.length) {
      setNotice({ kind: "err", text: failures.join("；") });
    } else if (duplicates) {
      setNotice({ kind: "ok", text: "这份文件已经在库里了，沿用原来那一篇。" });
    } else {
      setNotice({ kind: "ok", text: "上传完成，正在后台生成知识索引。" });
    }
  }

  async function remove(doc: DocumentSummary) {
    if (!window.confirm(`删除《${doc.title}》？它的检索内容会一并删除。`)) return;
    // 先从列表里拿掉，失败再放回去——删除是个很快的操作，转圈反而显得卡
    setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    try {
      await api.deleteDocument(doc.id);
    } catch (e) {
      setNotice({ kind: "err", text: e instanceof ApiError ? e.message : "删除失败" });
      refresh();
    }
  }

  if (auth.status !== "authed") {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <span className="text-[13px] text-muted-foreground">正在载入…</span>
      </main>
    );
  }

  return (
    <main
      className="relative h-full overflow-y-auto bg-background"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        // 只有真的离开了整个页面才收起提示，掠过子元素不算
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        send(e.dataTransfer.files);
      }}
    >
      {/* 拖拽提示只在拖进来时出现，平时不占地方（§19.2） */}
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-8">
          <div className="flex w-full max-w-md flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-bronze-border p-10 text-center">
            <Upload className="size-5 text-bronze-strong" />
            <p className="text-sm font-medium text-foreground">松手就开始上传</p>
            <p className="text-[13px] text-muted-foreground">支持 md / txt / docx / pptx / xlsx / pdf / 图片</p>
          </div>
        </div>
      )}

      <div className="mx-auto w-full max-w-[var(--content-wide-max)] px-4 py-8 sm:px-6">
        {/* ─── 页头 ─── */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              href="/chat"
              className="-ml-1.5 mb-2 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[13px] text-muted-foreground transition-colors hover:bg-surface-subtle hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
              回到对话
            </Link>
            <h1 className="text-lg font-semibold tracking-tight text-foreground">知识库</h1>
            <p className="mt-1 text-[13px] text-muted-foreground">
              {loaded && docs.length > 0
                ? `${docs.length} 个文档 · ${chunkTotal} 个知识片段 · 拖文件到页面任意位置即可上传`
                : "上传的文档只有你自己能检索到，公共知识库不受影响。"}
            </p>
          </div>

          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => {
              send(e.target.files);
              // 清掉 value：否则选同一个文件第二次不会触发 change
              e.target.value = "";
            }}
          />
          <Button
            className="mt-7 shrink-0 gap-1.5"
            disabled={busy}
            onClick={() => fileInput.current?.click()}
          >
            <Upload className="size-3.5" />
            上传文档
          </Button>
        </div>

        {/* ─── 上传队列（串行，所以队列对用户是有意义的信息）─── */}
        {busy && (
          <div className="mt-6 rounded-lg border border-border-subtle bg-surface-subtle p-3">
            <div className="flex items-center gap-2 text-[13px] text-foreground">
              <Loader2 className="size-3.5 animate-spin text-bronze-strong" />
              <span className="truncate">正在上传 {queue[0]}</span>
            </div>
            {queue.length > 1 && (
              <ul className="mt-2 space-y-1 border-t border-border-subtle pt-2 text-[13px] text-muted-foreground">
                {queue.slice(1).map((name) => (
                  <li key={name} className="truncate">
                    等待上传 · {name}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {notice && (
          <Alert variant={notice.kind === "err" ? "destructive" : "default"} className="mt-6">
            <AlertDescription className="text-[13px]">{notice.text}</AlertDescription>
          </Alert>
        )}

        {/* ─── 搜索 + 状态筛选 ─── */}
        {docs.length > 0 && (
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <div className="relative min-w-52 flex-1">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="搜索文档…"
                aria-label="搜索文档"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8"
              />
            </div>

            <div className="flex items-center gap-0.5">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  aria-pressed={filter === f.key}
                  className={cn(
                    "h-8 rounded-md px-2.5 text-[13px] transition-colors",
                    filter === f.key
                      ? "bg-surface-muted font-medium text-foreground"
                      : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ─── 列表 ─── */}
        <div className="mt-4">
          {!loaded ? (
            <p className="py-16 text-center text-[13px] text-muted-foreground">正在载入文档…</p>
          ) : docs.length === 0 ? (
            /* 空状态本身就是投放区。整页都能接住拖进来的文件（handler 在最外层
               的 main 上），但**得让人看出来**——原来只写「还没有上传过文档」，
               虚线框要等你已经开始拖才出现，等于没有 */
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="flex w-full flex-col items-center gap-1.5 rounded-xl border border-dashed border-border py-20 text-center transition-colors hover:border-bronze-border hover:bg-surface-subtle"
            >
              <Upload className="size-5 text-muted-foreground/60" />
              <span className="text-sm font-medium text-foreground">
                把文件拖到这里，或点击选择
              </span>
              <span className="text-[13px] text-muted-foreground">
                支持 md / txt / docx / pptx / xlsx / pdf / 图片，单份不超过 20MB
              </span>
              <span className="mt-1 text-[13px] text-muted-foreground/70">
                传一份操作手册上来，提问时就能引用到它
              </span>
            </button>
          ) : visible.length === 0 ? (
            <p className="py-16 text-center text-[13px] text-muted-foreground">没有符合条件的文档</p>
          ) : (
            <>
              <div className="grid grid-cols-[1fr_5.5rem_6.5rem_2rem] gap-3 border-b border-border-subtle px-2 pb-1.5 text-[11px] font-medium text-muted-foreground/70 max-sm:hidden">
                <span>文件</span>
                <span>状态</span>
                <span>更新时间</span>
                <span />
              </div>

              <ul>
                {visible.map((doc) => {
                  const s = STATUS[doc.status];
                  const Icon = s.icon;
                  return (
                    <li
                      key={doc.id}
                      className="group grid grid-cols-[1fr_2rem] items-center gap-3 border-b border-border-subtle px-2 py-2.5 transition-colors hover:bg-surface-subtle sm:grid-cols-[1fr_5.5rem_6.5rem_2rem]"
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <FileText className="size-4 shrink-0 text-muted-foreground/60" />
                        <div className="min-w-0">
                          <p className="truncate text-sm text-foreground" title={doc.title}>
                            {doc.title}
                          </p>
                          <p className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                            {doc.status === "done" && <span>{doc.chunk_count} 个知识片段</span>}
                            {doc.size_bytes ? <span>{formatSize(doc.size_bytes)}</span> : null}
                            <span className="sm:hidden">{formatTime(doc.updated_at)}</span>
                          </p>
                          {/* 失败原因要显示出来。只写「失败」的话用户完全不知道下一步该干什么 */}
                          {doc.error && (
                            <p
                              className={cn(
                                "mt-1 text-[11px]",
                                doc.status === "failed" ? "text-destructive" : "text-muted-foreground",
                              )}
                            >
                              {doc.error}
                            </p>
                          )}
                        </div>
                      </div>

                      <span
                        className={cn(
                          "flex items-center gap-1.5 text-[13px] max-sm:hidden",
                          s.className,
                        )}
                      >
                        <Icon className={cn("size-3.5", doc.status === "running" && "animate-spin")} />
                        {s.label}
                      </span>

                      <span className="text-[13px] text-muted-foreground max-sm:hidden">
                        {formatTime(doc.updated_at)}
                      </span>

                      <Menu.Root>
                        <Menu.Trigger
                          className={cn(
                            "flex size-7 items-center justify-center rounded-md text-muted-foreground transition-opacity",
                            "opacity-0 hover:bg-surface-muted hover:text-foreground",
                            "focus-visible:opacity-100 group-hover:opacity-100 data-popup-open:opacity-100 max-sm:opacity-100",
                          )}
                          aria-label={`《${doc.title}》的更多操作`}
                        >
                          <MoreHorizontal className="size-4" />
                        </Menu.Trigger>
                        <Menu.Portal>
                          <Menu.Positioner
                            className={POPUP_LAYER}
                            side="bottom"
                            align="end"
                            sideOffset={4}
                          >
                            <Menu.Popup
                              className="min-w-36 origin-[var(--transform-origin)] rounded-xl border border-border bg-popover p-1 outline-hidden transition-[opacity,scale] duration-150 data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0"
                              style={{ boxShadow: "var(--shadow-floating)" }}
                            >
                              <Menu.Item
                                className="flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-muted-foreground outline-none select-none data-highlighted:bg-destructive/10 data-highlighted:text-destructive"
                                onClick={() => remove(doc)}
                              >
                                <Trash2 className="size-3.5" />
                                删除文档
                              </Menu.Item>
                            </Menu.Popup>
                          </Menu.Positioner>
                        </Menu.Portal>
                      </Menu.Root>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
