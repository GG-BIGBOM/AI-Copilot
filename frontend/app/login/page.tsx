"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

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
        <p className="text-muted-foreground text-sm">正在加载…</p>
      </main>
    );
  }

  return (
    <AuthShell
      title="登录"
      description="旗舰版 ERP 知识库助手"
      footer={
        <>
          还没有账号？{" "}
          <Link href="/register" className="text-foreground underline underline-offset-4">
            用邀请码注册
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="email">邮箱</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">密码</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </Button>
      </form>
    </AuthShell>
  );
}
