# MVA-v2 Manual Smoke Test Plan

**Release Branch:** `202603-rc4`  
**Purpose:** Human QA sign-off procedure before merging to main.  
**Estimated time:** 30–45 minutes  
**Prerequisites:** Chrome or Edge (latest), access to the dev or preview server.

---

## Setup

1. In a terminal:
   ```bash
   cd mva-v2
   corepack pnpm run build && corepack pnpm run preview
   ```
2. Open **http://127.0.0.1:4173** in the browser.
3. Open the browser **DevTools Console** (F12 → Console) and keep it visible throughout. Any `[Error]` output is a test failure.

---

## Section 1 — Application Shell

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 1.1 | Load the URL. | App renders with the hero panel, left sidebar, and "Basic Information" tab active. No console errors. | |
| 1.2 | Inspect the sidebar. | Sidebar shows groups: **Simulation**, **Plant MVA**, **MPM Setup**, **Labor Time**, **Summary**. Each group is collapsible if the screen is narrow. | |
| 1.3 | Click every sidebar item in order. | Each click changes the main content area. No blank screens, no console errors. | |

---

## Section 2 — Basic Information Tab

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 2.1 | Navigate to **Basic Information**. | Form shows Model Name, Weekly Demand, Shifts/Day, Hours/Shift, OEE, and batch-size fields. | |
| 2.2 | Change **Weekly Demand** to `5000`. | Field accepts input immediately; no page reload. | |
| 2.3 | Navigate to **Simulation Results** and back to **Basic Information**. | Weekly Demand still shows `5000` (state persisted in memory). | |
| 2.4 | Reload the page. | The value is restored (localStorage persistence). | |

---

## Section 3 — File Import Verification

### 3A — Project JSON Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3A.1 | Navigate to **Summary (L10)** tab. | Summary page visible with Imports and Exports card at the bottom. | |
| 3A.2 | Click the hero **"Export Project JSON"** button. | Browser downloads a file named `<ModelName>_<timestamp>_project.json`. | |
| 3A.3 | In the Imports and Exports card, click **"Import Project JSON"** and select the downloaded file. | Status banner appears: `Imported project: <filename>`. No console errors. | |
| 3A.4 | Check **Basic Information** tab. | All field values match what was exported. | |

### 3B — Equipment Setup CSV Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3B.1 | Create a CSV file with this content: | | |
|   | `id,process,item,qty,unitPrice,depreciationYears` | | |
|   | `EQ-1,SMT,Placement line,1,540000,5` | | |
| 3B.2 | On the **Summary (L10)** page, use **"Import Equipment Setup CSV"** to select the file. | Status banner: `Imported equipment setup CSV: <filename>`. | |
| 3B.3 | Navigate to **Equipment List** tab. | Equipment list table shows one row: SMT / Placement line / 1 unit. | |
| 3B.4 | Create a malformed CSV (remove the `process` header column) and attempt to import it. | Status banner shows an error message about missing required headers. No crash. | |

### 3C — Space Setup CSV Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3C.1 | Create a CSV file: | | |
|   | `id,floor,process,areaSqft,ratePerSqft` | | |
|   | `SP-1,F1,SMT,1800,8` | | |
|   | `SP-2,F1,F/A,1200,8` | | |
| 3C.2 | Use **"Import Space Setup CSV"** on the Summary page. | Status banner confirms import. | |
| 3C.3 | Navigate to **Space Setup** tab. | Space allocation table shows two rows (SMT 1800 sqft, F/A 1200 sqft). | |

### 3D — DLOH/IDL Setup CSV Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3D.1 | Create a CSV file: | | |
|   | `kind,id,name,process,headcount,uphSource` | | |
|   | `direct,DL-1,Assembly,Assembly,15,line` | | |
|   | `indirect,IDL-1,TE,,8,line` | | |
| 3D.2 | Use **"Import DLOH/IDL Setup CSV"** on the Summary page. | Status banner confirms import. | |
| 3D.3 | Navigate to **DLOH-L & IDL Setup** tab. | Direct labor table shows Assembly (HC 15); Indirect table shows TE (HC 8). | |

### 3E — Line Standards JSON Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3E.1 | Navigate to **Equipment List** tab. | "Save as Line Standard" button is visible. | |
| 3E.2 | Click **"Export Line Standards JSON"** from the Summary page. | Browser downloads `line-standards_<timestamp>.json`. | |
| 3E.3 | Use **"Import Line Standards JSON"** on the Summary page to re-import it. | Status banner confirms import. Template selector in Equipment List still shows the standard. | |

### 3F — File Size Guard

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 3F.1 | Create any file larger than 5 MB and attempt to import it via any import slot. | Status banner shows: `File exceeds 5 MB limit.` No crash. | |

---

## Section 4 — Excel Template Export (XLSX)

### 4A — Trigger Export

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 4A.1 | Navigate to **Summary (L10)** tab. | Review Gate and summary table are visible. | |
| 4A.2 | Set the Review Gate **Decision** to `OK`, enter a **Reviewer** name (e.g., `QA-001`), and add a **Comment** (`Phase 5 smoke test`). | Fields accept input. Recorded-At timestamp updates. | |
| 4A.3 | Click the **"Export MVA XLSX"** button (top of SummaryPage, distinct from the CSV export). | Browser downloads a file named `<ModelName>_<timestamp>_L10_mva.xlsx`. | |

### 4B — Verify in Microsoft Excel

Open the downloaded `.xlsx` file and verify each sheet:

#### Sheet: `Summary L10`

| # | Check | Expected | Pass / Fail |
|---|---|---|---|
| 4B.1 | Row 1, Col A | `MVA Summary — L10` | |
| 4B.2 | Row 1, Col B | Model name entered in Basic Information | |
| 4B.3 | Row 1, Col D | `Generated: YYYY-MM-DD` (today's date) | |
| 4B.4 | Rows with `Confirm` in col A | Decision = `OK`, Reviewer = `QA-001`, Comment = `Phase 5 smoke test` | |
| 4B.5 | `Capacity Assumptions` section | Monthly FCST, Days/Month, Shifts/Day, Hours/Shift, Line UPH values populated (non-zero) | |
| 4B.6 | `A. LABOR Cost` section | At least one `DL` row present; `DL Total` row present | |
| 4B.7 | `D. Overhead` section | Equipment Depreciation, Space/Utility, Power rows present | |
| 4B.8 | `MVA Total Cost / Unit` row | Numeric non-zero value in col C | |
| 4B.9 | `Simulation` rows at bottom | UPH, Takt Time, Weekly Output, Bottleneck process present | |
| 4B.10 | Column widths | Col A ≈ 22 ch, Col B ≈ 28 ch — readable without expanding | |

#### Sheet: `Cost Rollup`

| # | Check | Expected | Pass / Fail |
|---|---|---|---|
| 4C.1 | Row 1 | `Cost Category` / `Value / Unit` headers | |
| 4C.2 | Last row | `Total / Unit` / numeric value | |
| 4C.3 | Row count | equals number of cost line items + 2 (header + total) | |

#### Sheet: `Simulation`

| # | Check | Expected | Pass / Fail |
|---|---|---|---|
| 4D.1 | Row 1 | `Step / Process / Side / Raw CT (s) / Final CT (s) / Utilization % / Bottleneck` | |
| 4D.2 | Bottleneck row | Exactly one row has `YES` in the Bottleneck column | |
| 4D.3 | Utilization % | All values are between 0 and 100 (no percentage sign — raw decimal × 100) | |

### 4E — L6 XLSX Export

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 4E.1 | Navigate to **Summary (L6)** tab, click **"Export MVA XLSX"**. | Downloads `<ModelName>_<timestamp>_L6_mva.xlsx`. Sheet is named `Summary L6`. | |
| 4E.2 | Open in Excel. Verify `Summary L6` sheet has a `Boards / Panel` row in the Capacity Assumptions section. | `Boards / Panel` row present (L6-specific field). | |
| 4E.3 | Verify the L10 export does **not** contain a `Boards / Panel` row. | Row absent in `Summary L10` sheet. | |

---

## Section 5 — Space Setup State Machine

Navigate to **Space Setup** tab.

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 5.1 | Observe the **Mode** dropdown. Default is `manual`. | Allocation table is editable; matrix-specific inputs (line dimensions, floor area) are hidden. | |
| 5.2 | Click **"Add Row"** in the allocation table, fill in Floor `F1`, Process `SMT`, Area `1500`. | Row appears in table. Monthly cost auto-calculates. | |
| 5.3 | Click **"Delete"** on the new row. | Row is removed. | |
| 5.4 | Change **Mode** to `matrix`. | New section appears: Line Specification (Length, Width, Multiplier) and Building Area by Floor (BF/F1/F2) and Process Distribution. Manual allocation table is still visible below. | |
| 5.5 | Enter Line Length `100`, Width `20`, Multiplier `1.2`. Observe **Total Line Area** field. | Reads `2400.00` sq ft (100 × 20 × 1.2). | |
| 5.6 | Enter a Process Distribution percentage (e.g., SMT = 60, F/A = 40), then click **"Apply Matrix Allocation"**. | Space allocation table is replaced with two rows derived from the percentages. | |
| 5.7 | Enable the **40/60 Split** toggle and set Total Floor Sqft to `3000`. Set Hi-Process mode to `FA`. Click **"Apply 40/60 Split"**. | Allocation table replaced with two rows: SMT = 1200 sqft (40%), F/A = 1800 sqft (60%). | |
| 5.8 | Change Hi-Process to `FBT`, click **"Apply 40/60 Split"**. | Second row now shows `FBT` instead of `F/A`. | |
| 5.9 | Change Hi-Process to `FA + FBT`, click **"Apply 40/60 Split"**. | Three rows: SMT 1200, F/A 900, FBT 900. | |

---

## Section 6 — L6 Segment Routing

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 6.1 | Navigate to **MPM Setup (L6)** tab. | L6 product form with SKU, PN, PCB dimensions, test time fields. | |
| 6.2 | Enter SKU `TEST-SKU-01` and at least one routing time row. | Routing time table accepts new row with process name and time in seconds. | |
| 6.3 | Navigate to **Labor Time (L6)** tab. | L6 segment matrix renders. If no segments exist, a fallback segment named `TEST-SKU-01` or `Segment 1` is shown. | |
| 6.4 | Click **"Add Station"** inside the first segment. | New station row appears with empty Name, HC 0, CT 0, Allowance 0. | |
| 6.5 | Fill in station Name `Station A`, CT `45`, Allowance `0.15`. Observe the snapshot columns. | Avg CT = `45 × 1.15 = 51.75 s`, UPH ≈ `69.6`. | |
| 6.6 | Click **"Add Segment"**. | New empty segment block appears below the first. | |
| 6.7 | Click **"Delete Segment"** on the new segment. | Segment removed; first segment remains intact. | |
| 6.8 | Click **"Apply to Direct Labor"** button. | Status banner appears. Navigate to **DLOH-L & IDL Setup** tab — Direct Labor table now contains one row per station with name and HC populated. | |

### 6B — L6 CSV Import

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 6B.1 | Prepare an L6 labor time estimation CSV with at least two segments (place a `Total` row between them). | | |
| 6B.2 | On **Labor Time (L6)** tab, click **"Import L6 Labor Time Estimation"** and select the file. | Status banner confirms import. | |
| 6B.3 | Verify segment count matches the number of non-total header groups in the CSV. | Correct segment count rendered; each segment has its own station rows. | |
| 6B.4 | Verify the flat `.stations` array across all segments (check **Apply to Direct Labor**) contains all stations across all segments, not just the first. | Direct Labor table in DLOH-L tab has all stations. | |

---

## Section 7 — Simulation Results

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 7.1 | Navigate to **Simulation Results** tab. | Four KPI cards visible: System UPH, Takt Time (s), Weekly Capacity, Line Balance %. | |
| 7.2 | Verify KPI values are non-zero numerics. | No `NaN` or `Infinity` displayed. | |
| 7.3 | In the mountain chart / bar chart area, the tallest bar is visually distinct. | Bottleneck step bar has a different colour or indicator. | |
| 7.4 | Verify the simulation results table. | Each row shows Step, Process, Side, CT, Utilization %; the bottleneck row is highlighted. | |

---

## Section 8 — Error Boundary

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 8.1 | In DevTools Console, type `window.__FORCE_ERROR__ = true` (this only applies if an error-trigger test hook exists; skip if not). | — | |
| 8.2 | Verify that if any feature component throws at runtime, the page does not go fully blank; an `ErrorBoundary` fallback message is shown instead of an empty screen. | Fallback UI appears for the affected tab region. Other tabs still work. | |

---

## Section 9 — Persistence & State Isolation

| # | Step | Expected Result | Pass / Fail |
|---|---|---|---|
| 9.1 | Navigate to **Equipment List** tab. Change the template selector to any available line standard. | Selection persists in the dropdown. | |
| 9.2 | Reload the page. | The selected line standard is still selected (saved to localStorage). | |
| 9.3 | Open the app in a second browser tab. Make a change in the first tab and reload the second. | The second tab reflects the reloaded saved state (they share `localStorage`). | |

---

## Sign-Off Checklist

Upon completing all sections above with no failures, the QA lead must complete the following:

- [ ] All 9 sections passed with zero failures logged
- [ ] No unhandled JavaScript errors appeared in the DevTools Console
- [ ] XLSX file opened without warnings in Microsoft Excel (version 2019+ or Microsoft 365)
- [ ] `Summary L10` and `Summary L6` sheets both contain the correct review gate metadata
- [ ] Reviewer name: ___________________
- [ ] Sign-off date: ___________________
- [ ] Verdict: **PASS** / **FAIL**

---

## Appendix — Test Data Reference

### Minimal Equipment CSV
```csv
id,process,item,qty,unitPrice,depreciationYears
EQ-1,SMT,Placement line,1,540000,5
EQ-2,F/A,Wave Solder,1,120000,7
```

### Minimal Space CSV
```csv
id,floor,process,areaSqft,ratePerSqft
SP-1,F1,SMT,1800,8
SP-2,F1,F/A,1200,8
```

### Minimal Labor CSV
```csv
kind,id,name,process,headcount,uphSource
direct,DL-1,Assembly,Assembly,15,line
direct,DL-2,Inspection,F/A,4,line
indirect,IDL-1,TE,,8,line
```

### Minimal L6 Labor Time Estimation CSV
```csv
meta,segmentname,SEG-A
station,name,laborhc,cycletime,allowancerate
,Station A,2,45,0.15
,Station B,1,38,0.15
total,,3,45,
meta,segmentname,SEG-B
station,name,laborhc,cycletime,allowancerate
,Station C,2,52,0.15
total,,2,52,
```
