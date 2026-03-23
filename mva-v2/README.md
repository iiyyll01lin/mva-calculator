# mva-v2

A modular release-candidate rewrite of the legacy StreamWeaver MVA workbook.

## What It Includes
- Typed project model for simulation, labor, overhead, equipment, space, products, and review state
- Browser application for editing project assumptions and generating release summaries
- Local persistence for project state and line-standard selection
- CSV and JSON import flows with structural validation and file-size guards
- JSON and CSV export flows with timestamped filenames
- Automated test matrix covering unit, functional, regression, and end-to-end smoke flows

## Project Structure
- src/domain: calculation, CSV, export, defaults, persistence, and data models
- src/components: shared UI building blocks
- src/state: project-state hook and persistence wiring
- tests/unit: pure-domain and import-validation tests
- tests/functional: jsdom UI workflow tests
- tests/regression: release baseline snapshot tests
- tests/e2e: served-app smoke test
- docs: legacy functionality specifications in English and Traditional Chinese

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
