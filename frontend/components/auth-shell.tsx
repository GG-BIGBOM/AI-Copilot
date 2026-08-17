import type { ReactNode } from "react";
import { Bot } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** 登录页和注册页共用的 ChatGPT / OpenAI 风格外框。 */
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
      {/* 柔和的环境光晕背景底纹 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 flex items-center justify-center overflow-hidden"
      >
        <div className="size-[500px] rounded-full bg-primary/5 blur-3xl" />
      </div>

      <Card className="w-full max-w-sm rounded-3xl border-border/80 bg-card/80 backdrop-blur-xl shadow-xl transition-all duration-200">
        <CardHeader className="space-y-3.5 text-center pb-4">
          <div className="mx-auto flex size-13 items-center justify-center rounded-2xl bg-gradient-to-tr from-primary to-primary/80 text-primary-foreground shadow-md ring-8 ring-primary/10">
            <Bot className="size-6" />
          </div>
          <div className="space-y-1">
            <CardTitle className="text-xl font-bold tracking-tight text-foreground">{title}</CardTitle>
            <CardDescription className="text-xs leading-relaxed text-muted-foreground">{description}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {children}
          <div className="border-t border-border/60 pt-4">
            <p className="text-muted-foreground text-center text-xs">{footer}</p>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
