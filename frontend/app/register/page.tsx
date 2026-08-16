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
        <div className="flex items-center gap-2.5 text-muted-foreground text-sm font-medium">
          <Loader2 className="size-4 animate-spin text-primary" />
          <span>正在验证登录状态…</span>
        </div>
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
            className="text-foreground font-semibold underline underline-offset-4 hover:opacity-80 transition-opacity"
          >
            直接登录
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="invite" className="text-xs font-semibold text-foreground flex items-center justify-between">
            <span>专属邀请码</span>
            <span className="text-[11px] text-muted-foreground font-normal">内部测试资格</span>
          </Label>
          <div className="relative">
            <Input
              id="invite"
              required
              placeholder="XXXX-XXXX"
              autoComplete="off"
              className="h-10 rounded-xl bg-muted/40 border-border/80 font-mono tracking-widest uppercase pl-9 focus-visible:ring-primary/20 text-xs sm:text-sm"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
            />
            <KeyRound className="size-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
          </div>
        </div>

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
