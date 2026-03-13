# StreamWeaver MVA 舊版功能規格

## 文件控管
- 版本: 1.0
- 日期: 2026-03-13
- 基準來源: ddm-l6/l6-ddm-demo.html 與相關 ddm-l6 檔案
- 讀者: 軟體工程師、QA 工程師、架構師
- 目的: 在重構到 mva-v2 前，完整封存目前舊版功能與限制

## 1. 產品摘要
舊版系統是一個純瀏覽器執行的製造規劃與成本分析工具，以單一 HTML 檔案承載 React、狀態管理、計算邏輯與匯入匯出流程。它把兩個主要領域放在同一個操作流程中：

1. L6 產線模擬
2. L6 與 L10 報表導向的 MVA 成本建模

整體使用方式類似工程工作簿。使用者輸入生產規劃、機台速率、BOM 分布、製程路徑、勞務配置、製造費用、新產品設定等資訊，系統即時計算產能、瓶頸、單位人工成本、單位製造費用，以及各種報表輸出。

## 2. 執行特性
- 交付模式: 靜態 HTML 單頁
- UI 技術: React 18 UMD 搭配瀏覽器內 Babel
- 樣式: Tailwind CDN 與大量 inline class
- 圖表: Recharts UMD
- 試算表輸出: xlsx-populate 瀏覽器版
- 持久化: 僅有 line standard library 存在 localStorage
- 後端依賴: 主 MVA UI 流程沒有硬性依賴
- 動態模組依賴: 預期存在 ./mva/*.js，但目前目錄為空，因此執行時大量依靠 inline fallback 邏輯

## 3. 主要使用目標
- 估算產線吞吐量並找出瓶頸站點
- 建立直接與間接人工的單位成本
- 以設備、空間與用電假設建構製造費用
- 輸入 L6 與 L10 新產品設定資料
- 從 CSV 匯入設定模板
- 輸出 JSON、CSV 與選配 XLSX 報表
- 比較 baseline line standard 與新產品額外設備差異

## 4. 資訊架構
舊版 UI 由側邊欄 tab 導覽組成。

### 4.1 模擬區
- Basic Information
- Machine Rates
- BOM Mapping
- Model Process
- Simulation Results

### 4.2 MVA Plant 區
- Plant Rates
- Plant Environment
- Plant Equipment
- Plant Space
- DLOH / IDL

### 4.3 產品與摘要區
- MPM L10 Setup
- MPM L6 Setup
- Labor Time Estimation L10
- Labor Time Estimation L6
- MVA Summary L10
- MVA Summary L6

## 5. 功能規格

### 5.1 Basic Information
使用者可編輯生產規劃的基礎假設。

輸入項目:
- Model name
- Work days
- Weekly demand
- Shifts per day
- Hours per shift
- Break minutes per shift
- Target utilization
- FPY
- Placement OEE
- Others OEE
- Lot size
- Setup time per lot
- Boards per panel

行為:
- 所有欄位修改後立即影響模擬計算
- 可按下一步切到下一個設定頁

### 5.2 Machine Rates
使用者可編輯預設設備清單的 nominal unit-per-hour 速率。

設備類別包含:
- Printer
- SPI
- 多種 placement machine group for chips, IC, BGA
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

行為:
- 機台速率調整會直接影響 cycle time
- 這份機台清單是製程模擬的 baseline

### 5.3 BOM Mapping
使用者可定義 package count 與 side、machine group 的對應。

預設資料包含:
- Top side chip, IC, BGA
- Bottom side chip, IC, BGA

行為:
- Placement 類製程的 cycle time 取決於 side 與 machine group 對應的 BOM count
- 非 placement 類製程使用固定速率邏輯

### 5.4 Model Process
使用者檢視並依序使用既定的 routing process。

預設 21 個步驟，包含:
- Bottom-side SMT sequence
- Top-side SMT sequence
- THT / DIP 與 wave solder
- ICT 與 FBT
- Visual inspection
- Assembly 與 packing

行為:
- 流程序列驅動整個 simulation loop
- 每一步會對應 machine group
- 每一步都會得到 cycle time、utilization 與 bottleneck 標記

### 5.5 Simulation Engine
模擬引擎用來計算產能與瓶頸。

核心公式:
- Available time per day = shifts × hours × 60 - breaks
- Takt time = available weekly seconds / weekly demand
- Placement cycle time = component count / machine rate × 3600 / OEE
- Fixed machine cycle time = 1 / machine rate × 3600 / OEE
- Setup loss per board = setup time per lot / lot size
- Bottleneck = process 中最大 cycle time
- UPH = 3600 / bottleneck cycle time
- Line balance efficiency = sum of cycle times / (bottleneck cycle time × number of steps)
- Weekly output = UPH × available hours × work days × target utilization

輸出:
- Takt time KPI
- UPH KPI
- Line balance efficiency KPI
- Weekly output KPI，並以需求達成與否標示顏色
- Bottleneck alert
- 製程 cycle time 對 takt 的圖表
- 各站詳細表格

### 5.6 MVA Rates 與 Volume Planning
Plant rate 頁面負責直接與間接人工成本，以及產量假設。

輸入項目:
- Direct labor hourly rate
- Indirect labor hourly rate
- Efficiency
- Demand mode: monthly 或 weekly
- RFQ quantity per month
- Weekly demand
- Work days per week
- Work days per month
- Process type: L6 或 L10
- Yield strategy: ignore 或 apply_to_capacity
- FPY 與 VPY
- 可選是否使用 L10 匯入的 FPY

行為:
- 月產量優先使用 RFQ
- 若 RFQ 缺失，則由 UPH、班次、工時、工作日與利用率推算
- Yield 只有在 apply_to_capacity 時才影響有效產能

### 5.7 Plant Environment 與 Overhead
Environment 頁面可分別維護 L6 與 L10 的 overhead 假設。

輸入項目:
- Equipment average useful life in years
- Equipment depreciation per month
- Equipment maintenance ratio
- Space monthly cost
- Power cost per unit
- Plant operation days per year

行為:
- L6 與 L10 各有獨立 overhead 狀態
- 使用中的 overhead 取決於目前 process type

### 5.8 Equipment List 管理
Equipment 頁面可管理 baseline 與 extra equipment。

支援功能:
- 新增、編輯、刪除 baseline equipment
- 新增、編輯、刪除新產品 delta 用 extra equipment
- 匯入 equipment list CSV
- 匯入 equipment template CSV 到 line standard library
- 選擇 line standard template
- 套用選中的 template 到目前 baseline list
- 重新命名或刪除 line standard template
- 匯出與匯入 line standard library JSON
- 以 localStorage 持久化 line standard library
- 可選是否將 extra equipment 納入總製造費用
- 可選是否以 itemized equipment total 取代 overhead depreciation

設備欄位:
- Process
- Item name
- Quantity
- Unit price
- Depreciation years
- Cost per month override

計算行為:
- Automatic monthly cost = quantity × unit price / (depreciation years × 12)
- 若手動輸入 monthly cost，則優先採用 override 值
- Baseline 與 extra equipment 會分別計算並組成 delta 分析

### 5.9 Space Allocation 管理
Plant space 頁面支援兩種空間成本模式。

模式:
- Matrix mode
- Manual allocation mode

Matrix mode 輸入:
- Line length in feet
- Line width in feet
- Space ratio
- Space rate per square foot

Manual mode 輸入:
- Floor
- Process
- Area in square feet
- Rate per square foot override
- Monthly cost override
- Area multiplier
- Optional building area override

額外輔助:
- 可用 40/60 split helper 分配 SMT 與 HI 空間
- Process name 會做 normalization
- 標準 process 類別包含 SMT、F/A、FBT、Non-Prod

計算行為:
- Matrix mode 以 line area × ratio × rate 計算
- Manual mode 依 area、rate、multiplier 與 building area override 計算各列成本
- 若啟用，總空間月成本可覆蓋原本 overhead space assumption

### 5.10 Direct 與 Indirect Labor 設定
系統支援 UI 直接維護 labor rows，也支援從 CSV 匯入。

Direct labor 欄位:
- Name
- Process
- Headcount
- UPH source: line 或 override
- Override UPH

Indirect labor 欄位:
- Name
- Department
- Role
- Headcount
- Allocation percent
- UPH source
- Override UPH
- RR note

行為:
- 可新增、編輯、刪除 row
- IDL 支援 L10 與 L6 兩種 UI mode
- Auto mode 會依匯入資料推測目前使用哪種 IDL 結構
- Allocation percent 在計算路徑中被當成 headcount multiplier

計算行為:
- Cost per unit = hourly rate × effective headcount / (UPH used × efficiency)
- Effective headcount = headcount × allocation percent，如果 allocation 存在
- 直接人工依 process 彙總，間接人工依 department 彙總

### 5.11 L10 Labor Time Estimation
系統支援 L10 的 station-based labor time 設定。

Station 欄位:
- Station name
- Labor headcount
- Machine quantity 或 parallel station count
- Cycle time in seconds
- Allowance factor

每站衍生輸出:
- Average cycle time
- UPH
- Per-shift capacity
- Daily capacity
- Weekly capacity
- Monthly capacity

行為:
- 匯入或編輯 L10 station 後，可自動覆蓋 direct labor rows
- 顯示與匯出用 summary table 會把 equipment cost 分攤到 station

### 5.12 L6 Labor Time Estimation
系統支援 L6 的 segment-based matrix 輸入。

支援列別包含:
- 標準工時
- 寬放率
- 治具數或 parallel stations
- Cycle time per piece
- VPY
- Shift rate
- DL online

行為:
- 可自動補出 official process columns
- 可動態新增 segment
- 每個 segment 可新增、更新、刪除 station
- Flatten 後的 station list 可覆蓋 direct labor rows

計算行為:
- Allowance 會依輸入值範圍判斷是 factor 還是 percentage
- UPH 由調整後 cycle time 與 parallel station count 推導

### 5.13 L10 與 L6 新產品設定
UI 提供 L10 與 L6 兩套新產品欄位。

L10 欄位包含:
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
- 工作日曆資訊
- Shifts、hours、UPH、cycle time

L6 欄位包含:
- BU
- Customer
- PN 或 SKU
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

行為:
- 可由 CSV 匯入任一模板
- 匯入後會更新共用 planning 欄位，例如 model name、work days、shifts、hours、RFQ、yield

### 5.14 Cost Rollup 與 Summary Views
Summary views 以 L10 與 L6 兩種呈現模式展示 MVA 結果。

成本類別:
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

輔助輸出:
- Capacity assumptions 區塊
- Detailed labor tables
- Grouped cost breakdowns
- L10 station summary table

### 5.15 Confirmation 與 Export
使用者必須先設定 OK/NG，才能匯出報表。

確認欄位:
- Decision: OK 或 NG
- Reviewer
- Comment
- Decision timestamp

輸出內容:
- Full JSON report
- Summary CSV
- L10 summary CSV
- 選用 L10 summary XLSX
- L10 station breakdown CSV

匯出行為:
- 檔名包含 model name 與 timestamp
- 匯出以當前 UI state 為唯一權威快照
- 內建 test hook 可攔截下載，供瀏覽器自動化測試使用

## 6. 匯入介面
舊版 UI 預期支援下列匯入來源:
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

目前狀態:
- 匯入 UI 存在
- 但 repository 中缺少對應動態 helper modules
- 多數匯入行為只會走 warning path，或直接沒有實際效果

## 7. 持久化模型
瀏覽器可持久化:
- Line standard library JSON，localStorage key 為 mva_line_standards_v1

不會持久化:
- Project state
- Simulation inputs
- MVA assumptions
- Labor rows
- Product forms
- Confirmation state

## 8. 舊版技術限制
- 全部應用狀態都在單一頂層 component
- 業務邏輯與畫面邏輯高度耦合
- 多個 render function 已大到不適合單檔維護
- 多數匯入匯出能力依賴缺失的 helper modules
- 缺少型別化與驗證邊界
- 單頁 HTML 沒有共用測試基礎設施
- 沒有可持久化的 project storage 或 backend synchronization
- 沒有 audit log 或版本歷程

## 9. 相關後端範圍
在 ddm-l6 目錄下還有一個獨立 FastAPI 後端，主要處理 MiniMOST 與 SOP 分析。它沒有真正整合到目前的 MVA HTML 流程，但屬於相鄰領域。現有後端測試只覆蓋少量 TMU 與 modifier 邏輯。

## 10. mva-v2 重構要求
新 codebase 必須:
- 從單檔 HTML 改為模組化、可測試的應用架構
- 在行為仍然合理的前提下保留舊版計算意圖與主要工作流
- 將缺失的動態 helper modules 變成正式 source files
- 為 simulation、MVA、labor、equipment、space、exports 建立明確 domain model
- 讓匯入匯出流程可驗證且可重現
- 補齊 unit、functional、e2e、regression 測試
- 具備可重複執行的 build 與 test 指令，達到 release-ready

## 11. 需要帶入重構 backlog 的已知缺口
- import、CSV、line standard、equipment mapping 等 runtime helper modules 缺失
- 除 line standard library 外，沒有 project persistence
- 沒有 collaboration 或 server synchronization
- 沒有 line rebalancing recommendation engine
- MVA UI 缺少 SIMO 與左右手建模
- IDL allocation 語意不夠清楚
- 匯入檔案沒有正式 validation error model
- 前後端 domain 之間沒有明確 API contract
