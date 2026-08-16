import type { ReactNode } from "react";
import { Bot } from "lucide-react";

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
    <main className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-10">
      {/* 柔和的环境光背景底纹 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 flex items-center justify-center"
      >
        <div className="size-96 rounded-full bg-primary/5 blur-3xl" />
      </div>

      <Card className="w-full max-w-sm border-border/80 shadow-lg transition-all duration-200">
        <CardHeader className="space-y-3 text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
            <Bot className="size-6" />
          </div>
          <div className="space-y-1">
            <CardTitle className="text-xl font-semibold tracking-tight">{title}</CardTitle>
            <CardDescription className="text-xs">{description}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {children}
          <div className="border-t pt-4">
            <p className="text-muted-foreground text-center text-xs">{footer}</p>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
