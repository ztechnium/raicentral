# Reports & Analytics: visual QA record

Captured 2026-11-18 with Playwright driving Chrome against `next dev`, with the
`/v1/*` API **stubbed** (fixtures in the capture script, not a real backend).
The UI, routing, filters and rendering are real; the numbers are synthetic.

| Capture | Viewport | Horizontal overflow | Page errors |
|---|---|---|---|
| `desktop-{executive,risk,controls,assurance,runtime,economics}.png` | 1440×900 | none | 0 |
| `mobile-{executive,risk,controls,assurance,runtime,economics}.png` | 390×844 | none | 0 |
| `desktop-executive-partial-failure.png` | 1440×900 | none | 0 |
| `desktop-not-authorized.png` | 1440×900 | none | 0 |

Raw per-capture data: `record.json`.

## What the captures confirm

* Every KPI shows its definition; ratios show their denominator (`3 of 8 (37.5%)`).
* **Partial failure** (summary endpoint returns 500): the four KPIs backed by it
  read "Unavailable", the attention panel shows the error with a trace id and
  *Try again*, while spend, top systems and the trend still render.
* **Not authorized** (no `reports:read`): an explanatory panel, no toolbar, no requests.
* Assurance renders real metrics when data exists (no blur).
* Control Effectiveness shows matched effect, final decision and execution
  outcome as separate panels and explains that rates are not effectiveness.

## Defect found and fixed during QA

`mobile-executive` first overflowed horizontally (content 630px in a 390px
viewport). Cause: the `sr-only` column header inside the attention table is
absolutely positioned and escaped its scroll container. Fixed by making the
scroll wrappers `relative`. Re-captured: no overflow on any view.

## Not covered

Real-backend data, screen-reader testing with assistive software, other
browsers, and light theme (the app rendered in its dark theme).
