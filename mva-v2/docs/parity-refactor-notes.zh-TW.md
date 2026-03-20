# Legacy Parity Refactor Notes

## 目的
- 將 mva-v2 的頁面階層改為與 legacy HTML 相同的工作流順序。
- 將原本已存在於 domain layer 但未暴露到 UI 的能力補回畫面。
- 讓測試與手動驗證都能直接對照原版頁面。

## 這次調整的主要邏輯

### 1. 導航階層改回 legacy 16-page workflow
- 原本狀態：React 版只有 8 個平鋪 tabs。
- 調整後：改成以下 16 個分頁，並按 legacy 分組展示。
  - Basic Information
  - Machine Rates
  - BOM Mapping
  - Model Process
  - Simulation Results
  - Labor Rate & Efficiency
  - Environment & Equipment Rate
  - Equipment List
  - Space Setup
  - DLOH-L & IDL Setup
  - MPM Setup (L10)
  - MPM Setup (L6)
  - Labor Time (L10)
  - Labor Time (L6)
  - Summary (L10)
  - Summary (L6)
- 原因：使用者要求 UI 每個階層關係必須跟 legacy 一樣，原本的合併式頁面不符合。

### 2. 補回 Model Process 的可編輯工作頁
- 原本狀態：只有 simulation output，沒有獨立 editable routing page。
- 調整後：新增 Model Process 頁，允許新增、修改、刪除 process steps。
- 原因：legacy workflow 中 process routing 是獨立維護頁，不只是結果展示。

### 3. 補回 DLOH-L / IDL 的可編輯介面
- 原本狀態：只看到 costed table，無法直接在 UI 維護 direct / indirect labor rows。
- 調整後：新增 direct labor 與 indirect labor 的 CRUD 介面。
- 原因：legacy workflow 中 labor setup 是主流程的一部分，不能只靠 import。

### 4. 補回 L10 / L6 Labor Time Estimation 獨立頁面
- 原本狀態：只顯示 snapshot，不能直接編修 stations。
- 調整後：新增 L10 與 L6 labor time estimation 頁面，可新增、修改、刪除 station，並可同步回 direct labor rows。
- 原因：legacy 版中 labor time estimation 是獨立工程作業面，不只是報表附屬資訊。

### 5. 補回 L10 / L6 Summary 獨立頁面
- 原本狀態：只有 dashboard / reports 的混合視圖。
- 調整後：新增 Summary (L10) 與 Summary (L6) 兩個獨立頁面，各自顯示：
  - simulation KPI
  - yield summary
  - direct / indirect labor breakdown
  - overhead / material / SGA / profit / ICC
  - full cost rollup
  - warnings
  - review gate
  - import / export
  - CSV preview
- 原因：legacy 版有明確的 L10 / L6 summary 工作頁，且交付審核依賴這些頁面。

### 6. 使用 process-specific project clone 計算 L10 / L6 summary
- 調整內容：新增 process-specific project helper，讓同一份 project state 可以分別生成 L10 與 L6 summary。
- 原因：避免單純依賴目前 active processType，導致 Summary (L10) / Summary (L6) 無法各自穩定呈現。

### 7. 將 Basic Information 改回 legacy 的 4 個輸入分組
- 原本狀態：Basic Information 將所有欄位攤平成單一 grid，缺少 Production Planning 等明確分組。
- 調整後：改回以下 4 張卡片分組。
  - Production Planning
  - Shift Configuration
  - Efficiency and Quality
  - Batch Settings
- 原因：使用者明確指出 production planning 分類缺失；legacy 版本的輸入理解是靠分組，不是只靠欄位存在。

### 8. 將 Model Process 改成 sequence table 形式
- 原本狀態：Model Process 以簡化 editable list 呈現，雖然有 step，但缺少 legacy 的 sequence table 語意。
- 調整後：改成表格式 Seq / Process / Side / Action，並加入 side badge 與 sticky header。
- 原因：使用者要求 sequence 必須明確可見；legacy 版本是 sequence table，不是鬆散 list editor。

### 9. 補回 Simulation Results 的 bottleneck alert 與 CT mountain chart
- 原本狀態：只有 KPI 與簡化表格，缺少 bottleneck alert 與圖表。
- 調整後：新增：
  - bottleneck alert
  - takt reference line
  - CT / bottleneck mountain chart
  - raw CT / loss / final CT / utilization detail table
- 原因：legacy simulation page 的核心價值在於快速辨識 bottleneck，而不是只看數字表格。

### 10. 將整體視覺收斂到較接近 legacy 的 light material 風格
- 原本狀態：畫面使用深色玻璃風，且 hero header 留白偏大。
- 調整後：改為淺色卡片、淡色側欄、較緊湊的 header、badge/progress/chart 色彩語意。
- 原因：使用者明確指出 UI 風格與留白配置偏離 legacy；這次先把主視覺方向拉回 legacy 風格。

## 涉及檔案
- [src/domain/models.ts](src/domain/models.ts)
- [src/components/Sidebar.tsx](src/components/Sidebar.tsx)
- [src/components/SectionCard.tsx](src/components/SectionCard.tsx)
- [src/styles.css](src/styles.css)
- [src/App.tsx](src/App.tsx)
- [tests/functional/app.spec.tsx](tests/functional/app.spec.tsx)

## 手動驗證方式

### 導航與 hierarchy
1. 啟動開發環境。
2. 檢查左側導航是否依序顯示 5 個分組：Simulation、Plant Inputs、MPM Report Setup、Labor Time Estimation、MVA Summary。
3. 檢查分組內是否有 16 個頁面，名稱與 legacy 一致。

### Basic Information
1. 進入 Basic Information。
2. 確認畫面拆成 4 張卡：Production Planning、Shift Configuration、Efficiency and Quality、Batch Settings。
3. 修改 Weekly Demand。
4. 切到 Simulation Results，確認 KPI 有跟著變化。

### Model Process
1. 進入 Model Process。
2. 確認表頭為 Seq / Process / Side / Action，且捲動時表頭固定。
3. 新增一列 step，輸入 process 與 side。
4. 切到 Simulation Results，確認該 routing 被納入結果。

### Simulation Results
1. 進入 Simulation Results。
2. 確認有 KPI、bottleneck alert、CT mountain chart、detail table。
3. 調高 Weekly Demand 或修改某個 machine rate / process，使 bottleneck 改變。
4. 確認 alert 內容、chart 高柱位置、detail table highlight row 會同步變化。

### DLOH-L / IDL Setup
1. 進入 DLOH-L & IDL Setup。
2. 新增 direct labor 與 indirect labor row。
3. 切到 Summary (L10) 或 Summary (L6)，確認 labor breakdown 有更新。

### Labor Time Estimation
1. 進入 Labor Time (L10) 或 Labor Time (L6)。
2. 修改 station 的 HC 或 cycle time。
3. 確認 computed snapshot 有變化。
4. 按 Apply to Direct Labor，回到 DLOH-L & IDL Setup，確認 direct labor rows 已同步。

### Summary
1. 進入 Summary (L10) 與 Summary (L6)。
2. 確認都有獨立 cost rollup、warnings、review gate、summary preview。
3. 在 Review Gate 填入 reviewer 與 decision，確認狀態即時更新。

## 測試驗證
- Build:
  - corepack pnpm build
- Functional:
  - corepack pnpm exec vitest run tests/functional/app.spec.tsx --reporter=verbose
- Regression:
  - corepack pnpm exec vitest run tests/regression/summary.spec.ts --reporter=verbose
- Smoke:
  - bash tests/e2e/smoke.sh

## 目前仍值得持續精進的地方
- 官方 Excel 樣板匯出仍未完全追平 legacy xlsx-populate 工作流。
- L10 / L6 labor time 頁面雖已獨立，但尚未完全覆蓋 legacy 的 matrix / header parity 細節。
- Plant Inputs、MPM Setup、Summary 頁仍有部分欄位與版面可再逐頁對照 legacy 繼續收斂。
- Equipment delta 目前以成本差異為主，還可再補齊更多 legacy 粒度欄位。