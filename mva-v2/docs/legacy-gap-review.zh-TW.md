# Legacy Gap Review

## 目的
- 列出目前 mva-v2 與 legacy HTML 還未完全一致的欄位、版型、互動與輸出差異。
- 這份文件只記錄剩餘差異，不重複已完成的 parity 工作。

## 已完成到可交付程度的區塊
- Basic Information 四分組卡片
- Machine Rates、BOM Mapping、Model Process、Simulation Results 主流程頁
- Plant Inputs 五大頁面的主結構與大部分輸入欄位
- MPM Setup L10 / L6 主欄位與匯入流程
- L10 / L6 labor time estimation 的主要欄位與寬表格
- Summary L10 / L6 的 confirm、capacity、labor、cost rollup、warnings、匯出
- 左側導覽與右側內容分離捲動

## 目前仍未完全追平 legacy 的差異

### 1. Plant Inputs
- Environment & Equipment Rate
  - 尚未把 L6 / L10 overhead 內容拆成與 legacy 完全一致的視覺順序
  - 尚未補「Import Equipment List Setup CSV」直接放在頁內主區塊的 legacy 位置感
- Equipment List
  - 尚未完整補回 equipment-to-simulation mapping 欄位與互動
  - delta table 仍是簡化版，尚未達到 legacy 對照表的完整粒度
- Space Setup
  - matrix mode 已有主體，但尚未做到 legacy 的 floor/process disabled 狀態細節
  - 40/60 split 已補控制，但視覺與 legacy 的提示文字仍可再收斂
- DLOH-L & IDL Setup
  - direct / indirect labor 的欄位已補主體，但 L10/L6 mode-specific 顯示細節仍可再微調

### 2. MPM Setup
- MPM Setup L10
  - 版面已接近 legacy，但欄位排列次序仍可再細對 legacy HTML
  - 尚未把所有 derived result 以 legacy 原貌呈現
- MPM Setup L6
  - 已補 By Product / Header / Product Setup，但還沒做到 legacy 的全部群組命名與排版密度
  - routingTimesSec 尚未展成更接近 legacy 的逐站欄位布局

### 3. Labor Time Estimation
- L10
  - 已有 meta 與寬表格，但尚未完全重現 legacy 的 sticky first-column 體驗
  - station 計算欄位目前為單表寬列，非完全一致的 legacy 呈現方式
- L6
  - 已補 allowance / vpy / shift rate / dl online 欄位
  - 目前仍為寬表格，不是 legacy 最接近的 segment matrix 橫向矩陣版型
  - segments 目前尚未做完整多段管理與自訂 segment UI

### 4. Summary
- L10 / L6 summary 已達功能交付，但與 legacy 相比仍有兩類差異
  - 某些 cost category 的視覺 grouping 尚未逐表一比一複刻
  - Export MVA 的 legacy 風格按鈕與最終格式仍是現代化簡化版

### 5. 視覺與互動
- 整體已從深色玻璃風改回淺色 material 方向，但仍不是 legacy HTML 的逐像素複刻
- 某些表格的字級、間距、sticky 行為與 badge 顏色仍可再收斂

## 建議優先順序

### P1
- L6 labor time estimation 改為更接近 legacy 的 segment matrix 橫向版型
- Equipment simulation mapping 補齊
- Summary L10 / L6 的欄位順序與群組細節微調

### P2
- MPM L10 / L6 各群組欄位順序逐一與 legacy HTML 對齊
- Space Setup 的 disabled / manual / matrix 細節狀態微調

### P3
- 視覺細節逐像素收斂
- 匯出按鈕與最終報表呈現再向 legacy 風格靠攏

## 手動比對方式
1. 左側依頁切換，對照 legacy HTML 的同名頁面。
2. 逐區檢查欄位名稱、群組標題、欄位順序、是否可編輯。
3. 對 labor / space / equipment / summary 類頁面檢查表格欄位順序與 sticky 行為。
4. 對 L10 / L6 summary 檢查 confirm、capacity assumptions、labor tables、cost rollup 是否完整。