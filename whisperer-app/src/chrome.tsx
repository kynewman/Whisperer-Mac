import { CSSProperties, memo } from "react";
import { Icon, MicDropdown } from "./primitives";
import type { MicOption } from "./primitives";
import { NAV_ITEMS, NavKey } from "./data";

const navIcon: Record<NavKey, "home" | "modes" | "vocab" | "config" | "sound" | "history" | "stats"> = {
  home: "home",
  modes: "modes",
  vocabulary: "vocab",
  configuration: "config",
  sound: "sound",
  history: "history",
  stats: "stats",
};

export type EngineState = "running" | "stopped" | "loading";

const drag = { WebkitAppRegion: "drag" } as CSSProperties;
const noDrag = { WebkitAppRegion: "no-drag" } as CSSProperties;
const NAV_ROW_HEIGHT = 34;
const NAV_ROW_GAP = 2;

export const Sidebar = memo(({
  active,
  onSelect,
  version,
}: {
  active: NavKey;
  onSelect: (k: NavKey) => void;
  version: string;
}) => {
  const activeIndex = Math.max(0, NAV_ITEMS.findIndex((item) => item.key === active));
  const activeTop = activeIndex * (NAV_ROW_HEIGHT + NAV_ROW_GAP);
  const navHeight = NAV_ITEMS.length * NAV_ROW_HEIGHT + (NAV_ITEMS.length - 1) * NAV_ROW_GAP;

  const renderNavButton = (item: (typeof NAV_ITEMS)[number]) => {
    const isActive = item.key === active;
    return (
    <button
      key={item.key}
      onClick={() => onSelect(item.key)}
      aria-current={isActive ? "page" : undefined}
      style={{
        height: NAV_ROW_HEIGHT,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 10px",
        background: "transparent",
        border: "1px solid transparent",
        borderRadius: 8,
        cursor: "pointer",
        fontFamily: "inherit",
        fontSize: 13,
        color: isActive ? "var(--accent-ink)" : "var(--ink-2)",
        fontWeight: isActive ? 500 : 400,
        letterSpacing: 0,
        textAlign: "left",
        pointerEvents: "auto",
        transition: "color 620ms cubic-bezier(.2,.7,.2,1), font-weight 180ms ease",
        WebkitAppRegion: "no-drag",
      } as CSSProperties}
    >
      <Icon name={navIcon[item.key]} size={15} stroke={isActive ? 1.75 : 1.5} />
      {item.label}
    </button>
    );
  };

  return (
    <aside
      style={{
        width: 232,
        flex: "0 0 232px",
        borderRight: "1px solid var(--line)",
        background: "var(--bg-2)",
        display: "flex",
        flexDirection: "column",
        padding: "14px 12px 14px 14px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "4px 6px 16px" }}>
        <span
          style={{
            width: 22,
            height: 22,
            borderRadius: 6,
            background: "var(--card)",
            border: "1px solid var(--line)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--ink)",
            boxShadow: "var(--keycap-shadow)",
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <rect x="3" y="10" width="3" height="4" rx="1.5" />
            <rect x="6.9" y="7.5" width="3" height="9" rx="1.5" />
            <rect x="10.8" y="4.5" width="3" height="15" rx="1.5" />
            <rect x="14.7" y="7.5" width="3" height="9" rx="1.5" />
            <rect x="18.6" y="10" width="3" height="4" rx="1.5" />
          </svg>
        </span>
        <span style={{ fontSize: 14.5, fontWeight: 600, letterSpacing: "-0.012em" }}>Whisperer</span>
      </div>

      <nav style={{ position: "relative", display: "flex", flexDirection: "column", gap: NAV_ROW_GAP, height: navHeight }}>
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top: activeTop,
            height: NAV_ROW_HEIGHT,
            background: "var(--card)",
            border: "1px solid var(--accent)",
            borderRadius: 8,
            boxShadow: "var(--shadow-card)",
            transition: "top 190ms cubic-bezier(.16,1,.3,1), border-color 140ms, background 140ms",
            zIndex: 0,
          }}
        />
        <div style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", gap: NAV_ROW_GAP }}>
          {NAV_ITEMS.map((item) => renderNavButton(item))}
        </div>
      </nav>
      <span style={{ flex: 1 }} />
      <div className="mono" style={{ padding: "10px 8px 0", fontSize: 10, color: "var(--ink-3)" }}>
        {version}
      </div>
    </aside>
  );
});

export const TitleBar = ({
  pageTitle,
  micName,
  micOptions,
  onMicChange,
}: {
  pageTitle: string;
  micName: string;
  micOptions: MicOption[];
  onMicChange: (v: string) => void;
}) => {
  const w = window as Window & { whisperer?: { minimize: () => void; maximize: () => void; close: () => void; startDrag?: () => void } };
  const winApi = w.whisperer;
  const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");
  const buttons: { name: "minimize" | "maximize" | "close"; action: () => void }[] = [
    { name: "minimize", action: () => winApi?.minimize() },
    { name: "maximize", action: () => winApi?.maximize() },
    { name: "close", action: () => winApi?.close() },
  ];
  return (
    <div
      onMouseDown={(e) => {
        if (e.button === 0 && (e.target as HTMLElement).closest("[data-no-drag]") === null) {
          window.whisperer?.startDrag?.();
        }
      }}
      style={{
        height: 44,
        display: "flex",
        alignItems: "center",
        padding: "0 12px 0 14px",
        borderBottom: "1px solid var(--line-soft)",
        background: "var(--bg)",
        gap: 10,
        ...drag,
      }}
    >
      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)", letterSpacing: "-0.005em" }}>{pageTitle}</span>
      <span style={{ flex: 1 }} />
      <div data-no-drag style={{ display: "flex", alignItems: "center", gap: 8, ...noDrag }}>
        <MicDropdown value={micName} onChange={onMicChange} options={micOptions} />
        {!isMac && (
          <div style={{ display: "flex", gap: 4, marginLeft: 4 }}>
            {buttons.map((b) => (
              <button
                key={b.name}
                onClick={b.action}
                style={{
                  width: 26,
                  height: 26,
                  display: "grid",
                  placeItems: "center",
                  background: "transparent",
                  border: "1px solid transparent",
                  borderRadius: 6,
                  color: "var(--ink-2)",
                  cursor: "pointer",
                }}
              >
                <Icon name={b.name} size={13} />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
