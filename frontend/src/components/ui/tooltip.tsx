"use client"

import * as React from "react"

import { Tooltip as BeTooltip } from "@/components/motion/tooltip"

/**
 * beUI-backed tooltip that preserves the shadcn compound API
 * (TooltipProvider / Tooltip / TooltipTrigger / TooltipContent). `Tooltip`
 * inspects its children for the trigger element (the `asChild` child) and the
 * content, then renders beUI's single-child motion Tooltip. TooltipTrigger /
 * TooltipContent are markers — they're read by Tooltip, not rendered directly.
 */
type Side = "top" | "right" | "bottom" | "left"

function TooltipProvider({ children }: { children?: React.ReactNode }) {
  return <>{children}</>
}

function TooltipTrigger(_props: {
  asChild?: boolean
  children?: React.ReactNode
}): React.ReactNode {
  return null
}

function TooltipContent(_props: {
  className?: string
  side?: Side
  children?: React.ReactNode
}): React.ReactNode {
  return null
}

function Tooltip({ children }: { children?: React.ReactNode }) {
  let trigger: React.ReactNode = null
  let content: React.ReactNode = null
  let contentClassName: string | undefined
  let side: Side | undefined

  React.Children.forEach(children, (child) => {
    if (!React.isValidElement(child)) return
    if (child.type === TooltipTrigger) {
      const p = child.props as { children?: React.ReactNode }
      trigger = p.children
    } else if (child.type === TooltipContent) {
      const p = child.props as { children?: React.ReactNode; className?: string; side?: Side }
      content = p.children
      contentClassName = p.className
      side = p.side
    }
  })

  if (!React.isValidElement(trigger)) return <>{trigger}</>
  return (
    <BeTooltip
      content={contentClassName ? <span className={contentClassName}>{content}</span> : content}
      side={side}
    >
      {trigger}
    </BeTooltip>
  )
}

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
