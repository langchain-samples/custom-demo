import * as React from "react"

import {
  Tabs as BeTabs,
  TabsList as BeTabsList,
  TabsTrigger as BeTabsTrigger,
  TabsContent as BeTabsContent,
} from "@/components/motion/tabs"

/**
 * beUI-backed tabs, keeping the shadcn compound API
 * (Tabs / TabsList / TabsTrigger / TabsContent, controlled via value +
 * onValueChange). The active-pill glide comes from beUI's motion Tabs. The old
 * `variant="line"` maps to beUI's "underline"; the default is "segment".
 */
function Tabs({
  value,
  defaultValue,
  onValueChange,
  className,
  children,
}: {
  value?: string
  defaultValue?: string
  onValueChange?: (value: string) => void
  orientation?: "horizontal" | "vertical"
  className?: string
  children: React.ReactNode
}) {
  return (
    <BeTabs
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      variant="segment"
      className={className}
    >
      {children}
    </BeTabs>
  )
}

function TabsList({
  className,
  children,
}: {
  className?: string
  variant?: "default" | "line"
  children: React.ReactNode
}) {
  return <BeTabsList className={className}>{children}</BeTabsList>
}

function TabsTrigger({
  value,
  className,
  children,
}: {
  value: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <BeTabsTrigger value={value} className={className}>
      {children}
    </BeTabsTrigger>
  )
}

function TabsContent({
  value,
  className,
  children,
}: {
  value: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <BeTabsContent value={value} className={className}>
      {children}
    </BeTabsContent>
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent }
