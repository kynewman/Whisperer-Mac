export type NavKey =
  | "home"
  | "modes"
  | "vocabulary"
  | "configuration"
  | "sound"
  | "history"
  | "stats";

export interface NavItem {
  key: NavKey;
  label: string;
}

export interface Shortcut {
  key: string;
  label: string;
  combo: string[];
  hint: string;
}

export const NAV_ITEMS: NavItem[] = [
  { key: "home", label: "Home" },
  { key: "modes", label: "Modes" },
  { key: "vocabulary", label: "Vocabulary" },
  { key: "configuration", label: "Configuration" },
  { key: "sound", label: "Sound" },
  { key: "history", label: "History" },
  { key: "stats", label: "Stats" },
];

export const SHORTCUTS: Shortcut[] = [
  { key: "dictation", label: "Dictation hotkey", combo: ["Ctrl", "Left Windows"], hint: "Hold while speaking. Release to transcribe and paste." },
  { key: "long_form", label: "Lock dictation", combo: ["Alt"], hint: "Keep the overlay open for long-form dictation." },
  { key: "cancel", label: "Cancel recording", combo: ["Escape"], hint: "Discard the current recording immediately." },
];
