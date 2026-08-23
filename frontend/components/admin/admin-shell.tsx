"use client";

/**
 * 管理台的外壳：导航、时间范围、页头。三个页面共用。
 *
 * ⚠️ **这一层不做任何鉴权。** 页面各自调 `useRequireAdmin()`（体验），
 * 真正的门在 FastAPI 的 `CurrentAdmin`（安全）。外壳只是不想把同一段
 * 导航写三遍。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import type { AdminRange } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin", label: "概览" },
  { href: "/admin/users", label: "用户" },
  { href: "/admin/feedback", label: "反馈" },
] as const;

// 后端只认这三个（`Range` 是 Literal），多写一个会当场 422
export const RANGES: { value: AdminRange; label: string }[] = [
  { value: "24h", label: "24 小时" },
  { value: "7d", label: "7 天" },
  { value: "30d", label: "30 天" },
];

export function RangeTabs({
  value,
  onChange,
}: {
  value: AdminRange;
  onChange: (r: AdminRange) => void;
}) {
  return (
    <div className="flex items-center gap-0.5">
      {RANGES.map((r) => (
        <button
          key={r.value}
          type="button"
          onClick={() => onChange(r.value)}
          aria-pressed={value === r.value}
          className={cn(
            "h-8 rounded-md px-2.5 text-[13px] transition-colors",
            value === r.value
              ? "bg-surface-muted font-medium text-foreground"
              : "text-muted-foreground hover:bg-surface-subtle hover:text-foreground",
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

export function AdminShell({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <main className="h-full overflow-y-auto bg-background">
      <div className="mx-auto w-full max-w-[var(--content-wide-max)] px-4 py-8 sm:px-6">
        <Link
          href="/chat"
          className="-ml-1.5 mb-2 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[13px] text-muted-foreground transition-colors hover:bg-surface-subtle hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          回到对话
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
            {subtitle && <p className="mt-1 text-[13px] text-muted-foreground">{subtitle}</p>}
          </div>
          {actions}
        </div>

        <nav className="mt-6 flex items-center gap-0.5 border-b border-border-subtle">
          {NAV.map((n) => {
            // `/admin/users/` 这种带尾斜杠的也要算中（trailingSlash: true）
            const active = n.href === "/admin"
              ? pathname === "/admin" || pathname === "/admin/"
              : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors",
                  active
                    ? "border-bronze-strong font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-6">{children}</div>
      </div>
    </main>
  );
}

/** 一个指标。`hint` 用来写口径——数字本身说不清「分母是谁」。 */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "warn" | "bad";
}) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface p-3.5">
      <div className="text-[13px] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 text-xl font-semibold tabular-nums tracking-tight",
          tone === "default" && "text-foreground",
          tone === "warn" && "text-bronze-strong",
          tone === "bad" && "text-destructive",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-[12px] leading-snug text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export function formatMs(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return "0";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
