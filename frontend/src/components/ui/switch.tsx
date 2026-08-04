"use client"

import { Switch as BeSwitch } from "@/components/motion/switch"

/**
 * beUI-backed switch, keeping the shadcn call shape (checked / onCheckedChange /
 * disabled / className) so existing consumers don't change. The animated thumb
 * and press physics come from beUI's motion Switch.
 */
function Switch({
  checked,
  onCheckedChange,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  checked?: boolean
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  className?: string
  "aria-label"?: string
  /** Accepted for API-compat with the old shadcn switch; visual size is beUI's. */
  size?: "sm" | "default"
}) {
  return (
    <BeSwitch
      checked={!!checked}
      onCheckedChange={(v) => onCheckedChange?.(v)}
      disabled={disabled}
      className={className}
      ariaLabel={ariaLabel}
    />
  )
}

export { Switch }
