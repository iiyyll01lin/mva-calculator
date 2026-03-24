# mva-v2

A modular release-candidate rewrite of the legacy StreamWeaver MVA workbook.  
Branch: `202603-rc4` — Legacy Parity Modernization, Phase 4 complete.

## What It Includes
- Typed project model for simulation, labor, overhead, equipment, space, products, and review state
- Browser application for editing project assumptions and generating release summaries
- Local persistence for project state and line-standard selection
- CSV and JSON import flows with structural validation and file-size guards
- JSON, CSV, and **multi-sheet XLSX** export flows with timestamped filenames
- Automated test matrix covering unit, functional, regression, and end-to-end smoke flows

## Architecture (`rc4`)

### `src/` Tree

```
src/
├── App.tsx                       196 ln  ← pure orchestrator (state, routing)
├── main.tsx
├── styles.css
│
├── components/                          ← shared/reusable UI primitives
│   ├── ErrorBoundary.tsx          45 ln
│   ├── KpiCard.tsx                14 ln
│   ├── SectionCard.tsx            23 ln
│   ├── Sidebar.tsx                85 ln
│   └── SummaryPage.tsx           284 ln  (Review Gate + XLSX export button)
│
├── features/                            ← full-page tab components (all React.memo)
│   ├── BasicInfoPage.tsx          71 ln  (production plan, shifts, OEE)
│   ├── BomMapPage.tsx             58 ln  (package-count-by-side table)
│   ├── DlohIdlPage.tsx           165 ln  (DLOH/IDL import, labor tables)
│   ├── LaborTimeL10Page.tsx      131 ln  (L10 station matrix + snapshots)
│   ├── LaborTimeL6Page.tsx       238 ln  (L6 segment + station matrix)
│   ├── MachineRatesPage.tsx       57 ln  (machine-group rate table)
│   ├── ModelProcessPage.tsx      129 ln  (editable routing with side badges)
│   ├── MpmL10Page.tsx            144 ln  (L10 product metadata + imports)
│   ├── MpmL6Page.tsx             140 ln  (L6 product metadata + routing)
│   ├── PlantEnvPage.tsx           75 ln  (overhead, space override)
│   ├── PlantEquipmentPage.tsx    291 ln  (equipment list, delta, line standards)
│   ├── PlantRatesPage.tsx        157 ln  (labor rates, volume, ICC)
│   ├── PlantSpacePage.tsx        202 ln  (space setup modes, allocation table)
│   └── SimulationPage.tsx        105 ln  (KPI cards, mountain chart)
│
├── hooks/
│   └── useProjectImports.ts      183 ln  (6 file-import handlers for Summary slot)
│
├── utils/
│   ├── fileImport.ts              19 ln  (assertFileSize, isRecord, readJsonRecord)
│   └── formatters.ts              23 ln  (numberValue, boundedNumber, toMoney, formatTimestamp)
│
├── domain/                              ← pure business logic, no React
│   ├── calculations.ts           617 ln  (simulation, MVA, projections)
│   ├── csv.ts                     59 ln  (parseCsv, toCsv)
│   ├── defaults.ts               320 ln  (defaultProject factory)
│   ├── exporters.ts              232 ln  (buildMvaXlsx, buildSummaryCsv, download helpers)
│   ├── importers.ts              413 ln  (all CSV/JSON parse + validate functions)
│   ├── models.ts                 471 ln  (ProjectState, all domain types)
│   └── persistence.ts            108 ln  (localStorage wrappers)
│
└── state/
    └── useProjectState.ts               (project state hook + localStorage sync)
```

### Component Hierarchy

| Layer | Purpose | Rule |
|---|---|---|
| `src/components/` | **Shared primitives** reused across multiple pages (cards, sidebar, error boundary, summary template) | No business logic; accept typed props only |
| `src/features/` | **Full-page tab components** — one per sidebar tab; own their local handlers and `useMemo` derivations | Never import from other `features/` files; receive `project` + `updateProject` from App |
| `src/hooks/` | **Logic hooks** that close over App-level state setters | Use `use*` naming; no JSX |
| `src/domain/` | **Pure functions** — calculations, CSV/XLSX builders, importers, model types | Zero React imports |

### Design Decisions

1. **No Context API** — `{ project, updateProject, onStatusMessage }` props passed one level deep is sufficient for this tree depth. Adding Context would introduce indirection without a cross-cutting sharing problem to solve.

2. **`React.memo` on every feature page** — Prevents cascade re-renders when App-level state changes. Each feature page only re-renders when its own props change.

3. **`useMemo` at point-of-use** — Derived values (e.g., `l6SegmentSnapshots`, `equipmentDelta`) are computed inside the relevant feature component, not hoisted to App. This keeps the derivation and the template that uses it co-located.

4. **`useProjectImports` hook** — The six async file-import handlers used by the SummaryPage `importsExportsSlot` are co-located in a single hook. This keeps App.tsx free of ~180 lines of async `try/catch` file-reading logic while avoiding a second component abstraction for what is pure logic.

5. **App.tsx as pure orchestrator** — App holds only: global state, shared `useMemo` chains (simulation → MVA → process variants), export closures, the `renderImportsExports` JSX factory (used in exactly two places), and the tab-routing JSX. At 196 lines it is readable end-to-end.

## SheetJS XLSX Export Pipeline

The multi-sheet Excel export is triggered from the **Summary (L10)** or **Summary (L6)** tab via the **"Export MVA XLSX"** button in `SummaryPage.tsx`.

### Sheets Generated

| Sheet name | Contents |
|---|---|
| `Summary L10` / `Summary L6` | Full legacy-aligned MVA summary: Header, Confirm (decision/reviewer/comment), Capacity Assumptions, Direct Labor rows + totals, Indirect Labor rows + totals, Overhead breakdown, SG&A breakdown, MVA Total/Unit, Simulation KPIs |
| `Cost Rollup` | Flat two-column table: Cost Category → Value/Unit for each `mva.costLines` entry, plus Total/Unit footer |
| `Simulation` | Step-by-step routing table: Step, Process, Side, Raw CT (s), Final CT (s), Utilization %, Bottleneck flag |

### Pipeline

```
SummaryPage "Export MVA XLSX" button
  └─▶ buildMvaXlsx(processLabel, project, mva, simulation)   [exporters.ts]
        ├─▶ buildMvaSummaryXlsxRows(...)  → Array of rows
        ├─▶ XLSX.utils.aoa_to_sheet(rows) → worksheet
        ├─▶ Cost Rollup sheet from mva.costLines
        ├─▶ Simulation sheet from simulation.steps
        └─▶ XLSX.write(workbook, { type: 'array', bookType: 'xlsx' })
              └─▶ downloadBytes(filename, Uint8Array, 'application/vnd.openxmlformats...')
                    └─▶ Blob → createObjectURL → <a download> click
```

Column widths are set per sheet. The `Summary` sheet uses 5 columns (A: Section 22 ch, B: Item 28 ch, C: Value 20 ch, D: Note/Unit 22 ch, E: Labor cost 16 ch).

### Adding New Sheet Types

Add a new `XLSX.utils.aoa_to_sheet(...)` block inside `buildMvaXlsx` in `src/domain/exporters.ts` and append it with `XLSX.utils.book_append_sheet(wb, sheet, 'Sheet Name')` before the `return` statement.

## Project Structure
- `src/domain` — calculation, CSV, export, defaults, persistence, and data models (zero React)
- `src/components` — shared UI building blocks (ErrorBoundary, SectionCard, KpiCard, Sidebar, SummaryPage)
- `src/features` — full-page tab components, one per sidebar entry (all `React.memo`)
- `src/hooks` — logic hooks extracting async file-import handlers from App
- `src/utils` — tiny shared formatters and file-import helpers
- `src/state` — project-state hook and persistence wiring
- `tests/unit` — pure-domain and import-validation tests
- `tests/functional` — jsdom UI workflow tests
- `tests/regression` — release baseline snapshot tests
- `tests/e2e` — served-app smoke test
- `docs` — legacy functionality specifications, gap review, parity notes, and smoke-test plan

## Run Locally
```bash
cd mva-v2
corepack pnpm install
corepack pnpm run dev
```

Open http://127.0.0.1:5173 for the Vite dev server.

To verify the production bundle locally:
```bash
cd mva-v2
corepack pnpm run build
corepack pnpm run preview
```

Then open http://127.0.0.1:4173

## Build
```bash
cd mva-v2
corepack pnpm run build
```

Production output is written to dist.

## Test
Run the full verification matrix:
```bash
cd mva-v2
corepack pnpm run test:all
```

Run individual suites:
```bash
corepack pnpm run test:unit
corepack pnpm run test:functional
corepack pnpm run test:regression
bash tests/e2e/smoke.sh
```

## Supported Import Formats
### Project JSON
- Export a project from the app and re-import it later
- Project payload must match the typed project structure

### Equipment CSV
```csv
id,process,item,qty,unitPrice,depreciationYears,costPerMonth
EQ-1,SMT,Placement line,1,540000,5,
```

### Space CSV
```csv
id,floor,process,areaSqft,ratePerSqft,monthlyCost
SP-1,F1,SMT,1800,8,
```

### Labor CSV
```csv
kind,id,name,process,department,role,headcount,allocationPercent,uphSource,overrideUph
direct,DL-1,Assembly,Assembly,,,15,,line,
indirect,IDL-1,TE,,Engineering,TE,8.36,1,line,
```

### Line Standards JSON
- JSON array of line standard objects
- Each line standard requires id, name, source, and equipmentList

## Release Notes
Current release-candidate scope preserves the core legacy workflow:
- simulation planning and bottleneck analysis
- labor and overhead costing
- equipment and space itemization
- equipment-to-simulation mapping with derived machine-rate preview
- L10 and L6 product metadata
- L6 segment-matrix labor estimation
- line-standard template reuse
- structured imports and exports

## Legacy Parity Notes
- Detailed parity refactor notes, logic changes, and manual verification steps are documented in [docs/parity-refactor-notes.zh-TW.md](docs/parity-refactor-notes.zh-TW.md).
- Remaining field-by-field differences versus the legacy HTML are tracked in [docs/legacy-gap-review.zh-TW.md](docs/legacy-gap-review.zh-TW.md).

## Known Next-Step Areas
- deeper schema validation beyond structural checks
- richer delete and edit controls for equipment and space lists
- more alternative regression scenarios beyond the default baseline
- optional API-backed persistence for collaboration and audit trails
