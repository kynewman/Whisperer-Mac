import { Card, Eyebrow, Pill, SectionTitle } from "../primitives";
import type { HistorySnapshot } from "../App";

export default function StatsPage({ history }: { history: HistorySnapshot }) {
  const stats = history.stats || {
    totalDictations: history.items.length,
    totalWords: history.totals.words,
    totalMinutes: history.totals.minutes,
    topWords: [],
    last7Days: [],
  };
  const maxWords = Math.max(1, ...stats.last7Days.map((day) => day.words));

  return (
    <div className="page-enter scroll page-shell">
      <div style={{ marginBottom: 22 }}>
        <Eyebrow>Usage</Eyebrow>
        <h1 style={{ fontSize: 28, fontWeight: 500, letterSpacing: "-0.025em", margin: "6px 0 6px" }}>Stats</h1>
        <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0, maxWidth: 620, lineHeight: 1.5 }}>
          A local snapshot of how much Whisperer has been helping.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14, marginBottom: 18 }}>
        {[
          { value: stats.totalWords.toLocaleString(), label: "total words transcribed" },
          { value: stats.totalDictations.toLocaleString(), label: "dictations captured" },
          { value: `${stats.totalMinutes.toLocaleString()}m`, label: "recording time" },
        ].map((item) => (
          <Card key={item.label}>
            <div className="mono" style={{ fontSize: 26, color: "var(--ink)", fontWeight: 600 }}>{item.value}</div>
            <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 6 }}>{item.label}</div>
          </Card>
        ))}
      </div>

      <SectionTitle>Last 7 Days</SectionTitle>
      <Card style={{ marginBottom: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 12, height: 220, alignItems: "end" }}>
          {stats.last7Days.map((day) => {
            const height = Math.max(6, Math.round((day.words / maxWords) * 150));
            return (
              <div key={day.date} style={{ display: "grid", gap: 8, alignItems: "end", justifyItems: "center" }}>
                <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{day.words.toLocaleString()}</div>
                <div
                  title={`${day.words} words, ${day.dictations} dictations`}
                  style={{
                    width: "58%",
                    height,
                    borderRadius: 3,
                    background: "var(--accent)",
                    opacity: day.words ? 0.95 : 0.28,
                  }}
                />
                <div style={{ color: "var(--ink-2)", fontSize: 12, fontWeight: 500 }}>{day.label}</div>
              </div>
            );
          })}
        </div>
      </Card>

      <SectionTitle>Frequently Used Words</SectionTitle>
      <Card>
        {stats.topWords.length ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {stats.topWords.map((item) => (
              <Pill key={item.word} tone="accent" style={{ height: 26 }}>
                {item.word}
                <span className="mono" style={{ opacity: 0.75 }}>{item.count}</span>
              </Pill>
            ))}
          </div>
        ) : (
          <div style={{ color: "var(--ink-3)", fontSize: 13 }}>No word stats yet.</div>
        )}
      </Card>
    </div>
  );
}
