# StreamWeaver MVA Legacy Functional Specification

## Document Control
- Version: 1.0
- Date: 2026-03-13
- Source baseline: ddm-l6/l6-ddm-demo.html and related ddm-l6 assets
- Audience: software engineers, QA engineers, solution architects
- Purpose: capture the current legacy behavior before refactoring into mva-v2

## 1. Product Summary
The legacy application is a browser-only manufacturing planning and cost analysis tool implemented as a single HTML file with inline React and inline business logic. It combines two related domains in one runtime:

1. Line simulation for L6 manufacturing flow
2. MVA cost modeling for L6 and L10 style reporting workflows

The tool acts as a guided workbook. Users enter model assumptions, machine rates, BOM distribution, process routing, labor allocations, overhead assumptions, and new-product setup data. The application then calculates throughput, bottlenecks, labor cost per unit, overhead cost per unit, and reporting exports.

## 2. Runtime Characteristics
- Delivery model: static HTML page loaded in browser
- UI technology: React 18 UMD with Babel in browser
- Styling: Tailwind CDN + inline class strings
- Charting: Recharts UMD
- Spreadsheet export: xlsx-populate browser build
- Persistence: browser localStorage for line standard library only
- Backend dependency: none for the main MVA UI flow
- Dynamic module dependency: optional ./mva/*.js helpers, but the current folder is empty, so runtime falls back to inline helper logic

## 3. Primary User Goals
- Estimate production line throughput and identify bottlenecks
- Model direct and indirect labor cost per unit
- Model overhead using equipment, space, and utility assumptions
- Capture new-product setup data for both L6 and L10 templates
- Import setup data from CSV templates
- Export JSON, CSV, and optional XLSX reports for downstream review
- Compare baseline line standards versus extra equipment for new products

## 4. Information Architecture
The legacy UI is organized as sidebar-driven tabs.

### 4.1 Simulation Tabs
- Basic Information
- Machine Rates
- BOM Mapping
- Model Process
- Simulation Results

### 4.2 MVA Plant Tabs
- Plant Rates
- Plant Environment
- Plant Equipment
- Plant Space
- DLOH / IDL

### 4.3 Product and Summary Tabs
- MPM L10 Setup
- MPM L6 Setup
- Labor Time Estimation L10
- Labor Time Estimation L6
- MVA Summary L10
- MVA Summary L6

## 5. Functional Specification

### 5.1 Basic Information
The user can edit production planning assumptions.

Inputs:
- Model name
- Work days
- Weekly demand
- Shifts per day
- Hours per shift
- Break minutes per shift
- Target utilization
- FPY
- OEE for placement machines
- OEE for non-placement machines
- Lot size
- Setup time per lot
- Boards per panel

Behavior:
- Values update simulation calculations immediately
- Navigation button advances the user to the next setup tab

### 5.2 Machine Rates
The user can edit nominal unit-per-hour rates for the default machine catalog.

Machine catalog includes:
- Printer
- SPI
- Multiple placement machine groups for chips, IC, BGA
- AOI
- Reflow
- THT/DIP
- Wave solder
- ICT
- FBT
- Visual inspection
- Assembly
- Packing
- Loader

Behavior:
- Machine group rate updates are reflected in simulation cycle time
- The machine list is the default baseline used by process routing

### 5.3 BOM Mapping
The user can define package-count mapping by side and machine group.

Supported defaults:
- Top side chip, IC, BGA buckets
- Bottom side chip, IC, BGA buckets

Behavior:
- Placement process cycle time depends on BOM counts assigned to that machine group and side
- Non-placement machines use fixed rate logic

### 5.4 Process Model
The user reviews the ordered routing of process steps.

Default process path contains 21 steps, including:
- Bottom-side SMT sequence
- Top-side SMT sequence
- THT / DIP and wave solder
- ICT and FBT
- Visual inspection
- Assembly and packing

Behavior:
- Process order drives the simulation loop
- Each step resolves to a machine group
- Each step receives a cycle time, utilization percentage, and bottleneck flag

### 5.5 Simulation Engine
The simulation engine calculates production capacity and bottleneck information.

Core calculations:
- Available time per day = shifts × hours × 60 - breaks
- Takt time = available weekly seconds / weekly demand
- Placement cycle time = component count / machine rate × 3600 / OEE
- Fixed machine cycle time = 1 / machine rate × 3600 / OEE
- Setup loss per board = setup time per lot / lot size
- Bottleneck = maximum cycle time across process steps
- UPH = 3600 / bottleneck cycle time
- Line balance efficiency = sum of cycle times / (bottleneck cycle time × number of steps)
- Weekly output = UPH × available hours × work days × target utilization

Outputs:
- Takt time KPI
- UPH KPI
- Line balance efficiency KPI
- Weekly output KPI with demand pass/fail highlight
- Bottleneck alert block
- Bar chart comparing process cycle times and takt reference
- Detailed process table with utilization and bottleneck marking

### 5.6 MVA Rates and Volume Planning
The plant rate view captures labor and volume assumptions.

Inputs:
- Direct labor hourly rate
- Indirect labor hourly rate
- Efficiency
- Demand mode: monthly or weekly
- RFQ quantity per month
- Weekly demand
- Work days per week
- Work days per month
- Process type: L6 or L10
- Yield strategy: ignore or apply_to_capacity
- FPY and VPY
- Optional use of FPY imported from L10 setup

Behavior:
- Monthly volume prefers RFQ input when available
- If RFQ is absent, monthly volume is inferred from UPH, shifts, hours, work days, and utilization
- Yield factor affects effective capacity only when strategy is apply_to_capacity

### 5.7 Plant Environment and Overhead
The environment view models overhead assumptions separately for L6 and L10.

Inputs:
- Equipment average useful life in years
- Equipment depreciation per month
- Equipment maintenance ratio
- Space monthly cost
- Power cost per unit
- Plant operation days per year

Behavior:
- L6 and L10 overhead assumptions are stored independently
- Active overhead context depends on selected process type

### 5.8 Equipment List Management
The equipment view supports baseline and extra equipment management.

Supported capabilities:
- Add, edit, delete baseline equipment items
- Add, edit, delete extra equipment items for new product delta
- Import equipment list CSV
- Import equipment template CSV into line standard library
- Select a line standard template
- Apply selected template to the current baseline list
- Rename or delete line standard templates
- Export and import line standard library JSON
- Persist line standard library in localStorage
- Optionally include extra equipment in total monthly overhead
- Optionally replace overhead depreciation with itemized equipment total

Equipment item fields:
- Process
- Item name
- Quantity
- Unit price
- Depreciation years
- Cost per month override

Calculation behavior:
- Automatic monthly cost = quantity × unit price / (depreciation years × 12)
- If manual cost per month is provided, that value overrides the calculated amount
- Baseline and extra equipment totals are shown separately and combined for delta analysis

### 5.9 Space Allocation Management
The plant space view supports two space costing modes.

Modes:
- Matrix mode
- Manual allocation mode

Matrix mode inputs:
- Line length in feet
- Line width in feet
- Space ratio
- Space rate per square foot

Manual allocation inputs:
- Floor
- Process
- Area in square feet
- Rate per square foot override
- Monthly cost override
- Area multiplier
- Optional building area override

Additional helper behavior:
- Optional 40/60 split helper for SMT versus HI space allocation
- Process name normalization maps several text variants into standard categories
- Supported process categories include SMT, F/A, FBT, and Non-Prod

Calculation behavior:
- Matrix mode computes line area × ratio × rate
- Manual mode computes monthly cost per row from area, rate, multiplier, and optional building area override
- Total space monthly cost can replace the plant overhead space assumption when enabled

### 5.10 Direct and Indirect Labor Setup
The application supports editing labor rows directly in the UI and importing structured CSV files.

Direct labor row fields:
- Name
- Process
- Headcount
- UPH source: line or override
- Override UPH

Indirect labor row fields:
- Name
- Department
- Role
- Headcount
- Allocation percent
- UPH source
- Override UPH
- RR note

Behavior:
- Users can add, edit, and delete rows
- Indirect labor supports two UI modes: L10 and L6
- In auto mode, the app infers which IDL structure to use from imported datasets
- Allocation percent is treated as a headcount multiplier in the calculation path

Calculation behavior:
- Cost per unit = hourly rate × effective headcount / (UPH used × efficiency)
- Effective headcount = headcount × allocation percent when allocation is present
- Grouped summaries are generated for direct labor by process and indirect labor by department

### 5.11 L10 Labor Time Estimation
The application supports station-based labor time entry for L10 reporting.

Station fields:
- Station name
- Labor headcount
- Machine quantity or parallel station count
- Cycle time in seconds
- Allowance factor

Derived outputs per station:
- Average cycle time
- UPH
- Per-shift capacity
- Daily capacity
- Weekly capacity
- Monthly capacity

Behavior:
- Imported or edited L10 stations can overwrite direct labor rows automatically
- A summary table distributes equipment cost across stations for display and export

### 5.12 L6 Labor Time Estimation
The application supports segment-based matrix input for L6 reporting.

Supported row categories include:
- Standard man-hours
- Allowance rate
- Fixture count or parallel stations
- Cycle time per piece
- VPY
- Shift rate
- DL online

Behavior:
- The app can seed official process columns for L6 matrices
- Segments can be added dynamically
- Stations can be added, updated, or removed within each segment
- The flattened station list can overwrite direct labor rows

Calculation behavior:
- Allowance input can be interpreted as factor or percentage depending on entered value range
- UPH derives from adjusted cycle time and parallel station count

### 5.13 New Product Setup for L10 and L6
The UI contains separate forms for L10 and L6 product-specific metadata.

L10 fields include:
- BU
- Customer
- Project name
- Model name
- Size
- Weight
- Test handling time
- Test function time
- Yield
- RFQ quantity per month
- Probe lifecycle
- RFQ lifecycle
- Work calendar values
- Shifts, hours, UPH, cycle time

L6 fields include:
- BU
- Customer
- PN or SKU
- RFQ quantity per month
- Boards per panel
- PCB size
- Station path
- Side 1 parts count
- Side 2 parts count
- Off-line blasting part count
- DIP type
- Selective wave solder usage
- Assembly part notes
- Routing times

Behavior:
- CSV import can populate either template
- Imported product values can update shared planning fields such as model name, work days, shifts, hours, RFQ, and yield

### 5.14 Cost Rollup and Summary Views
The summary views provide MVA outputs in L10 and L6 presentation modes.

Cost categories:
- Direct labor per unit
- Indirect labor per unit
- Equipment depreciation per unit
- Equipment maintenance per unit
- Space per unit
- Power per unit
- Material attrition per unit
- Consumables per unit
- SGA per unit
- Profit per unit
- ICC per unit
- Total per unit

Supporting outputs:
- Capacity assumptions block
- Detailed labor tables
- Grouped cost breakdowns
- L10 station summary table for template alignment

### 5.15 Confirmation and Export
The user must set an OK/NG confirmation state before report export is allowed.

Confirmation fields:
- Decision: OK or NG
- Reviewer
- Comment
- Decision timestamp

Export outputs:
- Full JSON report
- Summary CSV
- L10 summary CSV
- Optional L10 summary XLSX using workbook template
- L10 station breakdown CSV

Export behavior:
- Filenames include the model name and timestamp
- The export uses the current UI state as the authoritative snapshot
- Test hooks are present to intercept file downloads for browser automation

## 6. Import Interfaces
The legacy UI references importer helpers for the following sources:
- Month/year update CSV
- Equipment list CSV
- Equipment template CSV
- Extra equipment CSV
- Space setup CSV
- DLOH / IDL setup CSV
- L10 labor time estimation CSV
- L6 labor time estimation CSV
- L10 MPM setup CSV
- L6 MPM setup CSV
- Line standard library JSON

Current status:
- The import UI exists
- The dynamic helper modules are missing in the repository snapshot
- Most import actions fail over to warning paths or no-op behavior

## 7. Persistence Model
Persisted in browser:
- Line standard library JSON under localStorage key mva_line_standards_v1

Not persisted:
- Project state
- Simulation inputs
- MVA assumptions
- Labor rows
- Product forms
- Confirmation state

## 8. Legacy Technical Constraints
- All application state lives inside one top-level component
- Business logic and view code are tightly coupled
- Multiple render functions exceed maintainable size for single-file editing
- Many import and export capabilities assume helper modules that are absent
- There is no typed validation boundary for domain inputs
- There is no shared test harness for the HTML app
- There is no durable project storage or backend synchronization
- There is no audit log or version history at the application level

## 9. Related Backend Scope
The ddm-l6 backend contains a separate FastAPI application focused on MiniMOST and SOP analysis. It is not integrated into the MVA HTML flow, but it provides adjacent domain logic and tests. The current backend test coverage verifies a small set of TMU and modifier behaviors only.

## 10. Refactor Requirements for mva-v2
The new codebase shall:
- Move from single-file HTML to a modular, testable application architecture
- Preserve the legacy calculation intent and user workflows where behavior is still valid
- Replace missing dynamic helper modules with first-class source files
- Formalize domain models for simulation, MVA, labor, equipment, space, and exports
- Support deterministic import and export flows with validation
- Add unit, functional, e2e, and regression test coverage
- Be releasable with repeatable build and test commands

## 11. Known Gaps to Carry Into Refactor Backlog
- Missing runtime helper modules for import, CSV, line standard, and equipment mapping
- No project persistence beyond line standard library
- No collaboration or server synchronization
- No line rebalancing recommendation engine
- No SIMO or dual-hand modeling in MVA UI
- Ambiguous IDL allocation semantics
- No formal validation error model for imported files
- No API contract between frontend and backend domains
