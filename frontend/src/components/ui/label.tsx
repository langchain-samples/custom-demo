import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * A plain styled <label>. (Previously Radix Label — dropped as part of the beUI
 * migration; native <label htmlFor> already gives click-to-focus, so no Radix
 * dependency is needed here.)
 */
function Label({
  className,
  ...props
}: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

export { Label }
