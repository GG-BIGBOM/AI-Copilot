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
      setError(err instanceof ApiError ? err.message : "登录失败，请重试。");
      setBusy(false);
    }
  }

  if (checking) {
    return (
      <main className="flex h-full items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="size-4 animate-spin" />
          <span>正在验证身份…</span>
        </div>
      </main>
    );
  }

  return (
    <AuthShell
      title="欢迎回来"
      description="登录以访问旗舰版 ERP 知识库助手"
      footer={
        <>
          还没有账号？{" "}
          <Link
            href="/register"
            className="text-foreground font-medium underline underline-offset-4 hover:opacity-80 transition-opacity"
          >
            用邀请码注册
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="email" className="text-xs font-medium">
            邮箱
          </Label>
          <Input
            id="email"
            type="email"
            placeholder="name@example.com"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password" className="text-xs font-medium">
            密码
          </Label>
          <div className="relative">
            <Input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              className="pr-10"
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
          <Alert variant="destructive" className="py-2.5">
            <AlertDescription className="text-xs">{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full font-medium" disabled={busy}>
          {busy ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              <span>登录中…</span>
            </span>
          ) : (
            "登录"
          )}
        </Button>
      </form>
    </AuthShell>
  );
}
