"use client";

/**
 * 「我的文档」：上传、看解析状态、删除。
 *
 * 两个设计决定：
 *
 * 1. **状态靠轮询，不上 WebSocket。** 只有「排队中/解析中」的文档在时才轮询，
 *    全都跑完就自己停。服务器 1.6GB，为一个几十秒才变一次的状态常驻一条
 *    WebSocket 不划算（plan.md 七·5：不为假想需求先付工程费）。
 * 2. **上传后立刻把返回的那一行插进列表**，不等下一轮轮询。否则用户点完上传，
 *    要盯着一个没有任何变化的页面等三秒——会以为没传上去，然后再点一次。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  Trash2,
  Upload,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, ApiError, DOC_IN_PROGRESS, type DocumentSummary } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth-guard";
import { cn } from "@/lib/utils";

// 图片和扫描件 PDF 走视觉模型转写，比文本解析慢（一页约 5–10 秒），
// 但对用户是同一条路：传上去、等状态转绿。
// ⚠️ 要和后端 `upload_allowed_suffixes` 保持一致——这里多写一个类型，
// 用户传上去只会收到一句「不支持的文件类型」，而他明明是照着提示传的
const ACCEPT = ".md,.txt,.docx,.pptx,.pdf,.png,.jpg,.jpeg,.webp,.bmp";
const POLL_MS = 3000;

const STATUS: Record<
  DocumentSummary["status"],
  { label: string; variant: "secondary" | "outline" | "destructive"; icon: typeof Clock }
> = {
  pending: { label: "排队中", variant: "outline", icon: Clock },
  running: { label: "解析中", variant: "secondary", icon: Loader2 },
  done: { label: "已完成", variant: "secondary", icon: CheckCircle2 },
  failed: { label: "解析失败", variant: "destructive", icon: AlertCircle },
};

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
    : d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function DocumentsPage() {
  const auth = useRequireAuth();

  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

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

  async function send(files: FileList | File[] | null) {
    const list = Array.from(files ?? []);
    if (!list.length) return;

    setBusy(true);
    setNotice(null);
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
    }

    setBusy(false);
    setLoaded(true);
    if (failures.length) {
      setNotice({ kind: "err", text: failures.join("；") });
    } else if (duplicates) {
      setNotice({ kind: "ok", text: "这份文件已经在库里了，沿用原来那一篇。" });
    } else {
      setNotice({ kind: "ok", text: "上传完成，正在后台解析。" });
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
        <div className="flex flex-col items-center gap-3 text-sm text-muted-foreground">
          <Loader2 className="size-5 animate-spin text-primary" />
          <span>正在载入…</span>
        </div>
      </main>
    );
  }

  return (
    <main className="h-full overflow-y-auto bg-background">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:py-10">
        <div className="mb-6 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-foreground sm:text-xl">我的文档</h1>
            <p className="mt-1 text-xs text-muted-foreground">
              上传的文档只有你自己能检索到，公共知识库不受影响。
            </p>
          </div>
          {/* 用 Link 而不是 Button+render：和 login/register 页的写法保持一致 */}
          <Link
            href="/chat"
            className="flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            <span className="hidden sm:inline">回到对话</span>
          </Link>
        </div>

        {/* 拖拽 / 点击上传 */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            send(e.dataTransfer.files);
          }}
          className={cn(
            "rounded-2xl border-2 border-dashed p-6 text-center transition-colors sm:p-8",
            dragging ? "border-primary bg-primary/5" : "border-border bg-card",
          )}
        >
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            {busy ? <Loader2 className="size-5 animate-spin" /> : <Upload className="size-5" />}
          </div>
          <p className="text-sm font-medium text-foreground">
            {busy ? "正在上传…" : "把文件拖到这里，或点下面的按钮选择"}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            支持 md / txt / docx / pptx / pdf / 图片（截图会自动转成文字），单份不超过 20MB
          </p>
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
            size="sm"
            className="mt-4 gap-1.5 rounded-xl text-xs"
            disabled={busy}
            onClick={() => fileInput.current?.click()}
          >
            <Upload className="size-3.5" />
            选择文件
          </Button>
        </div>

        {notice && (
          <Alert
            variant={notice.kind === "err" ? "destructive" : "default"}
            className="mt-4 rounded-xl"
          >
            <AlertDescription className="text-xs">{notice.text}</AlertDescription>
          </Alert>
        )}

        {/* 列表 */}
        <div className="mt-6 space-y-2">
          {!loaded ? (
            <p className="py-10 text-center text-xs text-muted-foreground">正在载入文档…</p>
          ) : docs.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <div className="flex size-10 items-center justify-center rounded-xl bg-muted">
                <FileText className="size-5 text-muted-foreground opacity-50" />
              </div>
              <p className="text-xs font-medium text-foreground/80">还没有上传过文档</p>
              <p className="text-[11px] text-muted-foreground">
                传一份操作手册上来，提问时就能引用到它
              </p>
            </div>
          ) : (
            docs.map((doc) => {
              const s = STATUS[doc.status];
              const Icon = s.icon;
              return (
                <div
                  key={doc.id}
                  className="flex items-start gap-3 rounded-xl border border-border bg-card p-3 transition-colors hover:border-border/80"
                >
                  <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
                    <FileText className="size-4 text-muted-foreground" />
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground" title={doc.title}>
                        {doc.title}
                      </span>
                      <Badge variant={s.variant} className="gap-1 text-[10px]">
                        <Icon className={cn("size-2.5", doc.status === "running" && "animate-spin")} />
                        {s.label}
                      </Badge>
                    </div>

                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                      {doc.status === "done" && <span>{doc.chunk_count} 个检索片段</span>}
                      {doc.size_bytes ? <span>{formatSize(doc.size_bytes)}</span> : null}
                      <span>{formatTime(doc.created_at)}</span>
                    </div>

                    {/* 失败原因要显示出来。只写「失败」的话用户完全不知道下一步该干什么 */}
                    {doc.error && (
                      <p
                        className={cn(
                          "mt-1.5 text-[11px]",
                          doc.status === "failed" ? "text-destructive" : "text-muted-foreground",
                        )}
                      >
                        {doc.error}
                      </p>
                    )}
                  </div>

                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => remove(doc)}
                    title="删除"
                    aria-label={`删除 ${doc.title}`}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </main>
  );
}
