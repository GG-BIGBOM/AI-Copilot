import type { ReactNode } from "react";

import { BrandLogo } from "@/components/brand-mark";

/**
 * 登录页和注册页共用的外框（UI_OPTIMIZATION_SPEC §20）。
 *
 * 去掉了毛玻璃和光晕：这是一个每天要进来好几次的企业工具，不是落地页。
 * 暖中性底 + 一张安静的卡片就够了。
 */
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
    <main className="flex min-h-full items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-[22rem]">
        <div className="flex flex-col items-center gap-2.5 text-center">
          <BrandLogo className="h-7" />
          <div>
            <h1 className="text-base font-semibold tracking-tight text-foreground">{title}</h1>
            <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{description}</p>
          </div>
        </div>

        <div className="mt-6 rounded-2xl border border-border-subtle bg-surface p-5">
          {children}
        </div>

        <p className="mt-4 text-center text-[13px] text-muted-foreground">{footer}</p>
      </div>
    </main>
  );
}
