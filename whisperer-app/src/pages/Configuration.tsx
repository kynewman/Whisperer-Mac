import { useEffect, useRef, useState } from "react";
import { Btn, Card, Eyebrow, Icon, Input, KeyCombo, Row, SectionTitle, Select, Toggle } from "../primitives";
import { SHORTCUTS } from "../data";
import type { AppSettings, BenchmarkStatus, Tweaks, UpdateStatus } from "../App";

type ProviderKey = {
  service: string;
  title: string;
  subtitle: string;
  placeholder: string;
};

type ApiKeyTest = {
  ok: boolean;
  message: string;
  testing?: boolean;
};

type CapturedShortcutKey = {
  id: string;
  label: string;
  value: string;
  rank: number;
  order: number;
};

const DIRECT_TESTS: Record<string, { url: string; header: (key: string) => string; label: string }> = {
  groq: {
    url: "https://api.groq.com/openai/v1/models",
    header: (key) => `Bearer ${key}`,
    label: "Groq",
  },
  openai: {
    url: "https://api.openai.com/v1/models",
    header: (key) => `Bearer ${key}`,
    label: "OpenAI",
  },
  deepgram: {
    url: "https://api.deepgram.com/v1/projects",
    header: (key) => `Token ${key}`,
    label: "Deepgram",
  },
};

const API_KEY_ROWS: ProviderKey[] = [
  {
    service: "openai",
    title: "OpenAI API key",
    subtitle: "Used by OpenAI speech-to-text and OpenAI LLM modes.",
    placeholder: "sk-...",
  },
  {
    service: "groq",
    title: "Groq API key",
    subtitle: "Used by Whisperer's default fast cloud transcription and Groq LLM modes.",
    placeholder: "gsk_...",
  },
  {
    service: "deepgram",
    title: "Deepgram API key",
    subtitle: "Used by Deepgram Nova speech-to-text modes.",
    placeholder: "Deepgram token",
  },
  {
    service: "nvidia",
    title: "NVIDIA API key",
    subtitle: "Optional bearer token for NVIDIA NIM Parakeet speech endpoints.",
    placeholder: "nvapi-...",
  },
  {
    service: "anthropic",
    title: "Anthropic API key",
    subtitle: "Used by Anthropic LLM post-processing modes.",
    placeholder: "sk-ant-...",
  },
  {
    service: "openai_compat",
    title: "OpenAI-compatible LLM key",
    subtitle: "Optional bearer token for local or hosted OpenAI-compatible chat APIs.",
    placeholder: "optional",
  },
  {
    service: "openai_compat_stt",
    title: "OpenAI-compatible STT key",
    subtitle: "Optional bearer token for custom speech endpoints such as self-hosted Whisper APIs.",
    placeholder: "optional",
  },
];

const MODIFIER_TOKENS: Record<string, Omit<CapturedShortcutKey, "order">> = {
  ctrl: { id: "ctrl", label: "Ctrl", value: "ctrl", rank: 0 },
  alt: { id: "alt", label: "Option", value: "alt", rank: 1 },
  shift: { id: "shift", label: "Shift", value: "shift", rank: 2 },
  cmd: { id: "cmd", label: "Cmd", value: "cmd", rank: 3 },
  fn: { id: "fn", label: "Fn", value: "fn", rank: 4 },
};

const SPECIAL_KEY_VALUES: Record<string, { label: string; value: string }> = {
  Escape: { label: "Esc", value: "escape" },
  " ": { label: "Space", value: "space" },
  Spacebar: { label: "Space", value: "space" },
  Enter: { label: "Enter", value: "enter" },
  Return: { label: "Enter", value: "enter" },
  Tab: { label: "Tab", value: "tab" },
  Backspace: { label: "Backspace", value: "backspace" },
  Delete: { label: "Delete", value: "delete" },
  ArrowLeft: { label: "Left", value: "left" },
  ArrowRight: { label: "Right", value: "right" },
  ArrowUp: { label: "Up", value: "up" },
  ArrowDown: { label: "Down", value: "down" },
  Home: { label: "Home", value: "home" },
  End: { label: "End", value: "end" },
  PageUp: { label: "Page Up", value: "page up" },
  PageDown: { label: "Page Down", value: "page down" },
};

const SYMBOL_KEY_VALUES: Record<string, { label: string; value: string }> = {
  "+": { label: "Plus", value: "plus" },
  "-": { label: "Minus", value: "minus" },
  "=": { label: "Equals", value: "equals" },
  ",": { label: "Comma", value: "comma" },
  ".": { label: "Period", value: "period" },
  "/": { label: "Slash", value: "slash" },
  "\\": { label: "Backslash", value: "backslash" },
  ";": { label: "Semicolon", value: "semicolon" },
  "'": { label: "Quote", value: "quote" },
  "`": { label: "Grave", value: "grave" },
  "[": { label: "Left Bracket", value: "left bracket" },
  "]": { label: "Right Bracket", value: "right bracket" },
  "!": { label: "Exclamation", value: "exclamation" },
  "@": { label: "At", value: "at" },
  "#": { label: "Hash", value: "hash" },
  "$": { label: "Dollar", value: "dollar" },
  "%": { label: "Percent", value: "percent" },
  "^": { label: "Caret", value: "caret" },
  "&": { label: "Ampersand", value: "ampersand" },
  "*": { label: "Asterisk", value: "asterisk" },
  "(": { label: "Left Paren", value: "left paren" },
  ")": { label: "Right Paren", value: "right paren" },
};

const keyTokenFromEvent = (event: KeyboardEvent, order: number): CapturedShortcutKey | null => {
  if (event.key === "Control") return { ...MODIFIER_TOKENS.ctrl, order };
  if (event.key === "Alt") return { ...MODIFIER_TOKENS.alt, order };
  if (event.key === "Shift") return { ...MODIFIER_TOKENS.shift, order };
  if (event.key === "Meta" || event.key === "OS") return { ...MODIFIER_TOKENS.cmd, order };
  if (event.key === "Fn" || event.key === "Function" || event.code === "Fn" || event.code === "FnLock") {
    return { ...MODIFIER_TOKENS.fn, order };
  }
  if (/^F([1-9]|1[0-9]|20)$/.test(event.key)) {
    return { id: event.code || event.key, label: event.key, value: event.key.toLowerCase(), rank: 10, order };
  }
  const named = SPECIAL_KEY_VALUES[event.key];
  if (named) {
    return { id: event.code || named.value, label: named.label, value: named.value, rank: 10, order };
  }
  if (event.key.length === 1) {
    const symbol = SYMBOL_KEY_VALUES[event.key];
    if (symbol) {
      return { id: event.code || symbol.value, label: symbol.label, value: symbol.value, rank: 10, order };
    }
    return { id: event.code || event.key, label: event.key.toUpperCase(), value: event.key.toLowerCase(), rank: 10, order };
  }
  return null;
};

export default function ConfigPage({
  tweaks,
  setTweaks,
  settings: appSettings,
  shortcuts,
  apiKeys,
  benchmarkStatus,
  updateStatus,
  setSetting,
  setShortcut,
  setApiKey,
  deleteApiKey,
  runSttBenchmark,
  checkForUpdates,
  installUpdate,
}: {
  tweaks: Tweaks;
  setTweaks: (t: Tweaks) => void;
  settings: AppSettings;
  shortcuts: Record<string, string[]>;
  apiKeys: Record<string, boolean>;
  benchmarkStatus: BenchmarkStatus;
  updateStatus: UpdateStatus;
  setSetting: (section: string, key: string, value: unknown) => void;
  setShortcut: (name: string, value: string) => void;
  setApiKey: (service: string, value: string) => void;
  deleteApiKey: (service: string) => void;
  runSttBenchmark: () => void;
  checkForUpdates: () => void;
  installUpdate: () => void;
}) {
  const [settings, setSettings] = useState({
    launchOnLogin: Boolean(appSettings.startup?.launch_on_login ?? false),
    autoStartEngine: Boolean(appSettings.startup?.auto_start_engine ?? true),
    retainAudio: Boolean(appSettings.privacy?.store_audio_history ?? false),
    retainHistory: Boolean(appSettings.privacy?.retain_history ?? true),
    enginePreload: String(appSettings.performance?.engine_preload ?? "app_start"),
    adaptiveStreaming: Boolean(appSettings.performance?.streaming_adaptive_finalize_enabled ?? true),
    fastPaste: Boolean(appSettings.performance?.paste_fast_path_enabled ?? true),
    autoSendEnter: Boolean(appSettings.paste?.auto_send_enter ?? false),
    restoreClipboard: Boolean(appSettings.paste?.restore_clipboard ?? false),
    pasteMethod: String(appSettings.paste?.method ?? "clipboard_paste"),
    ollamaUrl: String(appSettings.llm?.ollama_url ?? "http://localhost:11434"),
    openaiCompatUrl: String(appSettings.llm?.openai_compat_url ?? "http://localhost:8000"),
    sttCompatUrl: String(appSettings.stt?.openai_compat_url ?? "http://localhost:8000/v1/audio/transcriptions"),
    nvidiaNimUrl: String(appSettings.stt?.nvidia_nim_url ?? "http://localhost:9000/v1/audio/transcriptions"),
  });
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});
  const [apiKeyTests, setApiKeyTests] = useState<Record<string, ApiKeyTest>>({});
  const [recordingShortcut, setRecordingShortcut] = useState(false);
  const [pressedShortcut, setPressedShortcut] = useState<CapturedShortcutKey[]>([]);
  const [draftShortcut, setDraftShortcut] = useState<CapturedShortcutKey[]>([]);
  const pressedShortcutRef = useRef<Map<string, CapturedShortcutKey>>(new Map());
  const draftShortcutRef = useRef<CapturedShortcutKey[]>([]);
  const shortcutOrderRef = useRef(0);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeText, setPurgeText] = useState("");

  useEffect(() => {
    setSettings((current) => ({
      ...current,
      launchOnLogin: Boolean(appSettings.startup?.launch_on_login ?? false),
      autoStartEngine: Boolean(appSettings.startup?.auto_start_engine ?? true),
      retainAudio: Boolean(appSettings.privacy?.store_audio_history ?? false),
      retainHistory: Boolean(appSettings.privacy?.retain_history ?? true),
      enginePreload: String(appSettings.performance?.engine_preload ?? "app_start"),
      adaptiveStreaming: Boolean(appSettings.performance?.streaming_adaptive_finalize_enabled ?? true),
      fastPaste: Boolean(appSettings.performance?.paste_fast_path_enabled ?? true),
      autoSendEnter: Boolean(appSettings.paste?.auto_send_enter ?? false),
      restoreClipboard: Boolean(appSettings.paste?.restore_clipboard ?? false),
      pasteMethod: String(appSettings.paste?.method ?? "clipboard_paste"),
      ollamaUrl: String(appSettings.llm?.ollama_url ?? "http://localhost:11434"),
      openaiCompatUrl: String(appSettings.llm?.openai_compat_url ?? "http://localhost:8000"),
      sttCompatUrl: String(appSettings.stt?.openai_compat_url ?? "http://localhost:8000/v1/audio/transcriptions"),
      nvidiaNimUrl: String(appSettings.stt?.nvidia_nim_url ?? "http://localhost:9000/v1/audio/transcriptions"),
    }));
  }, [appSettings]);

  const set = <K extends keyof typeof settings>(k: K, v: (typeof settings)[K]) => {
    setSettings((s) => ({ ...s, [k]: v }));
    if (k === "launchOnLogin") setSetting("startup", "launch_on_login", v);
    if (k === "autoStartEngine") setSetting("startup", "auto_start_engine", v);
    if (k === "pasteMethod") setSetting("paste", "method", v);
    if (k === "restoreClipboard") setSetting("paste", "restore_clipboard", v);
    if (k === "autoSendEnter") setSetting("paste", "auto_send_enter", v);
    if (k === "retainHistory") setSetting("privacy", "retain_history", v);
    if (k === "retainAudio") setSetting("privacy", "store_audio_history", v);
    if (k === "enginePreload") setSetting("performance", "engine_preload", v);
    if (k === "adaptiveStreaming") setSetting("performance", "streaming_adaptive_finalize_enabled", v);
    if (k === "fastPaste") setSetting("performance", "paste_fast_path_enabled", v);
    if (k === "ollamaUrl") setSetting("llm", "ollama_url", v);
    if (k === "openaiCompatUrl") setSetting("llm", "openai_compat_url", v);
    if (k === "sttCompatUrl") setSetting("stt", "openai_compat_url", v);
    if (k === "nvidiaNimUrl") setSetting("stt", "nvidia_nim_url", v);
  };
  const setT = <K extends keyof Tweaks>(k: K, v: Tweaks[K]) => {
    setTweaks({ ...tweaks, [k]: v });
    setSetting("ui", k, v);
  };

  const sortedShortcut = () =>
    Array.from(pressedShortcutRef.current.values()).sort((a, b) => a.rank - b.rank || a.order - b.order).slice(0, 6);

  const publishPressedShortcut = (remember = false) => {
    const next = sortedShortcut();
    setPressedShortcut(next);
    if (remember && next.length && next.length >= draftShortcutRef.current.length) {
      draftShortcutRef.current = next;
      setDraftShortcut(next);
    }
  };

  const addModifierFlags = (event: KeyboardEvent) => {
    const modifiers: Array<[boolean, keyof typeof MODIFIER_TOKENS]> = [
      [event.ctrlKey, "ctrl"],
      [event.altKey, "alt"],
      [event.shiftKey, "shift"],
      [event.metaKey, "cmd"],
    ];
    modifiers.forEach(([active, key]) => {
      if (!active) {
        pressedShortcutRef.current.delete(key);
        return;
      }
      if (pressedShortcutRef.current.has(key)) return;
      shortcutOrderRef.current += 1;
      pressedShortcutRef.current.set(key, { ...MODIFIER_TOKENS[key], order: shortcutOrderRef.current });
    });
  };

  const syncNativeModifiers = (activeModifiers: string[], remember = false) => {
    const active = new Set(activeModifiers.filter((key): key is keyof typeof MODIFIER_TOKENS => key in MODIFIER_TOKENS));
    if (pressedShortcutRef.current.size === 0 && active.size > 0) {
      shortcutOrderRef.current = 0;
      draftShortcutRef.current = [];
      setDraftShortcut([]);
    }
    (Object.keys(MODIFIER_TOKENS) as Array<keyof typeof MODIFIER_TOKENS>).forEach((key) => {
      if (active.has(key)) {
        if (!pressedShortcutRef.current.has(key)) {
          shortcutOrderRef.current += 1;
          pressedShortcutRef.current.set(key, { ...MODIFIER_TOKENS[key], order: shortcutOrderRef.current });
        }
      } else {
        pressedShortcutRef.current.delete(key);
      }
    });
    publishPressedShortcut(remember && active.size > 0);
  };

  const clearPressedShortcut = () => {
    pressedShortcutRef.current.clear();
    setPressedShortcut([]);
  };

  useEffect(() => {
    if (!recordingShortcut) return;
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (pressedShortcutRef.current.size === 0) {
        shortcutOrderRef.current = 0;
        draftShortcutRef.current = [];
        setDraftShortcut([]);
      }
      addModifierFlags(event);
      const key = keyTokenFromEvent(event, shortcutOrderRef.current + 1);
      if (key && !pressedShortcutRef.current.has(key.id)) {
        shortcutOrderRef.current += 1;
        pressedShortcutRef.current.set(key.id, { ...key, order: shortcutOrderRef.current });
      }
      publishPressedShortcut(true);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const key = keyTokenFromEvent(event, 0);
      if (key) pressedShortcutRef.current.delete(key.id);
      publishPressedShortcut(false);
    };
    const onBlur = () => {
      clearPressedShortcut();
    };
    let disposed = false;
    const pollNativeModifiers = () => {
      window.whisperer?.shortcutModifierState?.()
        .then((raw) => {
          if (disposed) return;
          const parsed = JSON.parse(raw || "{}") as { modifiers?: string[] };
          syncNativeModifiers(parsed.modifiers || [], true);
        })
        .catch(() => {});
    };
    window.whisperer?.setShortcutCaptureActive?.(true).catch(() => {});
    pollNativeModifiers();
    const modifierPoll = window.setInterval(pollNativeModifiers, 35);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", onBlur, true);
    return () => {
      disposed = true;
      window.clearInterval(modifierPoll);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("blur", onBlur, true);
      window.whisperer?.setShortcutCaptureActive?.(false).catch(() => {});
    };
  }, [recordingShortcut]);

  const startShortcutCapture = () => {
    shortcutOrderRef.current = 0;
    pressedShortcutRef.current.clear();
    draftShortcutRef.current = [];
    setPressedShortcut([]);
    setDraftShortcut([]);
    setRecordingShortcut(true);
    window.focus();
  };

  const cancelShortcutCapture = () => {
    setRecordingShortcut(false);
    pressedShortcutRef.current.clear();
    draftShortcutRef.current = [];
    setPressedShortcut([]);
    setDraftShortcut([]);
  };

  const commitShortcut = () => {
    const captured = draftShortcutRef.current.length ? draftShortcutRef.current : draftShortcut;
    if (!captured.length) return;
    setShortcut("dictation", captured.map((key) => key.value).join("+"));
    cancelShortcutCapture();
  };

  const displayedShortcut = recordingShortcut ? (draftShortcut.length ? draftShortcut : pressedShortcut) : [];
  const dictationKeys = recordingShortcut
    ? (displayedShortcut.length ? displayedShortcut.map((key) => key.label) : ["Recording"])
    : (shortcuts.dictation?.length ? shortcuts.dictation : ["Ctrl", "Cmd"]);
  const shortcutCaptureStatus = pressedShortcut.length
    ? "Recording"
    : draftShortcut.length
      ? "Captured"
      : "Recording";
  const updateBusy = Boolean(updateStatus.busy);
  const updateMessage = updateStatus.message || "Updates have not been checked yet.";
  const updateMeta = updateStatus.latestVersion
    ? `Current ${updateStatus.currentVersion || "-"} - Latest ${updateStatus.latestVersion}`
    : `Current ${updateStatus.currentVersion || "-"}`;
  const updateColor =
    updateStatus.state === "available" ? "var(--accent-ink)" :
    updateStatus.state === "error" ? "var(--rec)" :
    updateStatus.state === "up_to_date" ? "var(--ok)" :
    "var(--ink-3)";

  const closePurge = () => {
    setPurgeOpen(false);
    setPurgeText("");
  };

  const confirmPurge = () => {
    if (purgeText !== "PURGE") return;
    window.whisperer?.purgeHistory?.();
    closePurge();
  };

  const updateApiDraft = (service: string, value: string) => {
    setApiKeyDrafts((current) => ({ ...current, [service]: value }));
  };

  const saveApiKey = (service: string) => {
    const value = (apiKeyDrafts[service] || "").trim();
    if (!value) return;
    setApiKey(service, value);
    setApiKeyDrafts((current) => ({ ...current, [service]: "" }));
    setApiKeyTests((current) => ({ ...current, [service]: { ok: false, message: "Saved. Test the key to verify it." } }));
  };

  const clearApiKey = (service: string) => {
    deleteApiKey(service);
    setApiKeyDrafts((current) => ({ ...current, [service]: "" }));
    setApiKeyTests((current) => ({ ...current, [service]: { ok: false, message: "Key removed." } }));
  };

  const callRawBridge = (method: string, ...args: unknown[]) => {
    const bridge = (window as typeof window & { bridge?: Record<string, (...values: unknown[]) => void> }).bridge;
    const fn = bridge?.[method];
    if (!fn) return null;
    return new Promise<string>((resolve) => {
      fn.apply(bridge, [...args, (result: string) => resolve(result)]);
    });
  };

  const directApiKeyTest = async (service: string, key: string) => {
    const check = DIRECT_TESTS[service];
    if (!check || !key) {
      throw new Error("Restart Whisperer to enable the built-in key test.");
    }
    const response = await fetch(check.url, {
      method: "GET",
      headers: { Authorization: check.header(key) },
    });
    if (response.ok) {
      return JSON.stringify({ apiKeyTest: { ok: true, message: `${check.label} key is working.` } });
    }
    return JSON.stringify({ apiKeyTest: { ok: false, message: `${check.label} rejected the key (${response.status}).` } });
  };

  const testApiKey = (service: string) => {
    const draft = (apiKeyDrafts[service] || "").trim();
    setApiKeyTests((current) => ({ ...current, [service]: { ok: false, message: "Testing...", testing: true } }));
    const saveFirst = draft && window.whisperer?.setApiKey
      ? window.whisperer.setApiKey(service, draft).then(() => {
          setApiKeyDrafts((current) => ({ ...current, [service]: "" }));
        })
      : Promise.resolve();
    const testCall = () => {
      const nativeTest = window.whisperer?.testApiKey
        ? window.whisperer.testApiKey(service)
        : callRawBridge("testApiKey", service);
      if (!nativeTest && draft) {
        return directApiKeyTest(service, draft);
      }
      if (!nativeTest) {
        return Promise.reject(new Error("Restart Whisperer to enable the built-in key test."));
      }
      return Promise.race([
        nativeTest,
        new Promise<string>((_resolve, reject) => {
          window.setTimeout(() => reject(new Error("The key test timed out. The key was saved; try one short dictation.")), 12000);
        }),
      ]);
    };
    saveFirst
      .then(testCall)
      .then((raw) => {
        let result: ApiKeyTest | null = null;
        try {
          const parsed = JSON.parse(raw || "{}");
          result = parsed.apiKeyTest as ApiKeyTest;
        } catch {
          result = null;
        }
        setApiKeyTests((current) => ({
          ...current,
          [service]: result || { ok: false, message: "Could not read the test result." },
        }));
      })
      .catch((error) => {
        setApiKeyTests((current) => ({
          ...current,
          [service]: { ok: false, message: error instanceof Error ? error.message : "Could not run the key test." },
        }));
      });
  };

  return (
    <div className="page-enter scroll page-shell">
      <div style={{ marginBottom: 22 }}>
        <Eyebrow>Configuration</Eyebrow>
        <h1 style={{ fontSize: 28, fontWeight: 500, letterSpacing: "-0.025em", margin: "6px 0 6px" }}>Configuration</h1>
        <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0, maxWidth: 620, lineHeight: 1.5 }}>
          Shortcuts, paste behavior, privacy, providers, and startup.
        </p>
      </div>

      <SectionTitle>Appearance</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Theme" subtitle="Follow daylight or pick a fixed mode."
             control={<Select value={tweaks.theme} onChange={(v) => setT("theme", v as Tweaks["theme"])} options={[{ value: "sun", label: "Sun" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} width={140} />} />
        <Row title="Accent" subtitle="Used for active states, buttons, and the live waveform."
             control={<Select value={tweaks.accent} onChange={(v) => setT("accent", v as Tweaks["accent"])} options={[{ value: "moss", label: "Moss" }, { value: "sage", label: "Sage" }, { value: "clay", label: "Clay" }, { value: "copper", label: "Copper" }, { value: "plum", label: "Plum" }, { value: "slate", label: "Slate" }]} width={160} />} />
        <Row title="Density" subtitle="Slightly tighter spacing on smaller displays."
             control={<Select value={tweaks.density} onChange={(v) => setT("density", v as Tweaks["density"])} options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} width={160} />}
             divider={false} />
      </Card>

      <SectionTitle>Keyboard shortcuts</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row
          title="Dictation hotkey"
          subtitle={recordingShortcut ? "Press a shortcut combination, then confirm it." : "Hold while speaking. Release to transcribe and paste."}
          control={
            <div style={{ display: "grid", justifyItems: "end", gap: 5 }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <KeyCombo keys={dictationKeys} />
                {recordingShortcut ? (
                  <>
                    <Btn size="sm" variant="accent" icon="check" onClick={commitShortcut} disabled={!draftShortcut.length}>OK</Btn>
                    <Btn size="sm" variant="ghost" onClick={cancelShortcutCapture}>Cancel</Btn>
                  </>
                ) : (
                  <Btn size="sm" variant="secondary" icon="edit" onClick={startShortcutCapture}>Change</Btn>
                )}
              </div>
              {recordingShortcut && (
                <span style={{ fontSize: 11.5, color: pressedShortcut.length ? "var(--accent-ink)" : "var(--ink-3)", fontWeight: 500 }}>
                  {shortcutCaptureStatus}
                </span>
              )}
            </div>
          }
        />
        {SHORTCUTS.filter((s) => s.key !== "dictation").map((s, i, arr) => (
          <Row key={s.key} title={s.label} subtitle={s.hint} divider={i < arr.length - 1} control={<KeyCombo keys={shortcuts[s.key] || s.combo} />} />
        ))}
      </Card>

      <SectionTitle>Paste behavior</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Default paste method" subtitle="How the final text is delivered to the active application."
             control={<Select value={settings.pasteMethod} onChange={(v) => set("pasteMethod", v)} options={[{ value: "clipboard_paste", label: "Clipboard paste" }, { value: "simulate_keys", label: "Simulate keystrokes" }, { value: "copy_only", label: "Copy only (no paste)" }]} width={240} />} />
        <Row title="Restore previous clipboard" subtitle="Put your old clipboard contents back after pasting."
             control={<Toggle checked={settings.restoreClipboard} onChange={(v) => set("restoreClipboard", v)} />} />
        <Row title="Auto-send Enter after paste" subtitle="Submit chat messages automatically. Avoid in code editors."
             control={<Toggle checked={settings.autoSendEnter} onChange={(v) => set("autoSendEnter", v)} />} divider={false} />
      </Card>

      <SectionTitle>Performance</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Adaptive streaming finalization" subtitle="Use the shortest safe RNNT close wait when streaming already has usable text."
             control={<Toggle checked={settings.adaptiveStreaming} onChange={(v) => set("adaptiveStreaming", v)} />} />
        <Row title="Known-good paste fast path" subtitle="Use a lower paste settle delay in apps that reliably accept immediate clipboard paste."
             control={<Toggle checked={settings.fastPaste} onChange={(v) => set("fastPaste", v)} />} />
        <Row
          title="STT provider benchmark"
          subtitle={benchmarkStatus.busy ? "Benchmarking the last dictation sample." : benchmarkStatus.status || benchmarkStatus.error || "Compare saved cloud STT providers on the same last dictation sample."}
          control={<Btn size="sm" variant="secondary" icon="play" disabled={benchmarkStatus.busy} onClick={runSttBenchmark}>{benchmarkStatus.busy ? "Running" : "Run"}</Btn>}
          divider={Boolean(benchmarkStatus.results?.length)}
        />
        {Boolean(benchmarkStatus.results?.length) && (
          <div style={{ padding: "8px 0 2px", display: "grid", gap: 8 }}>
            {benchmarkStatus.results?.slice(0, 5).map((result, index) => (
              <div key={`${result.label}-${index}`} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 12, alignItems: "center", padding: "8px 0", borderTop: index === 0 ? "none" : "1px solid var(--line-soft)" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, color: result.ok ? "var(--ink)" : "var(--ink-3)", fontWeight: 600 }}>{result.label}</div>
                  <div style={{ fontSize: 11.5, color: result.ok ? "var(--ink-3)" : "var(--rec)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {result.ok ? `${result.chars || 0} chars${benchmarkStatus.sampleSeconds ? ` from ${benchmarkStatus.sampleSeconds}s` : ""}` : result.error || "Failed"}
                  </div>
                </div>
                <span className="mono" style={{ color: result.ok ? "var(--accent-ink)" : "var(--ink-3)", fontSize: 12, fontWeight: 600 }}>
                  {result.ok ? `${Math.round(Number(result.ms) || 0)}ms` : "-"}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <SectionTitle>Privacy</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", gap: 14, padding: "12px 0 16px", borderBottom: "1px solid var(--line-soft)" }}>
          <Icon name="shield" size={18} stroke={1.5} />
          <div style={{ flex: 1, fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
            Whisperer is local-first. Audio and transcripts stay on this device unless you enable a cloud provider in a mode.
          </div>
        </div>
        <Row title="Retain transcription history" subtitle="Keep a searchable log of past dictations. Stored locally."
             control={<Toggle checked={settings.retainHistory} onChange={(v) => set("retainHistory", v)} />} />
        <Row title="Keep audio recordings" subtitle="Save the original WAV alongside each transcript. Off by default."
             control={<Toggle checked={settings.retainAudio} onChange={(v) => set("retainAudio", v)} />} />
        <Row title="Purge all history" subtitle="Permanently delete every dictation, audio file, and context entry."
             control={<Btn variant="danger" icon="trash" onClick={() => setPurgeOpen(true)}>Purge history</Btn>} divider={false} />
      </Card>

      <SectionTitle>Cloud & local AI providers</SectionTitle>
      <Card style={{ marginBottom: 18 }} padding={0}>
        <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--line-soft)", background: "var(--bg-sunken)", display: "flex", alignItems: "center", gap: 10 }}>
          <Icon name="info" size={14} />
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Cloud providers are only used by modes that opt in. API keys stay in secure local storage.</span>
        </div>
        <div style={{ padding: "0 18px" }}>
          {API_KEY_ROWS.map((row, index) => {
            const saved = Boolean(apiKeys[row.service]);
            const draft = apiKeyDrafts[row.service] || "";
            const test = apiKeyTests[row.service];
            const canTest = row.service === "groq" || row.service === "openai" || row.service === "deepgram";
            return (
              <Row
                key={row.service}
                title={row.title}
                subtitle={row.subtitle}
                divider={index < API_KEY_ROWS.length - 1}
                control={
                  <div style={{ display: "grid", justifyItems: "end", gap: 5 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          minWidth: 62,
                          color: saved ? "var(--ok)" : "var(--ink-3)",
                          fontSize: 11.5,
                          fontWeight: 600,
                        }}
                      >
                        {saved ? "Saved" : "Not saved"}
                      </span>
                      <Input
                        type="password"
                        value={draft}
                        onChange={(value) => updateApiDraft(row.service, value)}
                        placeholder={row.placeholder}
                        style={{ width: 220 }}
                      />
                      <Btn size="sm" variant="accent" icon="check" disabled={!draft.trim()} onClick={() => saveApiKey(row.service)}>Save</Btn>
                      {canTest && (
                        <Btn
                          size="sm"
                          variant="secondary"
                          icon="play"
                          disabled={(!saved && !draft.trim()) || Boolean(test?.testing)}
                          onClick={() => testApiKey(row.service)}
                        >
                          {draft.trim() ? "Save & Test" : "Test"}
                        </Btn>
                      )}
                      <Btn size="sm" variant="ghost" icon="trash" disabled={!saved && !draft} onClick={() => clearApiKey(row.service)} />
                    </div>
                    {test?.message && (
                      <span style={{ fontSize: 11.5, color: test.ok ? "var(--ok)" : "var(--ink-3)" }}>
                        {test.message}
                      </span>
                    )}
                  </div>
                }
              />
            );
          })}
        </div>
        <div style={{ padding: "14px 18px", background: "var(--bg-sunken)" }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: 10 }}>Local and compatible endpoints</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 5 }}>Ollama URL</div>
              <Input value={settings.ollamaUrl} onChange={(v) => set("ollamaUrl", v)} />
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 5 }}>OpenAI-compatible URL</div>
              <Input value={settings.openaiCompatUrl} onChange={(v) => set("openaiCompatUrl", v)} />
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 5 }}>OpenAI-compatible speech URL</div>
              <Input value={settings.sttCompatUrl} onChange={(v) => set("sttCompatUrl", v)} />
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 5 }}>NVIDIA NIM Parakeet URL</div>
              <Input value={settings.nvidiaNimUrl} onChange={(v) => set("nvidiaNimUrl", v)} />
            </div>
          </div>
        </div>
      </Card>

      <SectionTitle>Startup & updates</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row
          title="Whisperer updates"
          subtitle={updateMessage}
          control={
            <div style={{ display: "grid", justifyItems: "end", gap: 6, maxWidth: 430 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
                <span style={{ color: updateColor, fontSize: 11.5, fontWeight: 600 }}>
                  {updateStatus.state === "checking" ? "Checking" :
                   updateStatus.state === "installing" ? "Installing" :
                   updateStatus.state === "available" ? "Update available" :
                   updateStatus.state === "up_to_date" ? "Up to date" :
                   updateStatus.state === "error" ? "Check failed" :
                   "Not checked"}
                </span>
                <Btn size="sm" variant="secondary" icon="search" disabled={updateBusy} onClick={checkForUpdates}>
                  Check
                </Btn>
                <Btn
                  size="sm"
                  variant="accent"
                  icon="check"
                  disabled={updateBusy || !updateStatus.updateAvailable}
                  onClick={installUpdate}
                >
                  Install update
                </Btn>
              </div>
              <span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>
                {updateMeta}
              </span>
            </div>
          }
        />
        <Row title="Start Whisperer when macOS starts" subtitle="Add or remove Whisperer from your macOS login items."
             control={<Toggle checked={settings.launchOnLogin} onChange={(v) => set("launchOnLogin", v)} />} />
        <Row title="Start engine when Whisperer opens" subtitle="Warm the transcription engine automatically after launch."
             control={<Toggle checked={settings.autoStartEngine} onChange={(v) => set("autoStartEngine", v)} />} />
        <Row title="Engine preload" subtitle="When the transcription model gets loaded into memory."
             control={<Select value={settings.enginePreload} onChange={(v) => set("enginePreload", v)} options={[{ value: "app_start", label: "When app starts" }, { value: "login", label: "On login" }, { value: "off", label: "Manual" }]} width={200} />} />
      </Card>
      {purgeOpen && (
        <div
          data-no-drag
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 100,
            display: "grid",
            placeItems: "center",
            background: "rgba(0,0,0,0.24)",
          }}
        >
          <Card
            padding={0}
            style={{
              width: 430,
              maxWidth: "calc(100vw - 48px)",
              boxShadow: "var(--shadow-menu)",
              overflow: "hidden",
            }}
          >
            <div style={{ padding: "18px 20px 14px", borderBottom: "1px solid var(--line-soft)" }}>
              <Eyebrow>Confirm</Eyebrow>
              <h2 style={{ margin: "6px 0 6px", fontSize: 19, fontWeight: 550, color: "var(--ink)", letterSpacing: "-0.015em" }}>
                Purge History
              </h2>
              <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13, lineHeight: 1.45 }}>
                Type PURGE to permanently delete every dictation.
              </p>
            </div>
            <div style={{ padding: 20 }}>
              <Input value={purgeText} onChange={setPurgeText} autoFocus />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
                <Btn variant="ghost" onClick={closePurge}>Cancel</Btn>
                <Btn variant="danger" icon="trash" disabled={purgeText !== "PURGE"} onClick={confirmPurge}>Purge</Btn>
              </div>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
