:root {
  color-scheme: light dark;
  --fg: #1a1a1a;
  --bg: #ffffff;
  --muted: #666;
  --border: #ddd;
  --accent: #2b5797;
  --abnormal: #b3261e;
  --card-bg: #f7f7f8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e8e8e8;
    --bg: #16181c;
    --muted: #9aa0a6;
    --border: #333;
    --accent: #7aa7e0;
    --abnormal: #ff6b60;
    --card-bg: #1f2226;
  }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg); margin: 0; }
.page { max-width: 840px; margin: 0 auto; padding: 32px 20px 80px; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.tagline { color: var(--muted); margin-top: 0; }
.ask-form { display: flex; flex-direction: column; gap: 8px; margin: 20px 0; }
.ask-form textarea { font: inherit; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--fg); resize: vertical; }
.ask-form button { align-self: flex-start; padding: 8px 20px; border: none; border-radius: 6px; background: var(--accent); color: #fff; font-weight: 600; cursor: pointer; }
.examples { color: var(--muted); font-size: 0.85rem; }
.answer-panel { margin-top: 28px; padding: 16px 18px; border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg); }
.answer-panel h2 { margin-top: 0; font-size: 1.05rem; }
.citation-marker { color: var(--accent); font-weight: 600; cursor: default; }
.abstained-badge { display: inline-block; margin-bottom: 8px; padding: 2px 8px; border-radius: 10px; background: var(--muted); color: var(--bg); font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
section.panel { margin-top: 24px; }
section.panel h2 { font-size: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.evidence-cards { display: flex; flex-wrap: wrap; gap: 10px; }
.evidence-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; min-width: 180px; background: var(--card-bg); }
.evidence-card .badge { font-size: 0.7rem; text-transform: uppercase; color: var(--accent); font-weight: 700; }
.evidence-card .rid { font-size: 0.7rem; color: var(--muted); font-family: monospace; }
table.lab-results { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
table.lab-results th, table.lab-results td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.flag-abnormal { color: var(--abnormal); font-weight: 700; }
ul.timeline { list-style: none; padding: 0; margin: 0; }
ul.timeline li { padding: 8px 0 8px 16px; border-left: 2px solid var(--border); position: relative; }
ul.timeline li.abnormal { border-left-color: var(--abnormal); }
ul.timeline li::before { content: ""; position: absolute; left: -5px; top: 14px; width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
ul.timeline li.abnormal::before { background: var(--abnormal); }
details.debug-drawer { margin-top: 24px; font-size: 0.85rem; }
details.debug-drawer summary { cursor: pointer; color: var(--muted); }
details.debug-drawer pre { background: var(--card-bg); padding: 10px; border-radius: 6px; overflow-x: auto; }
