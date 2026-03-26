# MVA Platform — System Architecture

> **Phase 12 · Enterprise Handover document**  
> Last updated: 2026-03-27  
> Branch: `202603-feat-extreme-perf`

---

## High-Level Data Lifecycle

The diagram below traces a piece of data from the moment a user types a value
into the UI all the way into a Docker-served nginx response, including the
off-main-thread worker path, the virtualized rendering layer, the AI agent
pipeline, and the separate `ddm-l6` infrastructure stack.

```mermaid
graph TD
    subgraph FRONTEND["Frontend — React 18 SPA (mva-v2)"]

        subgraph UI_LAYER["UI Components  (React.lazy · code-split · React.memo)"]
            USER(["User"])
            SIDEBAR["Sidebar\n14 tabs"]
            FEATURE_PAGES["Feature Pages\nall React.memo + useCallback\nBasicInfo · BomMap · Simulation\nEquipment · Space · Labor …"]
            AI_BTN["AI Insights\nbutton"]
        end

        subgraph STATE_LAYER["State Management"]
            PORTFOLIO["usePortfolioState\nportfolio: ProjectState[]"]
            UPDATE["updateProject\nstartTransition wrapper"]
            DIRTY["isDirty flag\nunsaved-changes badge"]
        end

        subgraph PERF_LAYER["Performance Layer"]
            CALC_HOOK["useCalcWorker\n150 ms debounce\nanti-stale reqId counter"]
            WORKER[["calcWorker.ts\nWeb Worker\noff main thread"]]
            DEFERRED["useDeferredValue\nuseProcessSummary\nL10 path · L6 path"]
            SPINNER["Computing… spinner\nisComputing state"]
            BOM_V["BomMapPage\nrow-virtualizer"]
            L10_V["LaborTimeL10Page\nrow-virtualizer"]
            L6_V["LaborTimeL6Page\ncol-virtualizer\nSegmentMatrix deep-memo"]
        end

        AI_PANEL["AiInsightsPanel\nSlide-in drawer\nMarkdown renderer"]
        EXPORT_NODE(["Export out\nJSON · CSV · XLSX"])
        IMPORT_NODE(["File import in\nCSV · JSON"])
    end

    subgraph DOMAIN["Domain Core — Pure TypeScript  (zero DOM dependency)"]
        CALCS["calculations.ts\ncalculateSimulation\ncalculateMva\nround — ε-corrected"]
        MODELS_TS["models.ts\nProjectState · MvaResults\nSimulationResults · BomRow\nL10Station · L6Station …"]
        IMPORTERS["importers.ts\nCSV and JSON parse\nstructural validation\nfile-size guard"]
        EXPORTERS["exporters.ts\nbuildSummaryCsv\nbuildProjectExport\nbuildMvaXlsx"]
        PERSIST["persistence.ts\nloadPortfolio\nsavePortfolio\ndeep-merge migration"]
        AI_SVC["ai-agent.ts\nextractAiContext — PII filter\nhashAiContext — djb2\ngenerateOptimizationPrompt\nrunMockAiAnalysis"]
        CSV_SVC["csv.ts\nparseCsv — RFC 4180\ntoCsv"]
    end

    subgraph STORAGE["Browser Storage"]
        LS[("localStorage\nportfolio JSON array\nactive-project-id\nline-standard-selection")]
    end

    subgraph INFRA["Infrastructure"]

        subgraph MVA_DOCKER["mva-v2 — Docker  (host :8090  →  container :80)"]
            PROXY_ARG["Corporate Proxy\nBuildArg → ENV\nHTTP_PROXY / HTTPS_PROXY\nstrict-ssl = false"]
            DOCKERFILE["Dockerfile multi-stage\nstage-1  node:22-alpine\n  pnpm install + vite build\nstage-2  nginx:1.27-alpine\n  copy dist → html root"]
            NGINX_CONF["nginx.conf\ngzip on · comp_level 6\nSPA fallback try_files\n1-year immutable cache\nX-Frame-Options · nosniff"]
            COMPOSE["docker-compose.yml\nrestart: unless-stopped\nhealthcheck: wget /\nTZ: UTC"]
        end

        subgraph DDM_DOCKER["ddm-l6 — Separate Stack"]
            FASTAPI["FastAPI\nPython 3.11-slim\nnon-root uid:gid 1001"]
            DDM_NGINX["nginx\nStatic HTML frontend\nddm_p1_full.html …"]
            PYTEST_SVC["pytest\ntests_most/"]
        end
    end

    USER --> SIDEBAR
    USER --> IMPORT_NODE
    SIDEBAR -->|setActiveTab| FEATURE_PAGES
    FEATURE_PAGES --> UPDATE
    UPDATE --> PORTFOLIO
    PORTFOLIO --> DIRTY
    PORTFOLIO --> PERSIST
    PERSIST <-->|read/write JSON| LS

    PORTFOLIO --> CALC_HOOK
    CALC_HOOK -->|"postMessage {id, project}"| WORKER
    WORKER -->|calculateSimulation| CALCS
    WORKER -->|calculateMva| CALCS
    CALCS -.->|typed by| MODELS_TS
    WORKER -->|"postMessage {id, sim, mva}"| CALC_HOOK
    CALC_HOOK --> SPINNER

    PORTFOLIO --> DEFERRED
    DEFERRED -->|useMemo chain| CALCS

    PORTFOLIO --> BOM_V
    PORTFOLIO --> L10_V
    PORTFOLIO --> L6_V

    IMPORT_NODE --> IMPORTERS
    IMPORTERS --> CSV_SVC
    IMPORTERS --> UPDATE

    PORTFOLIO --> EXPORTERS
    EXPORTERS --> EXPORT_NODE

    AI_BTN --> AI_PANEL
    AI_PANEL --> AI_SVC
    AI_SVC -->|extractAiContext| CALCS

    PROXY_ARG --> DOCKERFILE
    DOCKERFILE --> NGINX_CONF
    COMPOSE --> DOCKERFILE
    NGINX_CONF -->|"serve /usr/share/nginx/html"| FRONTEND
```

---

## Subgraph Descriptions

### Frontend — React 18 SPA

| Concern | Implementation |
|---|---|
| Routing | Client-side tab state (`activeTab: TabId`), no React Router |
| Code splitting | `React.lazy` per feature page; only the active tab is ever parsed |
| Re-render isolation | All feature pages are `React.memo`; mutation handlers are `useCallback` with minimal dep arrays |
| State transitions | `updateProject()` is always wrapped in `startTransition()` so input events are never blocked by re-renders |
| Suspense fallback | `<Suspense>` wrapping the entire feature area; lazy pages show "Loading…" spinner on first visit |

### Domain Core

All business logic lives in plain TypeScript files with **zero DOM or React
imports**.  This makes them testable in Node.js (Vitest), runnable inside a Web
Worker, and safe to server-render if needed.

| File | Responsibility |
|---|---|
| `calculations.ts` | `calculateSimulation` (cycle-time bottleneck model), `calculateMva` (17-line cost rollup), all helper math |
| `models.ts` | Every domain type as TypeScript `interface` / `type`; single source of truth |
| `importers.ts` | All CSV and JSON parsers, structural validators, file-size guard (`assertFileSize`) |
| `exporters.ts` | `buildSummaryCsv`, `buildProjectExport` (JSON), `buildMvaXlsx` (multi-sheet) |
| `persistence.ts` | `loadPortfolio` / `savePortfolio` with deep-merge so new fields populate safely after schema changes |
| `csv.ts` | Hand-rolled RFC 4180–compliant CSV parser; no third-party dependency |
| `ai-agent.ts` | Context extraction → djb2 hash → prompt generation → mock LLM response; fully swappable to a real LLM |

### Performance Layer

| Mechanism | Benefit |
|---|---|
| Web Worker (`calcWorker.ts`) | `calculateSimulation + calculateMva` run off the main thread; UI stays at 60 FPS during heavy computation |
| 150 ms debounce in `useCalcWorker` | Rapid keystrokes are batched into a single worker call; intermediate values are never sent |
| Anti-stale `reqId` counter | Responses from superseded requests are silently discarded; no stale-result flash |
| `useDeferredValue` for L10/L6 summaries | These deferred paths accept one render-cycle lag; they never block the primary input flow |
| `@tanstack/react-virtual` | Row-virtualised BOM table (5 000+ rows) and L10 station table (500+ rows); column-virtualised L6 segment matrix (200+ stations) |

### Infrastructure

```text
host :8090  →  Docker :80  →  nginx  →  /usr/share/nginx/html  (Vite dist)
```

The Dockerfile uses a strict two-stage build:

1. **Builder** (`node:22-alpine`) — installs pnpm, runs `pnpm install --frozen-lockfile`,
   runs `pnpm run build` (TypeScript + Vite bundle).
2. **Runner** (`nginx:1.27-alpine`) — copies only the compiled `dist/` directory;
   the Node.js toolchain is not present in the production image.

Corporate proxy coordinates are injected as Docker `ARG` values and
immediately promoted to `ENV` so that `npm`, `pnpm`, `apk`, and `wget` all
pick them up during the builder stage without being baked in permanently.
