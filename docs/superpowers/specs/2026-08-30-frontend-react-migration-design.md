# Reconcile → Recover: React frontend migration

**Date:** 2026-08-30
**Status:** approved by user, pending implementation plan

## Goal

Replace [app/static/index.html](../../app/static/index.html) — a 2,482-line
vanilla-JS, no-build-step, no-CDN single file — with a React + Tailwind +
shadcn/ui app, so Bklit UI (chart components) and Kokonut UI (component
registry) can be used as intended (via the shadcn CLI), and Motion
(motion.dev) can drive the page's animation.

**The visual identity does not change.** The existing RazorpayX Banking+
theming — pure-black page, single yellow accent, the dataviz-validated
`--chart-*` palette, Inter/IBM Plex Mono type pairing — is preserved exactly.
These libraries are implementation infrastructure, not a restyle.

## Context

- The backend (`app/*.py`, 109 pytest tests) is unaffected by this migration
  and is explicitly out of scope. See `PROJECT_SUMMARY.md` and the
  `sync-logic-frozen` note on the payout sync path in particular — nothing
  here touches it.
- A Next.js + shadcn scaffold was started and removed earlier this session
  (commit `975c747` added it, `5dff3c3` removed it) for being incomplete and
  out of that session's scope — `page.tsx` was still `create-next-app`
  boilerplate, and `node_modules`/`.next` had been committed by mistake. Two
  files from that scaffold are worth reusing as a starting point when the
  implementation plan is written: `frontend/src/lib/api.ts` (a hand-written
  fetch wrapper) and `frontend/src/lib/types.ts` (response types), both
  recoverable via `git show 975c747:frontend/src/lib/api.ts`. This migration
  supersedes that attempt with Vite instead of Next.js (see Decision 2) and
  actually carries it through.
- No CORS is configured today (`app/main.py` mounts the dashboard same-origin
  via `StaticFiles`). The dev workflow below avoids ever needing it.

## Decisions made during brainstorming

1. **Visual identity: preserved exactly**, not evolved. Kokonut UI/Bklit
   components are restyled to the existing design tokens; effects that don't
   fit the product's disciplined tone (particle buttons, shimmer text, liquid
   glass) are skipped by default.
2. **Build tooling: Vite**, not Next.js. `npm run build` outputs static
   assets; FastAPI keeps serving them with `StaticFiles` exactly as today —
   no new server process, no SSR.
3. **Charts: migrated to Bklit** (shadcn chart components, Recharts under the
   hood). The vendored D3 dependency is dropped entirely once the three
   existing charts (gap-hero, backlog-by-cause, daily timeline) are ported.

## Architecture

```
recon-recover/
  frontend/                  # new
    src/
      index.css              # :root tokens, moved verbatim from index.html
      main.tsx
      App.tsx
      components/
      lib/
        api.ts                # typed fetch wrapper (start from git show 975c747:frontend/src/lib/api.ts)
        types.ts               # response types (start from git show 975c747:frontend/src/lib/types.ts)
    index.html                 # Vite entry (not the app — just the shell)
    vite.config.ts
    tailwind.config.ts
    components.json            # shadcn registry config incl. @bklit, @kokonutui
    package.json
  app/
    static/
      dist/                   # npm run build output, git-ignored, served by FastAPI
      vendor/                 # deleted once D3 migration (Decision 3) lands
    main.py                    # StaticFiles mount repointed to app/static/dist
```

- **Dev workflow:** `vite dev` runs on its own port; `vite.config.ts` proxies
  every backend route (`/health`, `/funnel`, `/exceptions`, `/audit`,
  `/analytics/*`, `/pipeline/*`, `/actions/*`, `/assistant/chat`, etc. — the
  full list is the 16 routes in `app/main.py`) to FastAPI on `:8000`. The
  browser only ever talks to one origin, so no CORS changes to the backend.
- **Build/serve workflow:** `npm run build` → `app/static/dist/`. `main.py`'s
  `StaticFiles(directory="app/static", html=True)` mount changes to point at
  `app/static/dist`. No other backend change.
- `frontend/node_modules` and `app/static/dist` are added to `.gitignore` —
  build artifacts are generated, not committed (the exact mistake the removed
  Next.js scaffold made).
- Package manager: **npm** (Node 22 / npm 10 confirmed installed; no pnpm on
  this machine, and shadcn's CLI supports npm equivalently).

## Design tokens

The `:root` block in `index.html` (lines ~21–121: `--page`, `--surface-*`,
`--brand*`, status colors, `--chart-*`, `--fs-*`, `--shadow`, `--focus`) and
its `@media (prefers-color-scheme: light)` / `[data-theme]` overrides move
into `frontend/src/index.css` **verbatim** — not reauthored as a Tailwind
color palette. `tailwind.config.ts` maps theme colors to `var(--brand)`,
`var(--surface-1)`, etc., so shadcn/Kokonut UI/Bklit components inherit the
real tokens instead of shadcn's default zinc/slate palette. The Google Fonts
`<link>` tags (Inter, IBM Plex Mono) and the `.mono` class
(`font-feature-settings: 'ss03' 1, 'zero' 1`) carry over unchanged.

## Library roles

- **Kokonut UI / shadcn primitives** — structural components only: buttons,
  inputs, selects, a dialog for the chat panel, tabs for the rail/sidebar
  nav. Installed via `npx shadcn add @kokonutui/<name>` then restyled to the
  existing tokens. Any component with a strong default aesthetic (particle
  buttons, shimmer text, liquid glass cards) is skipped unless a specific
  spot calls for it — that comes back as a question during implementation,
  not a default choice.
- **Bklit** — replaces the three vendored-D3 charts with Bklit chart
  components, re-applying the validated `--chart-bar` / `--chart-bar-dim` /
  `--chart-alarm` / `--chart-context` tokens as the chart's color scale.
  `app/static/vendor/d3.v7.9.0.min.js` is deleted once this lands.
- **Motion** — page-load stagger for the shell, tab cross-fade (the four
  `role="tabpanel"` sections), chat panel enter/exit, hover/press states on
  rail-nav items and cards. A few deliberate spots, matching the existing
  design's restraint — not applied everywhere by default.
- **Lenis** (already vendored, `app/static/vendor/lenis.v1.3.26.*`) is kept
  as-is for the `[data-lenis-prevent]` scroll containers (exception table,
  audit table, chat log) — it's orthogonal to the React migration.

## Component inventory (from the current single file)

| Current (`index.html`) | Migrates to |
|---|---|
| Icon rail + labelled sidebar (`rail-nav` / `sidebar`, `data-tab`) | React nav component, shadcn tabs primitive under the hood |
| Overview tab: gap-hero card + stats | React component; gap-hero chart → Bklit |
| Needs attention tab: filters + exceptions table | React component; filters → shadcn select |
| Insights tab: backlog-by-cause + daily timeline charts | React components; both charts → Bklit |
| Activity log tab: audit table | React component, same Lenis scroll container |
| Chat panel (`#chat-panel`, `#chat-form`) | React component, shadcn dialog, POST `/assistant/chat` |
| Developer-controls button (`#rail-settings`) | Same affordance — it's a one-line status toast from `GET /integration/status` today, not a drawer; stays that simple unless asked to expand |
| CSV export buttons (`#btn-export-exceptions`, `#btn-export-audit`) | Same client-side CSV generation, ported as-is |

## Data flow

All 16 backend routes are plain request/response — no SSE, no websockets
(confirmed: chat is a single POST, tab data is fetched on-demand via refresh
buttons, nothing currently polls). TanStack Query (`@tanstack/react-query`)
wraps `frontend/src/lib/api.ts` for:
- Query caching + manual refetch, replacing today's refresh-button handlers
  (`btn-refresh-overview`, `-exceptions`, `-insights`, `-audit`).
- Mutation state for the write endpoints: `/pipeline/reconcile`,
  `/pipeline/route`, `/pipeline/sync-payouts`, `/actions/{id}/confirm`,
  `/exceptions/{key}/resolve`, `/exceptions/{key}/recheck`.

## Error handling

Preserves the existing pattern (inline status text / toasts using the same
status-pill tokens: `--green`/`--red`/`--orange` on their `-bg` tint) rather
than introducing a new error-UI language. TanStack Query's error state feeds
the same visual treatment the vanilla-JS version uses today.

## Migration order

1. Shell + design tokens + nav (pixel-check against the running vanilla-JS
   app side by side before going further).
2. Overview tab (includes the first Bklit chart: gap-hero).
3. Needs attention tab (filters, table, CSV export).
4. Insights tab (both remaining charts).
5. Activity log tab.
6. Chat panel.
7. Cutover: repoint `main.py`'s `StaticFiles` mount to `app/static/dist`,
   delete `app/static/index.html` and `app/static/vendor/d3.v7.9.0.min.js`.

Each numbered step ships a before/after screenshot comparison.

## Testing

Backend already has 109 pytest tests, untouched by this work. The current
frontend has zero tests. This migration adds Vitest + React Testing Library
component tests for the logic that isn't purely presentational: exception
filtering/sorting, funnel/gap math display, chat message rendering. No
end-to-end test suite — out of scope unless requested separately.

## Out of scope

- Any backend route, schema, or business-logic change.
- The payout sync path (`sync-logic-frozen`) — not touched, not read for
  this work beyond confirming it's a plain REST endpoint like the rest.
- Visual redesign — see Decision 1.
- Next.js / SSR — see Decision 2.
