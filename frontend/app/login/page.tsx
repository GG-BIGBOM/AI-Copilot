"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, Loader2 } from "lucide-react";

import { AuthShell } from "@/components/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useRedirectIfAuthed } from "@/lib/auth-guard";

export default function LoginPage() {
  const router = useRouter();
  const checking = useRedirectIfAuthed();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.login({ email, password });
      // replace 而不是 push：登录成功后按返回键不该回到登录页
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请检查账号密码或网络连接。");
      setBusy(false);
    }
  }

  if (checking) {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <div className="flex items-center gap-2.5 text-muted-foreground text-sm font-medium">
          <Loader2 className="size-4 animate-spin text-primary" />
          <span>正在验证登录状态…</span>
        </div>
      </main>
    );
  }

  return (
    <AuthShell
      title="欢迎回来"
      description="登录以访问旗舰版 ERP 企业知识库助手"
      footer={
        <>
          还没有账号？{" "}
          <Link
            href="/register"
            className="text-foreground font-semibold underline underline-offset-4 hover:opacity-80 transition-opacity"
          >
            使用邀请码注册
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="email" className="text-xs font-semibold text-foreground">
            企业邮箱
          </Label>
          <Input
            id="email"
            type="email"
            placeholder="name@example.com"
            autoComplete="username"
            required
            className="h-10 rounded-xl bg-muted/40 border-border/80 focus-visible:ring-primary/20 text-xs sm:text-sm"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password" className="text-xs font-semibold text-foreground">
            账号密码
          </Label>
          <div className="relative">
            <Input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              placeholder="请输入密码"
              required
              className="h-10 rounded-xl bg-muted/40 border-border/80 pr-10 focus-visible:ring-primary/20 text-xs sm:text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="text-muted-foreground hover:text-foreground absolute right-3 top-1/2 -translate-y-1/2 transition-colors focus:outline-none"
              aria-label={showPassword ? "隐藏密码" : "显示密码"}
            >
              {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
        </div>

        {error && (
          <Alert variant="destructive" className="py-2.5 rounded-xl border-destructive/40 shadow-xs">
            <AlertDescription className="text-xs">{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full h-10 font-semibold rounded-xl shadow-xs transition-all active:scale-[0.99]" disabled={busy}>
          {busy ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              <span>正在登录…</span>
            </span>
          ) : (
            "立即登录"
          )}
        </Button>
      </form>
    </AuthShell>
  );
}
