import { useMemo, useState } from "react";
import { Btn, Card, Eyebrow, Input, Pill, Select } from "../primitives";
import type { AppSnapshot, HistorySnapshot } from "../App";

function formatWhen(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value || "";
  const delta = Date.now() - date.getTime();
  const minutes = Math.round(delta / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function HistoryPage({
  history,
  applySnapshot,
}: {
  history: HistorySnapshot;
  applySnapshot: (raw: string | AppSnapshot | undefined) => void;
}) {
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [modeFilter, setModeFilter] = useState("all");

  const modes = useMemo(() => {
    const names = Array.from(new Set(history.items.map((item) => item.mode).filter(Boolean)));
    return [{ value: "all", label: "All modes" }, ...names.map((name) => ({ value: name, label: name }))];
  }, [history.items]);

  const items = useMemo(() => {
    const lc = q.toLowerCase();
    return history.items.filter(
      (h) =>
        (modeFilter === "all" || h.mode === modeFilter) &&
        (!q ||
          h.text.toLowerCase().includes(lc) ||
          h.rawText.toLowerCase().includes(lc) ||
          h.app.toLowerCase().includes(lc) ||
          h.windowTitle.toLowerCase().includes(lc))
    );
  }, [history.items, modeFilter, q]);
  const visibleItems = items.slice(0, q ? 140 : 80);
  const detail = items.find((h) => h.id === selected) || items[0];

  const copyText = (text: string) => {
    window.whisperer?.copyText?.(text).catch(() => {});
  };

  const deleteItem = (id: number) => {
    window.whisperer?.deleteDictation?.(id).then(applySnapshot).catch(() => {});
    if (selected === id) setSelected(null);
  };

  return (
    <div className="page-enter" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "22px 28px 14px" }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <Eyebrow>Log</Eyebrow>
            <h1 style={{ fontSize: 28, fontWeight: 500, letterSpacing: "-0.025em", margin: "6px 0 6px" }}>History</h1>
            <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0, maxWidth: 620, lineHeight: 1.5 }}>
              Real dictations saved by Whisperer.
            </p>
          </div>
          <div style={{ display: "flex", gap: 18, fontSize: 12 }}>
            <span style={{ color: "var(--ink-3)" }}><span className="mono" style={{ color: "var(--ink)", fontWeight: 500 }}>{history.totals.today}</span> today</span>
            <span style={{ color: "var(--ink-3)" }}><span className="mono" style={{ color: "var(--ink)", fontWeight: 500 }}>{history.totals.words}</span> words</span>
            <span style={{ color: "var(--ink-3)" }}><span className="mono" style={{ color: "var(--ink)", fontWeight: 500 }}>{history.totals.minutes}m</span> spoken</span>
          </div>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <Input icon="search" placeholder="Search transcripts, apps, or windows..." value={q} onChange={setQ} style={{ flex: 1 }} />
          <Select value={modeFilter} onChange={setModeFilter} options={modes} width={160} />
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, minHeight: 0, borderTop: "1px solid var(--line-soft)" }}>
        <div className="scroll" style={{ flex: 1, overflow: "auto", borderRight: "1px solid var(--line)", background: "var(--bg-2)" }}>
          {visibleItems.map((h) => {
            const isActive = detail?.id === h.id;
            return (
              <button
                key={h.id}
                onClick={() => setSelected(h.id)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "13px 24px",
                  background: isActive ? "var(--card)" : "transparent",
                  border: "none",
                  borderLeft: isActive ? "2px solid var(--accent)" : "2px solid transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>{h.duration}s</span>
                  <Pill tone={h.status === "error" ? "rec" : "soft"} style={{ height: 18, fontSize: 10, padding: "0 7px" }}>{h.mode}</Pill>
                  <span style={{ flex: 1 }} />
                  <span style={{ fontSize: 11, color: "var(--ink-3)" }}>{h.app}</span>
                </div>
                <div
                  style={{
                    fontSize: 12.5,
                    color: h.status === "error" ? "var(--rec)" : "var(--ink-1)",
                    lineHeight: 1.5,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {h.status === "error" ? h.error || "Recording failed." : h.text || "(empty)"}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6, fontSize: 11, color: "var(--ink-3)" }}>
                  <span>{formatWhen(h.startedAt)}</span>
                  {h.words > 0 && <span>{h.words} words</span>}
                </div>
              </button>
            );
          })}
          {items.length > visibleItems.length && (
            <div style={{ padding: "14px 24px", color: "var(--ink-3)", fontSize: 12.5, textAlign: "center", borderTop: "1px solid var(--line-soft)" }}>
              Showing {visibleItems.length} of {items.length}. Search to narrow the list.
            </div>
          )}
          {items.length === 0 && (
            <div style={{ padding: "40px 24px", color: "var(--ink-3)", fontSize: 13, textAlign: "center" }}>
              No matching dictations.
            </div>
          )}
        </div>

        <div className="scroll" style={{ flex: "0 0 460px", overflow: "auto", padding: "20px 24px", background: "var(--bg)" }}>
          {detail ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
                <Pill tone="accent" style={{ fontWeight: 600 }}>{detail.mode}</Pill>
                <Pill tone="soft">{detail.app}</Pill>
                <Pill tone="soft"><span className="mono">{detail.duration}s</span></Pill>
                <Pill tone="soft"><span className="mono">{detail.words} words</span></Pill>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{formatWhen(detail.startedAt)}</span>
              </div>

              <Eyebrow>Final text</Eyebrow>
              <Card style={{ marginTop: 6, marginBottom: 14, lineHeight: 1.6, whiteSpace: "pre-wrap", fontSize: 13.5 }}>
                {detail.status === "error" ? detail.error || "Recording failed." : detail.finalText || detail.text || "(empty)"}
              </Card>

              {detail.rawText && detail.rawText !== detail.finalText && (
                <>
                  <Eyebrow>Raw transcript</Eyebrow>
                  <Card style={{ marginTop: 6, marginBottom: 14, lineHeight: 1.6, whiteSpace: "pre-wrap", fontSize: 12.5, color: "var(--ink-2)" }}>
                    {detail.rawText}
                  </Card>
                </>
              )}

              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
                <Btn variant="secondary" icon="copy" onClick={() => copyText(detail.finalText || detail.text)}>Copy final</Btn>
                {detail.rawText && <Btn variant="ghost" onClick={() => copyText(detail.rawText)}>Copy raw</Btn>}
                <span style={{ flex: 1 }} />
                <Btn variant="ghost" icon="trash" onClick={() => deleteItem(detail.id)}>Delete</Btn>
              </div>

              <Eyebrow>Context</Eyebrow>
              <div style={{ marginTop: 8 }}>
                {[
                  { src: "Active app", val: detail.app },
                  { src: "Window title", val: detail.windowTitle || "-" },
                  { src: "Paste method", val: detail.pasteMethod || "-" },
                  { src: "STT model", val: detail.sttModel || "-" },
                ].map((c, i) => (
                  <div
                    key={i}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "120px 1fr",
                      padding: "8px 0",
                      borderTop: i === 0 ? "none" : "1px solid var(--line-soft)",
                      fontSize: 12,
                    }}
                  >
                    <span style={{ color: "var(--ink-3)" }}>{c.src}</span>
                    <span style={{ color: "var(--ink-1)" }}>{c.val}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>No dictations yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
