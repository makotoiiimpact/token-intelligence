# Token Intelligence — Design System Reference

Based on Ron Design credit dashboard aesthetic. Dark, glassy, premium feel.

## Color Palette

### Primary Colors
- **Background**: #000000 (pure black)
- **Card Background**: rgba(255, 255, 255, 0.04) — near-black with subtle transparency
- **Card Border**: rgba(255, 255, 255, 0.08) — barely visible glass edge
- **Text Primary**: #FFFFFF
- **Text Secondary**: rgba(255, 255, 255, 0.6)
- **Text Tertiary**: rgba(255, 255, 255, 0.35)

### Accent Colors (from design system image)
- **Green (Primary Accent)**: #19F58C — the signature green, used for healthy/positive states
- **Red (Critical)**: #FF423D — alerts, critical tips, over-threshold warnings
- **Yellow (Warning)**: #FFD600 — caution states, approaching thresholds
- **Cyan (Info)**: #00FFE0 — informational, neutral data points
- **Purple (AI Features)**: #8F00FF — AI-powered analysis, premium features
- **Blue (Links/Actions)**: #0066FF — interactive elements, clickable items

### Severity Mapping for Tips/Health
- **Healthy / Good**: #19F58C (green)
- **Warning / Caution**: #FFD600 (yellow)
- **Critical / Bad**: #FF423D (red)
- **Info / Neutral**: #00FFE0 (cyan)
- **AI-Powered**: #8F00FF (purple)

### Gradients
- **Hero gradient**: linear-gradient(135deg, #000000 0%, #19F58C 100%) — used on large cards
- **Glass effect**: background: rgba(255, 255, 255, 0.04); backdrop-filter: blur(20px);
- **Card glow on hover**: box-shadow: 0 0 30px rgba(25, 245, 140, 0.1);

## Typography

### Font Family
- **Primary**: 'Red Hat Display', sans-serif (for headings, large numbers)
- **Secondary**: 'Red Hat Text', sans-serif (for body copy, labels)
- **Mono**: 'Red Hat Mono', monospace (for token counts, code, technical data)
- **Fallback**: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif

### Font Loading (CDN-free for local dashboard)
Download Red Hat Display/Text/Mono from Google Fonts, save to web/fonts/
Load via @font-face in CSS — no external CDN calls (privacy principle)

### Scale
- **Hero numbers** (token counts, health score): 48-64px, Red Hat Display, font-weight: 300
- **Card titles**: 20-24px, Red Hat Display, font-weight: 500
- **Body text**: 14-16px, Red Hat Text, font-weight: 400
- **Labels/captions**: 12px, Red Hat Text, font-weight: 400, text-transform: uppercase, letter-spacing: 0.05em
- **Data values**: 16-20px, Red Hat Mono, font-weight: 500
- **Small data**: 12px, Red Hat Mono

## Card System

### Standard Card
```css
.card {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 20px;
    padding: 24px;
    transition: all 0.3s ease;
}
.card:hover {
    border-color: rgba(255, 255, 255, 0.12);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}
```

### Highlighted Card (for health score, key metrics)
```css
.card-highlight {
    background: linear-gradient(135deg, rgba(25, 245, 140, 0.08) 0%, rgba(0, 0, 0, 0.4) 100%);
    border: 1px solid rgba(25, 245, 140, 0.2);
    border-radius: 20px;
}
```

### Glass Card (for overlays, modals)
```css
.card-glass {
    background: rgba(255, 255, 255, 0.06);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 24px;
}
```

## Layout

### Grid System
- Dashboard uses CSS Grid with auto-fill columns
- Cards have consistent 16px gap
- Sidebar navigation with icon pills (like the colored circles in the reference)
- Top navigation bar with pill-shaped active tab indicator

### Navigation
- **Tab bar**: Horizontal, top of page
- **Active tab**: Pill-shaped background (rgba(255,255,255,0.1)), rounded-full
- **Inactive tabs**: Text only, rgba(255,255,255,0.5)
- **Tab hover**: Text brightens to full white

### Responsive Breakpoints
- Desktop: 4-column grid
- Tablet: 2-column grid
- Mobile: 1-column stack

## Chart Styling (ECharts)

### Theme Colors for ECharts
```javascript
const chartTheme = {
    backgroundColor: 'transparent',
    textStyle: { color: 'rgba(255, 255, 255, 0.6)', fontFamily: 'Red Hat Text' },
    title: { textStyle: { color: '#ffffff', fontFamily: 'Red Hat Display' } },
    line: { itemStyle: { borderWidth: 2 } },
    categoryAxis: {
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.05)' } },
        axisLabel: { color: 'rgba(255, 255, 255, 0.4)' }
    },
    valueAxis: {
        axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.05)' } },
        axisLabel: { color: 'rgba(255, 255, 255, 0.4)' }
    },
    color: ['#19F58C', '#0066FF', '#8F00FF', '#FFD600', '#FF423D', '#00FFE0']
};
```

### Threshold Lines
- 120K token threshold: dashed line in #FFD600 (yellow)
- 250K danger zone: dashed line in #FF423D (red)
- Fill area below threshold in rgba(25, 245, 140, 0.05)
- Fill area above threshold in rgba(255, 66, 61, 0.05)

## Iconography

### Health Score Visualization
- Circular progress ring (like the credit score "730" in the reference)
- Color transitions: green (80-100) → yellow (50-79) → red (0-49)
- Large number in center, label below
- Rainbow/gradient bar underneath showing score position

### Status Indicators
- Green dot: healthy session
- Yellow dot: warning
- Red dot: critical
- Check/X icons for habit tracker (like the timeline calendar in reference)

## Micro-interactions

- Cards lift slightly on hover (translateY(-2px))
- Numbers animate counting up on page load
- Tab transitions slide content
- Tooltip appears on chart hover with glass effect
- Tips cards can be dismissed with slide-out animation

## Component Inventory

### Overview Tab
- Hero metric cards (4-column): Total Tokens, Sessions, Avg Health Score, Est. Cost
- Daily token burn chart (area chart with threshold overlay)
- Quick Wins panel (top 3 actionable tips)
- Per-project token comparison (horizontal bar)
- Model distribution (donut chart)

### Tips Tab
- Severity filter pills (All / Critical / Warning / Info)
- Tip cards with severity accent border-left
- Each card: icon + title + description + estimated savings badge
- "Analyze with AI" button (purple accent, #8F00FF)
- Historical tip trend (are tips decreasing over time?)

### Session Discipline Tab
- Health score ring (hero, center)
- Score trend line chart (last 30 sessions)
- Habit tracker grid (calendar-style, green checks / red Xs, like the reference)
- Metrics cards: avg session length, % over threshold, correction rate
- Recommendations panel

### Sessions Tab
- Session list with health score badges
- Expandable row → turn-by-turn view
- Compound token curve chart per session
- Correction cycles highlighted in red
- Optimal handoff points marked with yellow markers
