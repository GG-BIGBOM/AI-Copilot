import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-md border border-input bg-surface px-2.5 py-2 text-base transition-colors outline-none",
        "placeholder:text-muted-foreground/70 focus-visible:border-bronze-border",
        "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:opacity-55",
        "aria-invalid:border-destructive md:text-sm dark:bg-surface-subtle",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
