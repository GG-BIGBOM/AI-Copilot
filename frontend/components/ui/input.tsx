import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        // 圆角 8px、中性描边，聚焦时描边转青铜（外面那圈 outline 由全局 :focus-visible 给）。
        // text-base + md:text-sm 是为了 iOS：小于 16px 的输入框聚焦会自动放大页面。
        "h-8 w-full min-w-0 rounded-md border border-input bg-surface px-2.5 py-1 text-base transition-colors outline-none",
        "file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground",
        "placeholder:text-muted-foreground/70 focus-visible:border-bronze-border",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-surface-muted disabled:opacity-55",
        "aria-invalid:border-destructive md:text-sm dark:bg-surface-subtle",
        className
      )}
      {...props}
    />
  )
}

export { Input }
