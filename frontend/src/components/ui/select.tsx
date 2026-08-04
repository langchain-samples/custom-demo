/**
 * beUI-backed select. beUI's motion Select exposes the same compound API as the
 * old shadcn/Radix select (Select / SelectTrigger / SelectValue / SelectContent /
 * SelectItem), so this is a straight re-export — the one consumer
 * (NewAssistantForm) is unchanged and now gets beUI's animated dropdown.
 */
export {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/motion/select"
