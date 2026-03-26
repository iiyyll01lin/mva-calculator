# Changelog

All notable changes to the MVA Platform are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-03-27

> **Enterprise release.** Phases 1–12 of the MVA Platform are merged into `main`.  
> This release delivers a fully containerised, AI-augmented, high-performance  
> manufacturing value analysis tool with formal user identity, a scalable  
> component architecture, and a comprehensive automated test suite.

### 🚀 Features

- **AI Optimization Agent** — Integrated an automated bottleneck-analysis agent
  (`feat(ai)`) that ingests the full MVA calculation context and surfaces
  actionable insights; includes a hardened `AiAgentContext` type-guard and
  an interactive `AiInsightsPanel` sidebar section.
- **Resizable Sidebar** — Drag-to-resize sidebar with persistence, brand
  typography polish, and smooth collapse / expand transitions.
- **Formal User Identity** — User profile (Avery Yeh) pinned to the sidebar
  footer; nav area made independently scrollable so profile never overlaps
  the navigation links.
- **Login Guard** — Basic authentication gate (`feat(auth)`) prevents
  unauthenticated access to the analysis workspace.
- **Product / SKU Dashboard** — Multi-project state management with a
  dedicated product and SKU portfolio dashboard; supports parallel project
  comparison (`feat(portfolio)`).
- **XLSX Multi-Sheet Export** — Full SheetJS-backed template exporter writes
  structured cost data across multiple named sheets, with correct
  `Uint8Array → ArrayBuffer` Blob handling.
- **Typed Status Banner** — Severity-classified status messages (`info`,
  `warning`, `error`) with auto-clear after 6 seconds.
- **L6 Multi-Segment CSV Importer** — Recognises and parses multi-segment
  L6 process CSV files into the domain model.
- **Legacy Parity** — Full workflow hierarchy restoration and parity pass
  across plant setup, labour pages, segment matrix mapping, and dark-theme
  styling (`feat: restore legacy workflow hierarchy` et al.).
- **Summary Page State Machine** — Space-setup state machine and MPM L10
  field ordering brought into full parity with the legacy reference tool.

### ⚡ Performance

- **Web Worker MVA Engine** — Moved the entire MVA rollup computation off the
  main thread into a dedicated Web Worker with a 150 ms debounce
  (`perf(core)`).  UI thread stays responsive even for 5 000-BOM projects.
- **`useDeferredValue` Orchestration** — Wired `useCalcWorker` +
  `useDeferredValue` into the app orchestrator so renders are non-blocking
  during heavy recalculation.
- **TanStack Virtual — L10 Station Table** — Station rows virtualised with
  `@tanstack/react-virtual`; verified against a 200-station stress scenario.
- **TanStack Virtual — L6 Segment Matrix** — Column virtualisation with
  atomic memoisation applied to the L6 segment matrix for constant-time
  render regardless of column count.
- **BomMapPage Row Virtualisation** — BOM mapping table rows rendered via
  virtual window, eliminating DOM node explosion for large bills-of-materials.
- **`@tanstack/react-virtual` dependency** — Added as a direct build
  dependency with tree-shakeable imports.

### 🛠 Infrastructure & CI/CD

- **DDM-L6 FastAPI Backend Containerisation** — Production-grade multi-stage
  `Dockerfile` for the FastAPI backend; non-root `appgroup/appuser` runtime;
  `WORKDIR=/app/backend` aligned with `DATA_DIR` bind-mount.
- **Nginx Reverse Proxy** — Multi-stage Nginx deployment configuration routes
  `/api/` traffic to the FastAPI container; explicit `ddm-net` bridge with
  pinned `172.28.10.0/24` IPAM subnet.
- **Ephemeral CI Tools — mva-v2** — `compose profile: tools` pattern provides
  one-shot Dockerised CLI tooling (linting, testing, type-checking) without
  polluting the production service graph.
- **Ephemeral CI Tools — ddm-l6** — Equivalent ephemeral tools profile added
  for the DDM-L6 sub-project.
- **Corporate Proxy Support** — `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`
  build-args propagated through `docker-compose.yml` → `Dockerfile` for
  air-gapped / firewall-restricted build environments.
- **Docker Compose hygiene** — Removed obsolete `version` key; updated host
  port bindings (`8090` for mva-v2, `8010` for ddm-l6 backend).

### 📚 Documentation

- **Mermaid System Architecture Diagram** — `docs/ARCHITECTURE.md` contains a
  full Mermaid C4-style architecture diagram covering the React SPA, Web
  Worker engine, FastAPI backend, Nginx proxy, and Docker Compose topology.
- **Enterprise Handover Wiki** — `docs/HANDOVER.md` provides a complete
  project handover guide: technology decisions, component ownership map,
  known limitations, and onboarding checklist for successor engineers.
- **AI Agent Deep-Dive** — `docs/ai-agent-deep-dive.zh-TW.md` documents the
  AI Optimization Agent design, prompt engineering, and extension points (zh-TW).
- **DEVELOPER_GUIDE.md** — Root-level developer guide covering Docker-based
  dev environment setup, Compose profiles, and Dockerised CLI tool usage.
- **Domain JSDoc** — Inline JSDoc added for `projectForProcess`,
  `resolveIndirectLaborRows`, `computeEquipmentDelta`, and
  `deriveMachineRatesFromEquipmentLinks`.
- **Parity & Gap Notes** — Progressive documentation of Phase 2 parity
  changes, remaining legacy gaps, and smoke-test validation results.

### 🔧 Refactoring

- **App.tsx 731 → 196 lines** — Converted to a pure orchestrator; all
  business logic and page renders extracted into dedicated feature modules.
- **11 Feature Pages Extracted** — `BasicInfoPage`, `BomMapPage`,
  `DlohIdlPage`, `LaborTimeL10Page`, `LaborTimeL6Page`, `MachineRatesPage`,
  `ModelProcessPage`, `MpmL10Page`, `MpmL6Page`, `PlantEnvPage`,
  `PlantEquipmentPage`, `PlantRatesPage`, `PlantSpacePage`,
  `ProjectDashboardPage`, `SimulationPage` all live in `src/features/`.
- **`useProcessSummary` Hook** — Encapsulates the full L10/L6 derivation
  chain; downstream cascade guaranteed by a single reactive hook.
- **`SummaryPage` Component** — Extracted with `ErrorBoundary` wrapper.
- **Domain Layer Extension** — Shared utility functions, type helpers, and
  calculation primitives moved to `src/domain/`; IEEE 754 round-off fixed
  with epsilon-correct `round()`.

### 🧪 Tests

- **Monster Project Stress Suite** — 5 000-row BOM × 50 equipment items;
  confirms sub-second recalculation via Web Worker path.
- **200-Station L10 Stress Scenario** — End-to-end virtualisation stress test
  for the L10 station table.
- **Comprehensive Unit Coverage** — Edge-case matrix for all domain
  calculation functions including zero-value, boundary, and overflow paths.
- **Regression Snapshots** — L6/L10 process-path summaries locked as
  golden-file regression tests.
- **Functional Suite** — Downstream cascade and full page-render smoke tests
  using concurrent-mode React; `React.lazy` Suspense flush timing fixed for
  `jsdom` environment; `globalThis.Worker` stub hardened.

### 🐛 Bug Fixes

- Fixed `ctx.generatedFor` reference that was absent from `AiAgentContext`
  type; hardened `Record` cast in the AI agent pipeline.
- Fixed sidebar resizer logic; layout synchronisation between CSS custom
  property and React state.
- Fixed `mva.overhead.totalOverheadPerUnit` path in XLSX summary sheet export.
- Fixed `Uint8Array` buffer → `ArrayBuffer` cast for `Blob` construction in
  the XLSX exporter.
- Fixed IEEE 754 half-up drift in `round()` via epsilon correction.
- Improved CSV/JSON import error messages to be human-readable.
- Human-readable `ErrorBoundary` messages for the four most common React
  failure modes.

---

## [1.0.0] - 2026-03-13

### 🏁 Initial Release — Legacy Baseline Capture

- `chore: capture legacy baseline and specifications` — Committed the original
  single-file DDM-L6 HTML tools, legacy functional specifications (EN + zh-TW),
  reference CSV datasets, backend FastAPI source, and MiniMOST pipeline design
  docs as the authoritative starting baseline for the v2 rewrite.
