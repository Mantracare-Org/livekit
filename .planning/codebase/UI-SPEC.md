# UI-SPEC: MantraAssist Frontend Design Contract

> **Phase:** 1 — Design System & Visual Identity  
> **Status:** draft  
> **Design Direction:** **"OpsCraft"** — B2B enterprise operations dashboard  
> **Inspirations:** Discord (color), Linear (typography/density), Datadog (information hierarchy)  
> **Anti-patterns eliminated:** glassmorphism, radial gradient backgrounds, glow effects, Outfit font, 24px border radii, centered card layouts

---

## 1. Design Direction

### Name: "OpsCraft"

A professional, no-nonsense operations monitoring dashboard that looks like it belongs in a real call center, not an AI demo landing page.

**Vibe:** Discord server admin panel meets Linear project management — dense but readable, structured but not rigid, dark but not moody. Every pixel has a job. No decoration.

**What changed from "AI look":**

| Element | Before (AI Look) | After (OpsCraft) |
|---------|------------------|------------------|
| Background | `#0a0a0c` pure black | `#1e1f22` dark bluish-gray |
| Surfaces | Translucent glass (`rgba(255,255,255,0.03)`) | Solid `#2b2d31` |
| Accent | `#4f46e5` indigo | `#5865F2` blurple |
| Fonts | Outfit (headings) + Inter (body) | Inter everywhere |
| Corner radii | 24px (rounded) | 8px (tight, professional) |
| Card effects | backdrop-filter blur, glow shadows | Solid bg, subtle borders only |
| Layout | Centered card, floating | Full-width grid with top nav anchor |

---

## 2. Color Palette

### 2.1 Background & Surface Hierarchy

Tokens follow a layering principle: deeper = closer to the page, lighter = closer to the user.

```css
:root {
  /* ── Backgrounds (darkest → lightest) ─────────────────────── */
  --bg-page:           #1e1f22;   /* page canvas, nav bar background */
  --bg-surface:        #2b2d31;   /* cards, panels, table rows */
  --bg-elevated:       #313338;   /* dropdowns, modals, hover menus */
  --bg-hover:          #35373c;   /* interactive hover states */
  --bg-active:         #3f4148;   /* active/selected states */
  --bg-input:          #1e1f22;   /* form input backgrounds */

  /* ── Borders ───────────────────────────────────────────────── */
  --border-default:    #3f4148;   /* card borders, dividers */
  --border-strong:     #4e5058;   /* hovered borders, focus rings */
  --border-accent:     #5865F2;   /* focused inputs, active tabs */

  /* ── Text ──────────────────────────────────────────────────── */
  --text-primary:      #f2f3f5;   /* headings, body, primary labels */
  --text-secondary:    #949ba4;   /* secondary info, table headers */
  --text-tertiary:     #6d6f78;   /* placeholders, disabled, meta */
  --text-inverse:      #1e1f22;   /* text on accent/success/danger bgs */

  /* ── Brand / Accent (Blurple — Discord-alike) ─────────────── */
  --accent:            #5865F2;   /* primary CTAs, active links */
  --accent-hover:      #4752c4;   /* button hover, link hover */
  --accent-subtle:     rgba(88, 101, 242, 0.12);  /* bg badges, pills */

  /* ── Semantic Status Colors ────────────────────────────────── */
  --success:           #23a55a;   /* answer rate, completed calls */
  --success-subtle:    rgba(35, 165, 90, 0.12);
  --warning:           #f0b232;   /* queue warning, pending status */
  --warning-subtle:    rgba(240, 178, 50, 0.12);
  --danger:            #da373c;   /* errors, failed calls, disconnect */
  --danger-subtle:     rgba(218, 55, 60, 0.12);
  --info:              #5865F2;   /* informational indicators */

  /* ── Shadows (minimal — no glow, just depth) ───────────────── */
  --shadow-sm:         0 1px 2px rgba(0, 0, 0, 0.3);
  --shadow-md:         0 4px 12px rgba(0, 0, 0, 0.4);
  --shadow-lg:         0 8px 24px rgba(0, 0, 0, 0.5);
}
```

### 2.2 Color Usage Rules

| Token | Used On | NOT Used On |
|-------|---------|-------------|
| `--accent` | Primary buttons, active nav item, focus rings, links, active tab | Backgrounds, table rows, cards |
| `--accent-subtle` | Badge backgrounds, pill backgrounds, subtle indicators | Buttons, interactive elements |
| `--success` | Answer rate metric, completed status badges, success indicators | Primary CTAs, navigation |
| `--danger` | Error badges, disconnect button, failed status | Metrics, primary actions |
| `--warning` | Queue-gauge amber zone, pending status, near-capacity warnings | Positive indicators |

---

## 3. Typography

### 3.1 Font Stack

```css
:root {
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', 'Consolas', monospace;
}
```

- **Inter** for all UI text (headings, body, labels, buttons). One typeface everywhere — Linear-style consistency.
- **JetBrains Mono** for data displays: call IDs, durations, timestamps, metric values, code blocks.

### 3.2 Type Scale

```css
:root {
  --text-xs:   0.75rem;   /* 12px — labels, table headers, timestamps */
  --text-sm:   0.8125rem; /* 13px — body text, table cells, descriptions */
  --text-base: 0.875rem;  /* 14px — default body (slightly smaller = more data on screen) */
  --text-lg:   1rem;      /* 16px — section titles, nav links */
  --text-xl:   1.25rem;   /* 20px — card titles, metric values (small) */
  --text-2xl:  1.625rem;  /* 26px — primary metric values, welcome heading */
  --text-3xl:  2.25rem;   /* 36px — hero metric (Calls Today total) */
}
```

### 3.3 Font Weights

```css
:root {
  --weight-regular: 400;
  --weight-medium:  500;
  --weight-semibold: 600;
}
```

Only three weights. No `300` (too light for readability on dark bg). No `700` (600 is enough emphasis).

### 3.4 Line Heights

```css
:root {
  --leading-body:    1.5;    /* body text, paragraphs */
  --leading-heading: 1.15;   /* headings, metric values */
  --leading-tight:   1.25;   /* small labels, badges */
  --leading-mono:    1.4;    /* monospace text */
}
```

### 3.5 Letter Spacing

```css
:root {
  --tracking-normal:  0em;
  --tracking-wide:    0.02em;  /* uppercase labels, table headers */
  --tracking-mono:    -0.01em; /* monospace data (tighten for readability) */
}
```

---

## 4. Layout

### 4.1 Page Structure

```
┌────────────────────────────────────────────────────────────┐
│  NAV BAR (52px) — fixed top                               │
│  ┌──────────┬──────────────────────────┬────────────────┐  │
│  │ Logo+Nav │          (spacer)        │ User + Logout  │  │
│  └──────────┴──────────────────────────┴────────────────┘  │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  MAIN GRID (max-width: 1400px, centered, padding: 24px)   │
│  ┌────────────────────────────────┬─────────────────────┐  │
│  │                                │                      │  │
│  │  LEFT COLUMN                   │  RIGHT COLUMN        │  │
│  │  (flex: 1, gap: 20px)         │  (width: 340px)     │  │
│  │                                │                      │  │
│  │  ┌─ Metrics Row (4 cols) ──┐  │  ┌─ Active Calls ─┐  │  │
│  │  └─────────────────────────┘  │  └────────────────┘  │  │
│  │  ┌─ Call History Table ────┐  │  ┌─ Queue Gauge ──┐  │  │
│  │  └─────────────────────────┘  │  └────────────────┘  │  │
│  │                                │  ┌─ Activity Feed ┐  │  │
│  │                                │  └────────────────┘  │  │
│  └────────────────────────────────┴─────────────────────┘  │
│                                                            │
├────────────────────────────────────────────────────────────┤
│  FOOTER (optional, simple text)                           │
└────────────────────────────────────────────────────────────┘
```

### 4.2 Grid Definition

```css
.main-grid {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px 32px;
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 20px;
}
```

### 4.3 Responsive Breakpoints

```css
/* Tablet (1024px and below) — stack columns */
@media (max-width: 1024px) {
  .main-grid {
    grid-template-columns: 1fr;
    padding: 16px;
  }
  .metrics-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

/* Mobile (640px and below) — single column everything */
@media (max-width: 640px) {
  .metrics-row {
    grid-template-columns: 1fr;
  }
  .navbar {
    padding: 12px 16px;
  }
  .main-grid {
    padding: 12px;
  }
}
```

### 4.4 Nav Bar Spec

```css
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;                       /* fixed height */
  padding: 0 24px;
  background: var(--bg-page);
  border-bottom: 1px solid var(--border-default);
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.nav-logo {
  display: flex;
  align-items: center;
  gap: 10px;
}

.nav-logo-icon {
  width: 28px;
  height: 28px;
  background: var(--accent);
  border-radius: 6px;                /* tighter than before */
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.nav-logo-icon svg {
  width: 16px;
  height: 16px;
  color: white;
}

.nav-title {
  font-family: var(--font-sans);
  font-size: var(--text-lg);         /* 16px */
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.nav-links {
  display: flex;
  gap: 2px;                          /* tight grouping */
}

.nav-link {
  padding: 6px 14px;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);         /* 13px */
  font-weight: var(--weight-medium);
  text-decoration: none;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-link.active {
  background: var(--accent);
  color: white;
  /* NO glow box-shadow */
}

.nav-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.nav-user {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.btn-logout {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-logout:hover {
  border-color: var(--danger);
  color: var(--danger);
  background: var(--danger-subtle);
}
```

---

## 5. Component Specs

### 5.1 Cards (Generic)

Every card in the system shares this base:

```css
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;                /* was 16px */
  overflow: hidden;
  box-shadow: none;                  /* intentionally omitted */
}

.card-header {
  padding: 16px 20px;                /* was 18px 24px */
  border-bottom: 1px solid var(--border-default);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header h3 {
  font-family: var(--font-sans);
  font-size: var(--text-base);       /* 14px */
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  margin: 0;
}

.card-body {
  padding: 16px 20px;                /* was 20px 24px */
}
```

### 5.2 Metrics Bar

```css
.metrics-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;                        /* was 16px — tighter */
}

.metric-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 18px 20px;               /* was 20px 24px */
  text-align: center;
}

.metric-value {
  font-family: var(--font-mono);    /* mono = data precision feel */
  font-size: var(--text-2xl);       /* 26px — was 2rem via Outfit */
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  line-height: var(--leading-heading);
  letter-spacing: var(--tracking-mono);
}

.metric-label {
  font-family: var(--font-sans);
  font-size: var(--text-xs);        /* 12px */
  font-weight: var(--weight-medium);
  color: var(--text-secondary);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
}

/* Color variants */
.metric-card.accent .metric-value { color: var(--accent); }
.metric-card.success .metric-value { color: var(--success); }
.metric-card.warning .metric-value { color: var(--warning); }
.metric-card.danger .metric-value { color: var(--danger); }
```

### 5.3 Buttons

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 6px;               /* was 12px, was pill shapes */
  font-family: var(--font-sans);
  font-size: var(--text-sm);        /* 13px */
  font-weight: var(--weight-medium);
  line-height: 1;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s ease, border-color 0.15s ease;
  /* NO box-shadow, NO transform on hover */
}

.btn-primary {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.btn-primary:hover {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}

.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border-default);
}

.btn-secondary:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-strong);
}

.btn-danger {
  background: var(--danger);
  color: white;
  border-color: var(--danger);
}

.btn-danger:hover {
  background: #c62f36;              /* slightly darker red */
}

.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border-color: transparent;
}

.btn-ghost:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* For the full-width action button (login, start session) */
.btn-block {
  width: 100%;
  padding: 12px 20px;
}
```

### 5.4 Login Card

```css
.login-page {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: var(--bg-page);
  /* NO radial gradients, NO glow effects */
}

.login-card {
  width: 100%;
  max-width: 400px;                 /* was 420px */
  background: var(--bg-surface);    /* SOLID — no glass */
  border: 1px solid var(--border-default);
  border-radius: 8px;              /* was 24px */
  padding: 36px 32px;              /* was 48px 40px */
  box-shadow: var(--shadow-md);     /* subtle, no glow */
}

.login-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;
}

.login-title {
  font-family: var(--font-sans);
  font-size: var(--text-xl);       /* 20px */
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.login-subtitle {
  font-family: var(--font-sans);
  font-size: var(--text-sm);       /* 13px */
  color: var(--text-secondary);
  margin-bottom: 28px;             /* was 36px */
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
}

.field label {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
}

.field input[type="text"],
.field input[type="password"] {
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  border-radius: 6px;              /* was 12px */
  padding: 12px 14px;              /* was 14px 16px */
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);     /* 14px */
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.field input:focus {
  outline: none;
  border-color: var(--border-accent);
  box-shadow: 0 0 0 3px var(--accent-subtle);  /* subtle ring, no glow */
}

.login-error {
  color: var(--danger);
  font-size: var(--text-sm);
  margin-top: 12px;
  text-align: center;
  min-height: 20px;
}
```

### 5.5 Active Call Cards

```css
#active-calls-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.call-card {
  background: var(--bg-elevated);  /* one level up for distinction */
  border: 1px solid var(--border-default);
  border-radius: 6px;
  padding: 12px 16px;
  transition: border-color 0.15s ease;
}

.call-card:hover {
  border-color: var(--border-strong);
}

.call-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.call-id {
  font-family: var(--font-mono);
  font-weight: var(--weight-medium);
  font-size: var(--text-sm);       /* 13px */
  color: var(--text-primary);
  letter-spacing: var(--tracking-mono);
}

.call-card-body {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.call-room {
  font-family: var(--font-sans);
}
```

### 5.6 Status Badges

```css
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  padding: 3px 10px;
  border-radius: 4px;              /* was 20px (pill) — now squared off */
  text-transform: capitalize;
}

/* Status background variants (subtle backgrounds) */
.status-in_progress,
.status-completed {
  background: var(--success-subtle);
  color: var(--success);
}

.status-dispatching,
.status-pending {
  background: var(--warning-subtle);
  color: var(--warning);
}

.status-failed,
.status-error {
  background: var(--danger-subtle);
  color: var(--danger);
}

.status-unknown {
  background: var(--accent-subtle);
  color: var(--accent);
}

/* Status dot (small circle, used in tables) */
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.status-dot.completed,
.status-dot.in_progress { background: var(--success); }
.status-dot.dispatching,
.status-dot.busy { background: var(--warning); }
.status-dot.failed,
.status-dot.error { background: var(--danger); }
.status-dot.no_answer,
.status-dot.unknown { background: var(--text-tertiary); }
```

### 5.7 Call History Table

```css
.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-sans);
  font-size: var(--text-sm);       /* 13px */
}

thead th {
  text-align: left;
  padding: 10px 16px;
  color: var(--text-secondary);
  font-weight: var(--weight-medium);
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
  border-bottom: 1px solid var(--border-default);
  background: var(--bg-page);      /* slightly darker header */
  position: sticky;
  top: 0;
}

tbody td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-default);
  color: var(--text-primary);
}

tbody tr {
  transition: background 0.1s ease;
}

tbody tr:hover {
  background: var(--bg-hover);
}

.cell-mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  letter-spacing: var(--tracking-mono);
}

.cell-time {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
```

### 5.8 Queue Gauge

```css
.queue-stats {
  display: flex;
  justify-content: space-around;
  gap: 16px;
  margin-bottom: 16px;
}

.queue-stat {
  text-align: center;
}

.queue-stat-value {
  font-family: var(--font-mono);    /* mono for data precision */
  font-size: var(--text-xl);        /* 20px */
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  letter-spacing: var(--tracking-mono);
}

.queue-stat-label {
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
  margin-top: 2px;
}

.gauge-track {
  width: 100%;
  height: 6px;                      /* was 8px */
  background: var(--bg-elevated);
  border-radius: 3px;
  overflow: hidden;
}

.gauge-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease, background 0.4s ease;
}

/* Gauge color thresholds (applied via JS) */
.gauge-fill.low    { background: var(--success); }   /* 0-50% */
.gauge-fill.medium { background: var(--warning); }   /* 50-80% */
.gauge-fill.high   { background: var(--danger); }    /* 80-100% */

.gauge-label {
  text-align: right;
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-top: 4px;
}
```

### 5.9 Activity Feed

```css
#feed-list {
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}

.feed-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 6px 0;
  font-size: var(--text-sm);
}

.feed-item + .feed-item {
  border-top: 1px solid var(--border-default);
  padding-top: 7px;                 /* account for border */
}

.feed-time {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  flex-shrink: 0;
  min-width: 58px;
  letter-spacing: var(--tracking-mono);
}

.feed-msg {
  color: var(--text-primary);
}

.feed-item.feed-success .feed-msg { color: var(--success); }
.feed-item.feed-warning .feed-msg { color: var(--warning); }
.feed-item.feed-info .feed-msg    { color: var(--text-primary); }
```

### 5.10 Empty States

```css
.empty-state {
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  padding: 32px 16px;
  font-style: italic;              /* subtle visual cue */
}
```

### 5.11 Loading States

```css
.loading-spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid var(--accent-subtle);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Skeleton placeholder for data-loading */
.skeleton {
  background: var(--bg-elevated);
  border-radius: 4px;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.7; }
}
```

---

## 6. Scrollbar Styling

```css
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--bg-active);
  border-radius: 3px;
}

* {
  scrollbar-width: thin;
  scrollbar-color: var(--bg-active) transparent;
}
```

---

## 7. Spacing Scale

```css
:root {
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-5:  20px;
  --space-6:  24px;
  --space-8:  32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;
}
```

Use `--space-*` tokens where possible. 8-point scale with 12px and 20px additions for fine-tuning.

---

## 8. Focus & Accessibility

```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

:focus:not(:focus-visible) {
  outline: none;
}
```

- All interactive elements must have visible focus rings.
- Buttons: minimum 44px tap target (use padding, not height).
- Color contrast: text-primary (#f2f3f5 on #2b2d31) = 12.4:1 (surpasses WCAG AAA).
- Text-secondary (#949ba4 on #1e1f22) = 5.6:1 (surpasses WCAG AA).
- Text-tertiary (#6d6f78 on #1e1f22) = 3.7:1 (for non-essential text only).

---

## 9. Copywriting Contract

| Element | Copy | Notes |
|---------|------|-------|
| Login heading | "MantraAssist" | Logo wordmark |
| Login subtitle | "Sign in to the voice operations dashboard" | Was the same — keep |
| Login button | "Sign In" | Was the same |
| Logout button | "Sign Out" | Was the same |
| Primary CTA | "Start Test Session" | Test console page |
| Metric labels | "Calls Today" / "Answer Rate" / "Avg Duration" / "Active Now" | Already exists |
| Call History header | "Call History" | With total count pill |
| Active Calls header | "Active Calls" | With count badge |
| Queue & Capacity header | "Queue & Capacity" | Already exists |
| Activity Feed header | "Activity Feed" | Already exists |
| Empty state (calls) | "No calls recorded yet" | Already exists |
| Empty state (active) | "No active calls" | Already exists |
| Empty state (feed) | "Waiting for activity..." | Already exists |
| Loading (table) | "Loading..." | Keep |
| Error (table) | "Could not load call history" | Already exists |
| SSE error | "Reconnecting to event stream..." | Keep |
| Destructive actions | Sign Out (no confirmation needed) / Disconnect (no confirmation needed) | Low-risk |

---

## 10. Anti-Patterns Checklist

This is the **"does my UI still look like AI slop?"** checklist. Every card and component must be checked against these:

| Anti-Pattern | Status | How We Enforce |
|--------------|--------|----------------|
| Glassmorphism (`backdrop-filter: blur`) | ❌ BANNED | Never use `backdrop-filter` or `rgba` backgrounds on cards |
| Glow shadows (`box-shadow` with accent color) | ❌ BANNED | Use only `--shadow-*` tokens (black/dark shadows) |
| Radial gradient backgrounds | ❌ BANNED | Solid `--bg-page` only |
| Indigo accent `#4f46e5` | ❌ BANNED | Replaced with blurple `#5865F2` |
| Rounded corners > 12px | ❌ BANNED | Max border-radius: 8px (6px for tight elements) |
| Outfit / Space Grotesk / Clash Display fonts | ❌ BANNED | Inter only for UI |
| Gradient text or gradient borders | ❌ BANNED | Solid colors only |
| Pill-shaped badges (> 6px radius) | ❌ BANNED | Square badges with 4px radius |
| Floating/centered card (no nav anchor) | ❌ BANNED | Full-width layout with sticky top nav |
| Animated gradient backgrounds | ❌ BANNED | Never |
| "AI" decorative illustrations | ❌ BANNED | No decorative illustrations |

---

## 11. Implementation Notes

### CSS Architecture
- Use CSS custom properties (the `:root` variables above) exclusively.
- No CSS preprocessor needed — variables are native.
- `dashboard.html` and `login.html` each get a `<style>` block that starts with the `:root` definitions. Future phase: extract to `style.css`.

### What to keep from current code
- HTML structure (tables, grids, card classes)
- API fetch patterns, SSE connection, activity feed logic
- Responsive breakpoint structure
- Status badge naming conventions

### What to replace
- Every `--accent-color`, `--glass-bg`, `--glass-border`, `--accent-glow` variable
- Every `backdrop-filter: blur()` declaration
- Every `box-shadow` with `var(--accent-glow)`
- Every `radial-gradient` on body/page backgrounds
- `border-radius: 16px` → `8px` or `6px`
- `font-family: 'Outfit'` → `var(--font-sans)`
- `font-family: monospace` → `var(--font-mono)`
- `24px` border-radius on login → `8px`

### Light Mode (Future Phase)
- Not in scope for this phase.
- When implemented: flip the `:root` variables. Light mode colors would use the same token names but with light-optimized values (white page bg, dark text, surface gray, same accent).

---

## Design Contract Verification

| Check | Value |
|-------|-------|
| Spacing scale | 8-point (4, 8, 12, 16, 20, 24, 32, 40, 48, 64) |
| Typography sizes | 6 (12, 13, 14, 16, 20, 26, 36px) |
| Typography weights | 3 (400, 500, 600) |
| Body line-height | 1.5 |
| Heading line-height | 1.15 |
| Color: dominant (60%) | `#1e1f22` (page bg) |
| Color: secondary (30%) | `#2b2d31` (surfaces) |
| Color: accent (10%) | `#5865F2` (buttons, links, active states) |
| Semantic colors | 3 (success, warning, danger) |
| Corner radius (max) | 8px |
| Glassmorphism | BANNED |
| Glow effects | BANNED |
| Gradient backgrounds | BANNED |
| Indigo accent | BANNED |
