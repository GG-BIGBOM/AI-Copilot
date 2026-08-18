import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** 登录页和注册页共用的外框。 */
export function AuthShell({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <main className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-12 bg-background">
      {/* 极淡的环境光晕 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 flex items-center justify-center overflow-hidden"
      >
        <div className="size-[400px] rounded-full bg-foreground/[0.02] blur-3xl" />
      </div>

      <Card className="w-full max-w-sm rounded-2xl border-border/60 bg-card/90 backdrop-blur-xl transition-all duration-200" style={{ boxShadow: "var(--shadow-floating)" }}>
        <CardHeader className="space-y-3 text-center pb-4">
          <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-foreground/[0.06] text-foreground/80">
            <span className="text-xl">◈</span>
          </div>
          <div className="space-y-1">
            <CardTitle className="text-lg font-semibold tracking-tight text-foreground">{title}</CardTitle>
            <CardDescription className="text-xs leading-relaxed text-muted-foreground">{description}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {children}
          <div className="border-t border-border/40 pt-4">
            <p className="text-muted-foreground text-center text-xs">{footer}</p>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
