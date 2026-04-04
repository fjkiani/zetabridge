import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Coerce any API / Zod / unknown value to a string safe for React children (avoids error #31). */
export function safeRender(val: unknown): string {
  if (val === null || val === undefined) return ""
  if (typeof val === "string") return val
  if (typeof val === "number" || typeof val === "boolean") return String(val)
  if (val instanceof Error) return val.message
  if (typeof val === "object" && "message" in val) {
    const m = (val as { message: unknown }).message
    if (typeof m === "string") return m
  }
  try {
    return JSON.stringify(val)
  } catch {
    return "[unserializable]"
  }
}
