# Card + Chat size baseline (2026-07-20)

Recorded **before** the enlarge pass. Say「回退到基线尺寸」to restore these values in `src/styles/globals.css`.

## Stage / frame

| Selector | Property | Baseline |
|----------|----------|----------|
| `.card-stage` | `grid-template-columns` | `46px minmax(570px, 820px) 46px` |
| `.card-stage` | `gap` | `13px` |
| `.card-stage` | `height` | `calc(100dvh - 76px)` |
| `.card-transition-frame` | `height` | `clamp(420px, 57dvh, 560px)` |
| `.card-stage.chat-focus-stage` | `grid-template-columns` | `42px minmax(0, 1040px) 42px` |
| `.card-stage.chat-focus-stage` | `gap` | `10px` |
| `.card-chat-unit` | `--focus-height` | `clamp(500px, 64dvh, 630px)` |
| `.card-chat-unit` | `gap` | `10px` |
| `.card-chat-unit.chat-expanded` | `gap` | `14px` |
| `.card-chat-unit.chat-expanded .context-chat` | `height` / `max-height` | `calc(var(--focus-height) + 28px)` |
| `@media (max-width: 1040px)` `.card-stage` | `grid-template-columns` | `42px minmax(0, 720px) 42px` |

## Focus card shell

| Selector | Property | Baseline |
|----------|----------|----------|
| `.focus-card` | `padding` | `clamp(26px, 3.25dvh, 36px) clamp(30px, 3.2vw, 46px)` |
| `.focus-card` | `border-radius` | `28px` |
| `.overall-dial` | `width` / `height` | `134px` |

## Typography (card)

| Selector | Property | Baseline |
|----------|----------|----------|
| `.eyebrow` | `font-size` | `10px` |
| `.overview-hero h1, .task-title-block h1` | `font-size` | `clamp(29px, 2.9vw, 42px)` |
| `.overview-hero p` | `font-size` | `12px` |
| `.task-title-block h1` (override) | `font-size` | `clamp(25px, 2.45vw, 36px)` |
| `.task-title-block p` | `font-size` | `clamp(11px, 1.02vw, 13px)` |
| `.status-pill` | `font-size` / `height` | `9px` / `27px` |
| `.metric-cell strong` | `font-size` | `18px` |
| `.metric-cell > div > span` | `font-size` | `10px` |
| `.overview-bottom strong` | `font-size` | `12px` |
| `.progress-copy strong` | `font-size` | `12px` |
| `.progress-ring.lg` | size | `64px` |
| `.progress-ring > span` | `font-size` | `8px` |

## Chat bar (collapsed + expanded)

| Selector | Property | Baseline |
|----------|----------|----------|
| `.context-chat` | `min-height` | `112px` |
| `.context-chat` | `padding` | `11px 12px 10px` |
| `.context-chat` | `border-radius` | `17px` |
| `.chat-context-row > div:first-child` | `font-size` | `9px` |
| `.quick-actions button` | `height` / `font-size` | `22px` / `8px` |
| `.latest-message` | `font-size` / `height` | `9px` / `24px` |
| `.context-chat form` | `height` | `38px` |
| `.context-chat form input` | `font-size` | `10px` |
| `.agent-mini-mark` | size | `23px` |
| `.compact-context-card` | `padding` / `border-radius` | `20px` / `24px` |
| `.compact-task-copy h2` | `font-size` | `clamp(18px, 1.55vw, 23px)` |
| `.compact-task-copy p` | `font-size` | `9px` |

## Enlarge pass applied after this snapshot

Roughly **+12–15%** on stage width, card height, dial, and primary/chat type. See git / this file to roll back.
