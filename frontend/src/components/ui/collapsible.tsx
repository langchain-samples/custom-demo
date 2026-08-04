"use client"

import * as React from "react"

import { AgentDisclosure } from "@/components/agents/agent-disclosure"

/**
 * beUI-backed collapsible, keeping the shadcn compound API
 * (Collapsible / CollapsibleTrigger / CollapsibleContent, controlled via
 * open/onOpenChange). The content's open/close animation is beUI's spring
 * AgentDisclosure instead of Radix Collapsible.
 */
const Ctx = React.createContext<{
  open: boolean
  onOpenChange?: (open: boolean) => void
}>({ open: false })

function Collapsible({
  open = false,
  onOpenChange,
  className,
  children,
}: {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  className?: string
  children: React.ReactNode
}) {
  return (
    <div data-slot="collapsible" data-state={open ? "open" : "closed"} className={className}>
      <Ctx.Provider value={{ open, onOpenChange }}>{children}</Ctx.Provider>
    </div>
  )
}

function CollapsibleTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<"button">) {
  const { open, onOpenChange } = React.useContext(Ctx)
  return (
    <button
      type="button"
      data-slot="collapsible-trigger"
      data-state={open ? "open" : "closed"}
      aria-expanded={open}
      className={className}
      onClick={() => onOpenChange?.(!open)}
      {...props}
    >
      {children}
    </button>
  )
}

function CollapsibleContent({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  const { open } = React.useContext(Ctx)
  return (
    <AgentDisclosure open={open} className={className}>
      {children}
    </AgentDisclosure>
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
