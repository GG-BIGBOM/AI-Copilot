"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, KeyRound, Loader2 } from "lucide-react";

import { AuthShell } from "@/components/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useRedirectIfAuthed } from "@/lib/auth-guard";

export default function RegisterPage() {
  const router = useRouter();
  const checking = useRedirectIfAuthed();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      // 邀请码后端会自己归一化（大小写、空格、连字符都认），前端不必先处理
      await api.register({ email, password, inviteCode });
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "注册失败，请检查邀请码或网络连接。");
      setBusy(false);
    }
  }

  if (checking) {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <span className="text-[13px] text-muted-foreground">正在验证登录状态…</span>
      </main>
    );
  }

  return (
    <AuthShell
      title="创建账号"
      description="本平台为内部邀请制，请输入专属邀请码完成注册"
      footer={
        <>
          已经拥有账号？{" "}
          <Link
            href="/login"
            className="font-medium text-foreground underline underline-offset-4 decoration-bronze-border hover:decoration-bronze"
          >
            直接登录
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="invite" className="flex items-center justify-between text-[13px] text-muted-foreground">
            <span>专属邀请码</span>
            <span className="text-[11px] text-muted-foreground/70">内部测试资格</span>
          </Label>
          <div className="relative">
            <Input
              id="invite"
              required
              placeholder="XXXX-XXXX"
              autoComplete="off"
              className="h-9 pl-8 font-mono uppercase tracking-widest"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
            />
            <KeyRound className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="email" className="text-[13px] text-muted-foreground">
            企业邮箱
          </Label>
          <Input
            id="email"
            type="email"
            placeholder="name@example.com"
            autoComplete="username"
            required
            className="h-9"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password" className="text-[13px] text-muted-foreground">
            设置密码
          </Label>
          <div className="relative">
            <Input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              required
              minLength={8}
              placeholder="请输入至少 8 位密码"
              className="h-9 pr-9"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm text-muted-foreground transition-colors hover:text-foreground"
              aria-label={showPassword ? "隐藏密码" : "显示密码"}
            >
              {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription className="text-[13px]">{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" size="lg" className="w-full" disabled={busy}>
          {busy ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              <span>正在注册…</span>
            </span>
          ) : (
            "注册并进入工作台"
          )}
        </Button>
      </form>
    </AuthShell>
  );
}
