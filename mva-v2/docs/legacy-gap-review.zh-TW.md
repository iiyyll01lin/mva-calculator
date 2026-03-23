# Legacy Gap Review

## 目的
- 列出目前 mva-v2 與 legacy HTML 還未完全一致的欄位、版型、互動與輸出差異。
- 這份文件只記錄剩餘差異，不重複已完成的 parity 工作。

## 已完成到可交付程度的區塊
- Basic Information 四分組卡片
- Machine Rates、BOM Mapping、Model Process、Simulation Results 主流程頁
- Plant Inputs 五大頁面的主結構與大部分輸入欄位
- Equipment List 的 equipment-to-simulation mapping 欄位、preview、寫回流程
- MPM Setup L10 / L6 主欄位、routing time、匯入流程與命名收斂
- L10 / L6 labor time estimation 的主要欄位，且 L6 已改成 segment matrix 主版型
- Summary L10 / L6 的 product profile、confirm、capacity、labor、cost rollup、warnings、匯出
- 左側導覽與右側內容分離捲動
- 深色主題與整體深色 worksheet 風格

## 目前仍未完全追平 legacy 的差異

### 1. Plant Inputs
- Environment & Equipment Rate
  - 尚未把 L6 / L10 overhead 內容拆成與 legacy 完全一致的視覺順序
  - 尚未補「Import Equipment List Setup CSV」直接放在頁內主區塊的 legacy 位置感
- Equipment List
  - equipment-to-simulation mapping 已補主流程，但 machine-group 對照說明文字仍可再更貼近 legacy
  - delta table 已補 qty / baseline / extra 粒度，但仍不是 legacy 全部欄位的逐欄複刻
- Space Setup
  - matrix mode 已有主體，但尚未做到 legacy 的 floor/process disabled 狀態細節
  - 40/60 split 已補控制，但視覺與 legacy 的提示文字仍可再收斂
- DLOH-L & IDL Setup
  - direct / indirect labor 的欄位已補主體，但 L10/L6 mode-specific 顯示細節仍可再微調

### 2. MPM Setup
- MPM Setup L10
  - 欄位次序已再收斂，但某些 derived result 與欄位群組標題仍可再更細對 legacy HTML
- MPM Setup L6
  - 已補 routing time 明細，但多段 header / by-product 的排版密度仍與 legacy 有些差距

### 3. Labor Time Estimation
- L10
  - sticky first-column 已補，但 station 計算欄位仍是 typed React 版本的近似呈現，不是逐像素複刻
- L6
  - 已改成 segment matrix 並補上多段管理
  - 仍可再補更貼近 legacy 的匯入分段規則、segment-level header 區塊與 disabled cell 狀態

### 4. Summary
- L10 / L6 summary 已達功能交付，但與 legacy 相比仍有兩類差異
  - 某些 cost category 的視覺 grouping 尚未逐表一比一複刻
  - Export MVA 的 legacy 風格按鈕與最終格式仍是現代化簡化版

### 5. 視覺與互動
- 整體已改回深色工作台方向，但仍不是 legacy HTML 的逐像素複刻
- 某些表格的字級、間距、sticky 行為與 badge 顏色仍可再收斂

## 建議優先順序

### P1
- Summary L10 / L6 的欄位順序與群組細節再細對 legacy
- MPM L10 / L6 的群組標題、derived result 顯示方式再細對 legacy
- Space Setup 的 disabled / helper text / matrix state 細節追平

### P2
- L6 segment matrix 的 import 分段規則與 header 區塊細化
- Equipment delta 與 export 流程再靠近 legacy 樣板欄位

### P3
- 視覺細節逐像素收斂
- 匯出按鈕與最終報表呈現再向 legacy 風格靠攏

## 手動比對方式
1. 左側依頁切換，對照 legacy HTML 的同名頁面。
2. 逐區檢查欄位名稱、群組標題、欄位順序、是否可編輯。
3. 對 labor / space / equipment / summary 類頁面檢查表格欄位順序、sticky 行為與深色主題一致性。
4. 對 L10 / L6 summary 檢查 confirm、capacity assumptions、labor tables、cost rollup 是否完整。