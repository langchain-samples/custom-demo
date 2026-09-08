/**
 * beUI-backed select. beUI's motion Select exposes the same compound API as a
 * shadcn/Radix select (Select / SelectTrigger / SelectValue / SelectContent /
 * SelectItem), so this is a straight re-export: consumers (NewAssistantDialog,
 * VoicePicker) get beUI's animated dropdown without writing to a different API.
 */
export {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/motion/select"
