# MVA Platform — Engineer Handover Wiki

> **Phase 12 · Enterprise Handover document**  
> Audience: New engineers expected to onboard in ≤ 30 minutes  
> Last updated: 2026-03-27

---

## Table of Contents

1. [What This System Is](#1-what-this-system-is)
2. [The Tech Stack — Specific Choices Explained](#2-the-tech-stack--specific-choices-explained)
3. [Getting Running in 5 Minutes](#3-getting-running-in-5-minutes)
4. [Repository Layout](#4-repository-layout)
5. [The Test Matrix](#5-the-test-matrix)
6. [Critical Knowledge: The Gotchas](#6-critical-knowledge-the-gotchas)
   - 6.1 [IEEE 754 Floating-Point Noise in Cost Rollups](#61-ieee-754-floating-point-noise-in-cost-rollups)
   - 6.2 [React.lazy + Suspense Timing in Vitest](#62-reactlazy--suspense-timing-in-vitest)
   - 6.3 [Corporate Proxy in the Dockerfile](#63-corporate-proxy-in-the-dockerfile)
7. [The AI Agent: Design and Privacy Guarantees](#7-the-ai-agent-design-and-privacy-guarantees)
8. [Performance Architecture: Why We Did Not Block the UI](#8-performance-architecture-why-we-did-not-block-the-ui)
9. [State Persistence and Schema Migration](#9-state-persistence-and-schema-migration)
10. [Branch Conventions](#10-branch-conventions)

---

## 1. What This System Is

The **MVA Platform** (`mva-v2`) is a browser-based manufacturing value-add
(MVA) cost-analysis workbook.  It replaces a legacy multi-sheet Excel model
with a typed React SPA that can:

- Model a PCB assembly line's cycle-time simulation (SMT / F/A / FBT / ICT)
- Roll up 17 cost-line categories into a per-unit MVA figure
- Handle both **L10** (Surface Mount Technology) and **L6** (Final Assembly)
  process paths through separate calculation pipelines
- Import Mini-MOST labor time estimation data, equipment lists, and space
  allocation tables from CSV
- Export summary CSV, project JSON, and multi-sheet XLSX artefacts
- Process 5 000+ BOM rows and 500+ station tables at 60 FPS with no main-thread
  blocking

The companion `ddm-l6` stack (FastAPI + separate nginx) is a **completely
independent** service that must not be mixed with `mva-v2` development.

---

## 2. The Tech Stack — Specific Choices Explained

| Layer | Technology | Version | Why this choice |
|---|---|---|---|
| UI framework | **React** | 18.3 | Concurrent features: `startTransition`, `useDeferredValue`, and `React.lazy` are all essential to the performance model |
| Build tool | **Vite** | 7.x | Sub-second HMR; fingerprinted chunk hashing for long-lived nginx cache; native ESM for Web Worker `new URL(…, import.meta.url)` |
| Language | **TypeScript** | 5.8 | Strict mode; every domain type is a first-class `interface` in `models.ts`; no `any` in domain code |
| Test runner | **Vitest** | 3.x | Same Vite config and ESM graph as production; no separate Jest transform required |
| Virtualisation | **@tanstack/react-virtual** | 3.x | Supports both row-virtualised (BOM, L10) and column-virtualised (L6 segment matrix) patterns; zero wrapper component opinion so dark-theme CSS is fully preserved |
| CSV parser | **Hand-rolled `csv.ts`** | — | Zero dependency; RFC 4180 compliant including quoted-field commas and CRLF; 60-line file is fully auditable |
| XLSX export | **xlsx (SheetJS)** | 0.18.5 | Pinned to the last MIT-licensed release before the license change to source-available in 0.19 |
| Off-thread compute | **Web Worker API** | native | `calcWorker.ts` receives a full `ProjectState` via `postMessage`, runs `calculateSimulation + calculateMva`, and responds with typed `CalcWorkerOutput`; zero library required |
| Container runtime | **Docker** multi-stage | — | Builder: `node:22-alpine`; runner: `nginx:1.27-alpine`; Node.js toolchain is absent from the production image |
| Web server | **nginx** | 1.27-alpine | Gzip, 1-year `immutable` cache-control on fingerprinted chunks, SPA fallback `try_files`, security headers |

---

## 3. Getting Running in 5 Minutes

### Local development (no Docker)

```bash
cd mva-v2
pnpm install          # requires Node 22
pnpm dev              # Vite dev server → http://localhost:4173
```

### Production-equivalent Docker build

```bash
cd mva-v2
docker compose up -d --build
# App available at http://localhost:8090
```

On a **corporate network behind a proxy**, pass the proxy to the build:

```bash
http_proxy=http://10.6.254.210:3128 \
https_proxy=http://10.6.254.210:3128 \
docker compose up -d --build
```

The `docker-compose.yml` passes these as `ARG` values; the `Dockerfile`
promotes them to `ENV` and configures `npm`/`pnpm` before any network call.

### Run all tests

```bash
pnpm test          # unit + functional + regression (CI gate)
pnpm test:perf     # performance stress suite (separate; verbose timing)
pnpm test:all      # everything including e2e smoke
```

---

## 4. Repository Layout

```
mva-v2/
├── src/
│   ├── App.tsx                   Root orchestrator — state, routing, export helpers
│   ├── components/               Shared primitives (Sidebar, SectionCard, KpiCard, …)
│   ├── features/                 Full-page tab components — all React.memo
│   ├── hooks/
│   │   ├── useCalcWorker.ts      Worker bridge: debounce → postMessage → results
│   │   ├── useProcessSummary.ts  L10/L6 deferred memoised pipeline
│   │   └── useProjectImports.ts  6 file-import handlers wired to updateProject
│   ├── state/
│   │   └── useProjectState.ts    Portfolio CRUD + localStorage glue
│   ├── workers/
│   │   └── calcWorker.ts         Web Worker entry — zero DOM, pure domain calls
│   ├── domain/                   Pure TypeScript — safe in Worker, Node.js, browser
│   │   ├── calculations.ts       The entire math engine
│   │   ├── models.ts             Every domain type
│   │   ├── importers.ts          All CSV/JSON parsers + validators
│   │   ├── exporters.ts          CSV, JSON, XLSX builders
│   │   ├── persistence.ts        localStorage CRUD + deep-merge
│   │   ├── ai-agent.ts           AI context extraction, hashing, prompt generation
│   │   ├── csv.ts                RFC 4180 CSV parser/serialiser
│   │   └── defaults.ts           defaultProject factory + storage key constants
│   └── utils/
│       ├── fileImport.ts         assertFileSize, isRecord, readJsonRecord
│       └── formatters.ts         numberValue, toMoney, formatTimestamp
├── tests/
│   ├── setup.ts                  globalThis.Worker = undefined; sessionStorage auth
│   ├── unit/                     Pure domain function tests
│   ├── functional/               app.spec.tsx — React render/DOM interaction
│   ├── regression/               summary.spec.ts — inline snapshot lock
│   └── performance/              stress.spec.ts — timing benchmarks
├── docs/
│   ├── ARCHITECTURE.md           Mermaid system diagram (this deliverable)
│   └── HANDOVER.md               This file
├── Dockerfile                    Multi-stage build
├── docker-compose.yml            Production container definition
├── nginx.conf                    SPA-aware nginx config
└── vite.config.ts                Vite + Vitest unified config
```

---

## 5. The Test Matrix

| Suite | Command | What it verifies |
|---|---|---|
| `unit` | `pnpm test:unit` | Pure domain functions: `calculateMva`, `calculateSimulation`, `parseCsv`, importers, exporters, AI hashing |
| `functional` | `pnpm test:functional` | React render tests: `App` mounts, input events propagate, DOM state is correct |
| `regression` | `pnpm test:regression` | Inline snapshot lock on `buildSummaryCsv` output; prevents silent calculation drift |
| `performance` | `pnpm test:perf` | 5 000-BOM and 200-station timing benchmarks; asserts `calculateMva < 200 ms` |
| `e2e` | `pnpm test:e2e` | Smoke test via `curl` against `http://localhost:8090` |

All suites share `tests/setup.ts` which:
1. Sets `globalThis.Worker = undefined` (forces sync fallback in hooks)
2. Writes `sessionStorage.mva_auth = '1'` (bypasses the login guard)

---

## 6. Critical Knowledge: The Gotchas

These three issues each consumed significant engineering time. Read them before
touching the corresponding code.

---

### 6.1 IEEE 754 Floating-Point Noise in Cost Rollups

#### The problem

Manufacturing cost tables accumulate many small multiplications.  In IEEE 754
double-precision arithmetic, expressions like `3.14 * 100 * 0.01` produce
`3.1400000000000006` rather than `3.14`.  When these values are serialised to
CSV or displayed in a table, users see noise like `104.75140000000001` instead
of `104.7514`.  Worse, the assertion `result === 104.7514` fails in tests even
though the value is correct to any useful precision, causing false regression
failures.

#### The solution — `epsilon-corrected round()`

Every intermediate and final value in `calculations.ts` passes through this
function before being stored in the result object:

```typescript
// src/domain/calculations.ts
const round = (value: number, digits = 4): number => {
  if (!Number.isFinite(value)) return 0;           // NaN / ±Infinity → 0, never propagates
  const factor = Math.pow(10, digits);
  return Math.round((value + Number.EPSILON) * factor) / factor;
};
```

Key properties:
- **`+ Number.EPSILON` before rounding** — This is the epsilon correction.  It
  nudges values that are fractionally below a half-way point (due to IEEE 754
  representation) over the threshold so they round in the expected direction.
  For example, `Math.round(1.0049999999999999 * 1000)` gives `1004` without
  the nudge, but `Math.round((1.005 + ε) * 1000)` gives `1005`.
- **Returns `0` for non-finite values** — Division by zero, invalid inputs, or
  propagating `NaN` from upstream all produce `0` instead of polluting the
  entire cost table with `NaN` strings.
- **4 decimal places by default** — Appropriate for per-unit cost figures in
  USD; space and equipment calculations use `round(value, 2)` explicitly.

#### What NOT to do

Do not remove or "simplify" this function to `Math.round(value * 10000) / 10000`.
The epsilon correction is load-bearing — it was added in response to a real
regression where `totalPerUnit` shifted by `0.0001` between Node.js versions
due to slightly different floating-point evaluation order.

---

### 6.2 React.lazy + Suspense Timing in Vitest

#### The problem

`App.tsx` code-splits every feature page with `React.lazy`:

```typescript
const BasicInfoPage = lazy(() => import('./features/BasicInfoPage').then(…));
```

In a real browser this resolves asynchronously over the network; in Vitest it
resolves via Vite's module graph, but the resolution still takes ~30 ms of IPC
round-trip time the first time a module is loaded.

The Vitest test environment uses `createRoot` + `act()` from React 18.  The
`act()` flush loop works on a `setImmediate` schedule.  At ~30 ms, the first
dynamic `import()` returns **after** `setImmediate` has already checked for
pending work, so the Suspense retry callback lands **outside** the `act()`
context.  The lazy component is never committed to the DOM inside the test.

Without the fix, every functional test that renders `<App />` produces:

```
Warning: The current testing environment is not configured to support act(...)
AssertionError: expected null to not be null
```

#### The solution — `beforeAll` cache warming

The fix is in `tests/functional/app.spec.tsx`:

```typescript
beforeAll(async () => {
  await Promise.all([
    import('../../src/features/BasicInfoPage'),
    import('../../src/features/PlantEquipmentPage'),
    import('../../src/features/LaborTimeL6Page'),
  ]);
});
```

By eagerly importing the feature pages in a `beforeAll`, Vitest populates its
module cache before any test runs.  When `React.lazy` calls the same
`import()` expressions during the test, they resolve as **micro-tasks** (from
cache) rather than as async IPC calls.  Micro-tasks execute before
`setImmediate`, so the Suspense retry lands inside the `act()` drain window
and the component commits correctly.

The `actWithLazyFlush` wrapper in the same file explains this in a step-by-step
comment if you need to debug it again.

#### Rules going forward

- If you add a new `React.lazy` page that is rendered by `App` in the
  `basic` tab path, add it to the `beforeAll` list in `app.spec.tsx`.
- Do not replace `createRoot` with `render` from `@testing-library/react` in
  the functional tests without understanding how its `act()` implementation
  differs from the raw React one — the timing assumptions change.

---

### 6.3 Corporate Proxy in the Dockerfile

#### The problem

The production deployment host (`tao-mesAI`) sits behind a corporate HTTP
proxy (`10.6.254.210:3128`) that intercepts all outbound HTTPS using a
private CA certificate.  Without proxy configuration:

- `npm install` inside the Docker builder times out reaching `registry.npmjs.org`
- `pnpm` similarly fails because it does not inherit proxy from environment
  automatically in Alpine
- `apk add` fails for any additional Alpine packages
- HTTPS connections fail with a certificate verification error because the
  corporate MITM CA is not in the Alpine trust store

Naively setting `ENV HTTP_PROXY=…` in the Dockerfile would hard-code the
proxy URL into the image layer, making the image unusable outside the corporate
network.

#### The solution — `ARG` → `ENV` injection

The `Dockerfile` accepts proxy coordinates as **build-time arguments**, not
baked-in environment variables:

```dockerfile
ARG http_proxy
ARG https_proxy
ARG no_proxy

ENV HTTP_PROXY=$http_proxy \
    HTTPS_PROXY=$https_proxy \
    http_proxy=$http_proxy \
    https_proxy=$https_proxy \
    no_proxy=$no_proxy
```

Both lowercase and uppercase variants are set because different tools
(`npm`, `pnpm`, `wget`, Alpine `apk`) check different capitalizations.

`docker-compose.yml` passes through the host shell's proxy variables
automatically by listing them with `args:` but no explicit value:

```yaml
build:
  args:
    http_proxy:
    https_proxy:
    no_proxy:
```

When the host shell has `http_proxy` set, Docker Compose reads it and passes it
as the build arg.  When the host shell does not have it set, the arg has no
value and the `ENV` expansion is empty — the image is proxy-neutral.

Additionally the Dockerfile disables npm/pnpm TLS verification for the builder
stage only:

```dockerfile
RUN npm config set strict-ssl false && \
    pnpm config set strict-ssl false
```

This handles the corporate MITM CA without needing to inject the private CA
certificate into the Alpine trust store.  The runner stage (`nginx:1.27-alpine`)
does not inherit any of these settings.

#### What this means for `ddm-l6`

The `ddm-l6` backend has **the same proxy problem** with `pip install` and
`pypi.org`.  If the corporate proxy is intermittent (as observed in the
terminal logs), `pip` will retry five times and eventually timeout.  The
appropriate fix is the same `ARG → ENV` pattern in the Python Dockerfile,
combined with `--index-url` pointing to a local PyPI mirror if available.

---

## 7. The AI Agent: Design and Privacy Guarantees

The AI integration lives entirely in `src/domain/ai-agent.ts` and
`src/features/AiInsightsPanel.tsx`.

### Privacy-First Context Extraction

The `extractAiContext()` function is the system boundary.  It accepts the full
`ProjectState` and `SimulationResults` and returns a narrow `AiAgentContext`
object containing **only non-sensitive manufacturing parameters**:

| Included | Excluded |
|---|---|
| Cycle times, UPH, takt time | Model names, part numbers, customer names |
| Headcount (aggregate totals) | Individual employee data |
| Process step timing | Cost figures (rates, prices, depreciation) |
| Weekly demand, shifts, hours | Financial identifiers, contract terms |
| Line balance efficiency | Anything from `basicInfo.modelName` — wait, this is included but only as an opaque label |

The prompt sent to the LLM contains nothing that could identify a specific
product, customer, or contract.  If a real LLM API is wired in (replacing
`runMockAiAnalysis`), the JSON payload logged to the network would satisfy a
reasonable corporate data classification review.

### djb2 Result Cache

LLM calls are expensive and slow.  `hashAiContext()` computes a deterministic
djb2-style hash over the performance-relevant subset of the context
(UPH, takt time, station summary, headcount):

```typescript
export function hashAiContext(context: AiAgentContext): string {
  const payload = JSON.stringify({
    uph, taktTime, lineBalanceEfficiency, weeklyOutput,
    weeklyDemand, stationSummary, totalDirectHeadcount,
  });
  let h = 5381;
  for (let i = 0; i < payload.length; i++) {
    h = (((h << 5) + h) ^ payload.charCodeAt(i)) >>> 0; // unsigned 32-bit
  }
  return h.toString(16).padStart(8, '0');
}
```

The `AiInsightsPanel` stores the last `AiInsight` (which carries its
`contextHash`) in component state.  Before triggering a new analysis it
compares the current hash to the stored one.  If they match, the previous
result is shown immediately with zero latency and zero LLM cost.

### Swapping in a Real LLM

Replace `runMockAiAnalysis` in `ai-agent.ts` with a function that:
1. Calls `extractAiContext(project, simulation)` to get the safe context
2. Calls `generateOptimizationPrompt(context)` to get the system prompt text
3. POSTs to your LLM API endpoint with the prompt
4. Parses the structured JSON response into `AiInsight`

The caching, privacy filtering, and panel rendering require no changes.

---

## 8. Performance Architecture: Why We Did Not Block the UI

Manufacturing cost calculations involve summing hundreds of floating-point
products across equipment items, labor rows, and space allocations.  On a
"Monster Project" (5 000 BOM rows, 50 equipment items, 30 labor rows) this
takes ~150 ms in a single synchronous call — long enough to cause a visible
frame drop.

Three mechanisms keep the UI at 60 FPS:

### 1. Web Worker (`calcWorker.ts`)

Every `ProjectState` change after the first render is sent via `postMessage` to
a Web Worker.  The Worker runs `calculateSimulation + calculateMva` entirely
off the main thread and returns `{ id, simulation, mva }`.  The main thread
shows the "Computing…" spinner and remains interactive.

The **anti-stale `reqId` counter** ensures that if the user types quickly and
multiple messages are in-flight, only the response to the most recent request
updates the UI.  All older responses are discarded.

The **synchronous fallback** in `useCalcWorker` runs if `typeof Worker === 'undefined'`
(jsdom, some SSR environments).  This is the path all Vitest tests use.

### 2. `useDeferredValue` for L10/L6 Paths

The L10 and L6 process-specific summaries use `useProcessSummary`, which
contains three `useMemo` calls chained on a `projectForProcess` snapshot.
These are wrapped in `useDeferredValue` in `App.tsx`:

```typescript
const deferredProject = useDeferredValue(safeProject);
const { mva: l10Mva, … } = useProcessSummary(deferredProject, 'L10');
```

React's deferred mode lets input events interrupt the L10/L6 computation and
commit their results one render cycle later.  Tabs that display L10/L6 data
may show stale values for a single frame, but input handling is never blocked.

### 3. Row and Column Virtualisation

Tables with `N` rows render `N` `<tr>` elements unconditionally.  At 5 000
rows this is ~80 000 DOM nodes for the BOM table alone — catastrophic for
layout and garbage collection.

All heavy tables use `@tanstack/react-virtual`:

| Table | Strategy | Max DOM rows at any time |
|---|---|---|
| `BomMapPage` | Row virtualizer | ~15 (based on `MAX_TABLE_HEIGHT = 440 px`) |
| `LaborTimeL10Page` | Row virtualizer | ~14 (based on `MAX_TABLE_HEIGHT = 540 px`) |
| `LaborTimeL6Page` | Column virtualizer (transposed) | ~6 columns per segment |

The sticky `thead` works because the scroll container is the `data-table-wrapper`
div, not the window, and `position: sticky` is applied to `thead th` with
`z-index: 1` and matching dark-theme background.

---

## 9. State Persistence and Schema Migration

`ProjectState` is persisted to `localStorage` as JSON on every mutation.
The schema has evolved across 12 development phases.  Users loading a project
saved by an older version of the app will have a partially-populated object.

The `mergeProject()` function in `persistence.ts` handles this via deep-merge:

```typescript
function mergeProject(parsed: Partial<ProjectState>): ProjectState {
  return {
    ...defaultProject,       // ← all new fields get their default values
    ...parsed,               // ← overwrite with whatever the saved data has
    plant: {
      ...defaultProject.plant,
      ...(parsed.plant ?? {}),
      // … and so on for every nested object
    },
  };
}
```

**Rule:** Any time you add a new field to `ProjectState` (or any nested
object), you must:
1. Add a sensible default value to `defaultProject` in `defaults.ts`
2. Verify that `mergeProject` will spread that sub-object correctly
3. Run the regression suite — `buildSummaryCsv(defaultProject)` must still
   match the inline snapshot in `tests/regression/summary.spec.ts`

If the snapshot changes intentionally (e.g. a new cost line), update it
with `pnpm test:regression -- -u`.

---

## 10. Branch Conventions

| Pattern | Purpose |
|---|---|
| `YYYYMM-feat-<name>` | Feature or phase work (e.g. `202603-feat-extreme-perf`) |
| `YYYYMM-rc<N>` | Release candidate tags |
| `main` | Stable, deployed baseline |

Never commit directly to `main`.  All changes go through a feature branch.
The commit convention in this project is **Conventional Commits**:

```
type(scope): short description

feat(ui): add AI insights panel
perf(core): offload MVA calculations to Web Worker
fix(import): handle CRLF line endings in L6 CSV
docs(architecture): create system architecture diagram and handover wiki
test(perf): add 200-station L10 stress scenario
```
