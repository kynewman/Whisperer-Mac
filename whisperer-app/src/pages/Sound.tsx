import { useMemo } from "react";
import { Card, Eyebrow, Row, SectionTitle, Select, Toggle } from "../primitives";
import type { AppSettings, BridgeOption } from "../App";
import type { MicOption } from "../primitives";

export default function SoundPage({
  settings,
  microphones,
  mic,
  setMic,
  inputChannels,
  inputChannel,
  setInputChannel,
  setSetting,
}: {
  settings: AppSettings;
  microphones: MicOption[];
  mic: string;
  setMic: (value: string) => void;
  inputChannels: BridgeOption[];
  inputChannel: string;
  setInputChannel: (value: string) => void;
  setSetting: (section: string, key: string, value: unknown) => void;
}) {
  const duckingEnabled = Boolean(settings.audio?.ducking_enabled ?? false);
  const duckPercent = Math.max(0, Math.min(100, Math.round(Number(settings.audio?.ducking_percent ?? 75) / 25) * 25));
  const autoGain = Boolean(settings.sound?.auto_gain ?? true);
  const effectsEnabled = Boolean(settings.sound?.effects_enabled ?? true);
  const effectsVolume = Number(settings.sound?.effects_volume ?? 80);
  const selectedMic = useMemo(
    () => microphones.find((item) => item.value === mic)?.label || "System default microphone",
    [microphones, mic]
  );

  return (
    <div className="page-enter scroll page-shell">
      <div style={{ marginBottom: 22 }}>
        <Eyebrow>Audio</Eyebrow>
        <h1 style={{ fontSize: 28, fontWeight: 500, letterSpacing: "-0.025em", margin: "6px 0 6px" }}>Sound</h1>
        <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0, maxWidth: 620, lineHeight: 1.5 }}>
          Microphone selection, ducking, and the small sounds Whisperer makes.
        </p>
      </div>

      <Card style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <Eyebrow>Selected input</Eyebrow>
            <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 4 }}>
              <span className="mono" style={{ color: "var(--ink-1)" }}>{selectedMic}</span> / 16 kHz / mono
            </div>
          </div>
        </div>
      </Card>

      <SectionTitle>Input device</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Microphone" subtitle="Used everywhere unless overridden by a mode."
             control={<Select value={mic} onChange={setMic} options={microphones} width={280} />} />
        <Row title="Input channel" subtitle="For multi-channel interfaces, choose which channel to record from."
             control={<Select value={inputChannel} onChange={setInputChannel} options={inputChannels} width={140} />} />
        <Row title="Automatic gain" subtitle="Smooth out loud and quiet patches as you speak."
             control={<Toggle checked={autoGain} onChange={(value) => setSetting("sound", "auto_gain", value)} />} divider={false} />
      </Card>

      <SectionTitle>Speaker ducking</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Duck system audio while recording" subtitle="Lower playback volume when you press the dictation hotkey, restore on release."
             control={<Toggle checked={duckingEnabled} onChange={(value) => setSetting("audio", "ducking_enabled", value)} />} />
        <div style={{ padding: "16px 0 4px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
            <span style={{ fontSize: 13, color: "var(--ink-1)", fontWeight: 500 }}>Reduce volume by</span>
            <span className="mono" style={{ fontSize: 18, fontWeight: 500, color: "var(--ink)", fontFeatureSettings: '"tnum"' }}>{duckPercent === 100 ? "mute" : `${duckPercent}%`}</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={25}
            value={duckPercent}
            onChange={(e) => setSetting("audio", "ducking_percent", Math.round(Number(e.target.value) / 25) * 25)}
            style={{ width: "100%", accentColor: "var(--accent)" }}
            disabled={!duckingEnabled}
          />
          <div className="mono" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", marginTop: 6, fontSize: 11, color: "var(--ink-3)", textAlign: "center" }}>
            <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>mute</span>
          </div>
        </div>
      </Card>

      <SectionTitle>Sound design</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <Row title="Sound effects" subtitle="Small confirmation sounds for app events."
             control={<Toggle checked={effectsEnabled} onChange={(value) => setSetting("sound", "effects_enabled", value)} />} />
        <Row title="Output volume" subtitle="How loud Whisperer's own sounds play."
             control={<input type="range" min={0} max={100} value={effectsVolume} onChange={(e) => setSetting("sound", "effects_volume", Number(e.target.value))} style={{ width: 180, accentColor: "var(--accent)" }} />}
             divider={false} />
      </Card>
    </div>
  );
}
