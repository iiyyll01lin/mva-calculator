# AI Optimization Agent — 深度解析

> 給軟體工程師的完整導讀：這個 AI Agent 是怎麼做的、為什麼這樣設計、每一層的職責在哪裡。

---

## 目錄

1. [系統全景圖](#1-系統全景圖)
2. [Data Layer — 型別設計](#2-data-layer--型別設計)
3. [Context Extraction — 資料脫敏](#3-context-extraction--資料脫敏)
4. [Hashing & Memoization — LRU-1 快取](#4-hashing--memoization--lru-1-快取)
5. [Prompt Engineering — 如何餵 LLM](#5-prompt-engineering--如何餵-llm)
6. [Mock AI Engine — 不靠 LLM 的分析邏輯](#6-mock-ai-engine--不靠-llm-的分析邏輯)
7. [Magic Wand — What-If 純函數計算](#7-magic-wand--what-if-純函數計算)
8. [UI 層：AiInsightsPanel](#8-ui-層aiinsightspanel)
9. [UI 層：SimulationPage 的 AI 區塊](#9-ui-層simulationpage-的-ai-區塊)
10. [測試策略](#10-測試策略)
11. [把 Mock 換成真實 LLM API 的步驟](#11-把-mock-換成真實-llm-api-的步驟)
12. [設計決策總結](#12-設計決策總結)

---

## 1. 系統全景圖

```
ProjectState  ──┐
                ├──► extractAiContext() ──► AiAgentContext
SimulationResults ─┘                            │
                                                ▼
                                        hashAiContext()
                                                │
                                     ┌──────────┴─────────┐
                                     │  cache hit?         │
                                    yes                    no
                                     │                     │
                              return cached         runMockAiAnalysis()
                              AiInsight                    │
                                                   ┌───────┴──────────┐
                                                   │                  │
                                          分析 bottleneck    computeMagicWand()
                                          & headcount              │
                                                   │         MagicWandSuggestion
                                                   └──► AiInsight (含 magicWand)
                                                              │
                                                    ┌─────────┴─────────┐
                                                    │                   │
                                          AiInsightsPanel        SimulationPage
                                          (side drawer)          (inline 區塊)
```

整個 AI Agent **完全不需要後端 server**，所有分析都在瀏覽器內用純 TypeScript 完成。  
`runMockAiAnalysis` 是暫時的本機引擎，接口設計已預留換成真實 LLM API 的路徑。

---

## 2. Data Layer — 型別設計

檔案：[src/domain/ai-agent.ts](../src/domain/ai-agent.ts)

整個 AI 子系統只定義了三個核心型別：

### `AiAgentContext` — 送給 AI 的「乾淨資料」

```typescript
export interface AiAgentContext {
  readonly modelName: string;
  readonly taktTime: number;
  readonly uph: number;
  readonly lineBalanceEfficiency: number;
  readonly weeklyOutput: number;
  readonly weeklyDemand: number;
  readonly capacityGap: number;           // weeklyOutput - weeklyDemand
  readonly bottleneckProcess: string;
  readonly stationSummary: ReadonlyArray<{
    readonly step: number;
    readonly process: string;
    readonly side: string;
    readonly cycleTime: number;
    readonly utilization: number;
    readonly componentCount: number;
  }>;
  readonly totalDirectHeadcount: number;
  readonly shiftsPerDay: number;
  readonly hoursPerShift: number;
  readonly workDaysPerWeek: number;
}
```

**為什麼單獨定義一個 Context 型別，而不直接傳 `ProjectState`？**

- `ProjectState` 裡有 `projectId`、`lastModified`、`confirmation`（含reviewer姓名）等個資欄位
- 建立一個受控的「AI 可見型別」，強迫所有傳出去的資料都要顯式地被列舉
- 即使未來接真實 LLM API，型別系統也會阻止誤傳敏感欄位

### `MagicWandSuggestion` — What-If 計算結果

```typescript
export interface MagicWandSuggestion {
  readonly targetProcess: string;
  readonly action: 'add_parallel_station'; // 用 literal type，未來可 union 擴充
  readonly currentCycleTime: number;
  readonly projectedCycleTime: number;     // currentCycleTime / 2
  readonly currentUph: number;
  readonly projectedUph: number;
  readonly uphGainPercent: number;
}
```

### `AiInsight` — 最終報告

```typescript
export interface AiInsight {
  readonly generatedFor: string;      // 使用者名稱
  readonly timestamp: string;         // ISO-8601
  readonly executiveSummary: string;  // Markdown 格式
  readonly topBottlenecks: string[];  // 最多 2 條
  readonly headcountActions: string[];
  readonly uphPrediction: string;
  readonly magicWand: MagicWandSuggestion | null;
  readonly contextHash: string;       // 快取鍵
}
```

全部欄位都是 `readonly`，確保 insight 物件產出後不可變動（immutable），這樣才能安全地做物件參考比較（`first === second`）。

---

## 3. Context Extraction — 資料脫敏

```typescript
export function extractAiContext(
  project: ProjectState,
  simulation: SimulationResults
): AiAgentContext {
  const { basicInfo, plant } = project;

  const totalDirectHeadcount = plant.directLaborRows.reduce(
    (sum, r) => sum + Math.max(0, Number(r.headcount) || 0),
    0,
  );

  return {
    modelName: basicInfo.modelName,
    taktTime: simulation.taktTime,
    uph: simulation.uph,
    // ... 只取製造參數
    stationSummary: simulation.steps.map((s) => ({
      step: s.step,
      process: s.process,
      // 注意：只取時間/計數，不取 machineDescription 等額外文字
      cycleTime: s.cycleTime,
      utilization: s.utilization,
      componentCount: s.componentCount,
    })),
  };
}
```

**關鍵設計：這個函數是 AI 系統的「閘門」**。

- 在這裡被排除的欄位：`projectId`、`lastModified`、`confirmation`（reviewer、decision）、`lineStandards`（含儀器price資訊）
- 只傳 numbers 和 process 名稱，不傳任何 free-text 的 user input

---

## 4. Hashing & Memoization — LRU-1 快取

每次分析前，先算一個 8 位 16 進位的 hash：

```typescript
export function hashAiContext(context: AiAgentContext): string {
  const payload = JSON.stringify({
    uph: context.uph,
    taktTime: context.taktTime,
    lineBalanceEfficiency: context.lineBalanceEfficiency,
    weeklyOutput: context.weeklyOutput,
    weeklyDemand: context.weeklyDemand,
    stationSummary: context.stationSummary,
    totalDirectHeadcount: context.totalDirectHeadcount,
  });

  let h = 5381;
  for (let i = 0; i < payload.length; i++) {
    h = (((h << 5) + h) ^ payload.charCodeAt(i)) >>> 0; // unsigned 32-bit djb2
  }
  return h.toString(16).padStart(8, '0');
}
```

### djb2 演算法

這是 1991 年 Daniel J. Bernstein 發明的字串 hash，非常適合這個場景：

```
h = 5381（魔法初始值，選這個是因為 5381 是質數）
for each char c:
    h = h * 33 XOR c
```

`(h << 5) + h` 就是 `h * 33`（位元運算比乘法快）。  
`>>> 0` 確保結果永遠是無號 32-bit 整數，避免 JavaScript 浮點數溢位。

### 快取實作（LRU-1）

```typescript
let _cachedHash = '';
let _cachedInsight: AiInsight | null = null;

export function runMockAiAnalysis(...): AiInsight {
  const hash = hashAiContext(ctx);
  if (hash === _cachedHash && _cachedInsight !== null) {
    return _cachedInsight; // ← 直接回傳，O(1)
  }
  // ... 執行分析
  _cachedHash = hash;
  _cachedInsight = insight;
  return insight;
}
```

這是最簡單的 LRU-1（只記住最後一次）。  
為什麼不用 `useMemo` 或 `Map`？

- `useMemo` 是 React hook，只能在 component 內使用；這個 cache 需要跨多個 component 共享（`AiInsightsPanel` 和 `SimulationPage` 都會呼叫）
- 模組層級的變數（module-level singleton）在整個 app 生命週期內只存在一份，天然達成共享

---

## 5. Prompt Engineering — 如何餵 LLM

```typescript
export function generateOptimizationPrompt(
  project: ProjectState,
  simulation: SimulationResults,
  displayName = 'Avery, Yeh',
): string {
  const ctx = extractAiContext(project, simulation);

  // 先把 station 資料格式化成一個易讀的文字表格
  const stationRows = ctx.stationSummary
    .map((s) =>
      `  Step ${s.step}: ${s.process} (${s.side}) | CT=${s.cycleTime.toFixed(2)}s` +
      ` | Util=${s.utilization.toFixed(1)}% | Components=${s.componentCount}`,
    )
    .join('\n');

  return `You are a Lean Six Sigma Black Belt...

## Line Configuration — ${ctx.modelName}
- Demand: ${ctx.weeklyDemand} units/week
...

## Station Cycle-Time Profile
${stationRows}

## Your Task
1. Identify the top 2 bottleneck stations...
2. Suggest specific headcount re-allocation...
3. Predict the UPH improvement, showing the arithmetic.

## Output Format
### Bottleneck Analysis
### Headcount Re-Allocation Recommendations
### Projected UPH Improvement
`;
}
```

### Prompt 設計的三個重點

**1. 角色設定（Role Prompting）**  
> "You are a Lean Six Sigma Black Belt and Manufacturing IE Expert"

給 LLM 一個具體的專家人格，它的回答風格和用詞會因此收斂到製造業領域。

**2. 結構化資料注入**  
把 `stationSummary` 格式化成類似 CSV 的文字表格注入 prompt。LLM 處理這種規則的符號格式比自然語言描述更準確。

**3. 強制輸出格式**  
用 `## Output Format` 區塊明確要求三個 Markdown section，這樣前端 parser 才能可靠地解析結果。

---

## 6. Mock AI Engine — 不靠 LLM 的分析邏輯

`runMockAiAnalysis` 用純 TypeScript 實現了一個「假的 AI」，邏輯完全透明可測試：

```typescript
export function runMockAiAnalysis(
  project, simulation, displayName = 'Avery, Yeh'
): AiInsight {

  // Step 1: 取出 context，算 hash
  const ctx = extractAiContext(project, simulation);
  const hash = hashAiContext(ctx);
  if (hash === _cachedHash && _cachedInsight !== null) return _cachedInsight;

  // Step 2: 找 Top-2 Bottleneck（排序後取前兩名）
  const ranked = [...simulation.steps].sort((a, b) => b.cycleTime - a.cycleTime);
  const [first, second] = ranked;

  // Step 3: 找低使用率的站（可能可以轉人力給 bottleneck）
  const lowUtil = simulation.steps.find(
    (s) => !s.isBottleneck && s.utilization < 70
  );

  // Step 4: 組出 headcountActions
  // 如果找到低利用率的站 → 建議 reassign
  // 如果 capacityGap < 0 → 建議加班或三班

  // Step 5: 組出 executiveSummary（用樣板字串）
  // Step 6: computeMagicWand() 算出 UPH 預測
  // Step 7: 快取結果並回傳
}
```

**為什麼要建 Mock Engine 而不直接接 API？**

| | Mock Engine | 真實 LLM API |
|---|---|---|
| 部署依賴 | 零 | 需要 API key、網路 |
| 回應速度 | 即時 | 1–5 秒 |
| 測試難度 | 完全可 unit test | 需要 mock fetch |
| 成本 | 免費 | 按 token 計費 |
| 一致性 | 100% 確定性 | 非確定性，每次不同 |

Mock engine 讓你在沒有 API key 的情況下完整開發和測試整個功能。等到接真實 API 時，只需要替換 `runMockAiAnalysis` 這一個函數。

---

## 7. Magic Wand — What-If 純函數計算

Magic Wand 問的是：**「如果在 bottleneck 站加一台同款設備（parallel station），UPH 會變多少？」**

### 數學模型

```
平行站的效果 = 把 bottleneck 的 cycle time 除以 2

舉例：
  原本：Printer CT = 8.5s，Placement CT = 6.2s
  Bottleneck = Printer（CT最大）
  UPH = 3600 / 8.5 = 423.5

加一台 Printer 後：
  Printer 有效 CT = 8.5 / 2 = 4.25s（兩台輪流，等效 CT 砍半）
  新 bottleneck = Placement（CT = 6.2s）
  新 UPH = 3600 / 6.2 = 580.6
  UPH 提升 = (580.6 - 423.5) / 423.5 = +37.1%
```

```typescript
export function computeMagicWand(simulation: SimulationResults): MagicWandSuggestion | null {
  const bottleneck = simulation.steps.find((s) => s.isBottleneck);
  if (!bottleneck || simulation.steps.length < 2) return null;

  const projectedCycleTime = parseFloat((bottleneck.cycleTime / 2).toFixed(4));

  // 新的 max CT 是：把 bottleneck 換成 CT/2 後，整條線的最大值
  const newMaxCt = Math.max(
    ...simulation.steps.map((s) => (s.isBottleneck ? projectedCycleTime : s.cycleTime)),
  );

  const projectedUph = newMaxCt > 0 ? parseFloat((3600 / newMaxCt).toFixed(2)) : 0;
  const uphGainPercent = parseFloat(
    (((projectedUph - simulation.uph) / simulation.uph) * 100).toFixed(1)
  );

  return { /* ... */ };
}
```

### `applyMagicWandPreview` — 產生完整的 What-If SimulationResults

```typescript
export function applyMagicWandPreview(
  simulation: SimulationResults,
  suggestion: MagicWandSuggestion,
): SimulationResults {
  // Step 1: 把 bottleneck 的 cycleTime 換成 suggestion.projectedCycleTime
  const modifiedSteps = simulation.steps.map((s) =>
    s.isBottleneck
      ? { ...s, cycleTime: suggestion.projectedCycleTime }
      : { ...s }
  );

  // Step 2: 重新算 max CT、utilization、LineBalanceEfficiency
  const newMaxCt = Math.max(...modifiedSteps.map((s) => s.cycleTime), 0);
  const recalcedSteps = modifiedSteps.map((s) => ({
    ...s,
    isBottleneck: s.cycleTime === newMaxCt,
    utilization: (s.cycleTime / newMaxCt) * 100,
  }));

  // Step 3: 回傳全新的 SimulationResults（不 mutate 原本的）
  return {
    taktTime: simulation.taktTime,  // takt 不變（需求沒變）
    uph: 3600 / newMaxCt,
    lineBalanceEfficiency: (totalCt / (newMaxCt * steps.length)) * 100,
    // ...
  };
}
```

**Pure function 設計的好處：**
1. 不 mutate 原始 simulation → UI 的 `simulation` prop 永遠是真實數據
2. 完全可以 unit test，輸入確定輸出就確定
3. React 的 `useMemo` 可以正確地偵測到參數改變並重新計算

---

## 8. UI 層：AiInsightsPanel

檔案：[src/features/AiInsightsPanel.tsx](../src/features/AiInsightsPanel.tsx)

### 狀態機

```
           用戶點 Sidebar 的 ✨ 按鈕
                    │
                    ▼
           isOpen = true
                    │
        ┌───────────┤
        │           │
    insight=null  insight!=null
    isLoading=false   isLoading=false
        │           │
  [空白提示畫面]  [顯示報告]
        │
  用戶點 "Analyze Now"
        │
   isLoading=true
        │
   setTimeout 700ms (模擬非同步)
        │
   runMockAiAnalysis()
        │
   setInsight(result)
   isLoading=false
        │
    [顯示報告]
```

```typescript
const handleAnalyze = useCallback(() => {
  setIsLoading(true);
  // 700ms 延遲：讓用戶感受到「有在計算」，體驗類似真實 LLM 回應
  setTimeout(() => {
    const result = runMockAiAnalysis(project, simulation);
    setInsight(result);
    setIsLoading(false);
  }, 700);
}, [project, simulation]);
```

### 輕量 Markdown Renderer

為了避免引入 `marked` 或 `react-markdown` 等外部套件，用兩個小 component 完成最常用的格式：

```tsx
// 把 **text** 轉成 <strong>text</strong>
function BoldText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('**') && part.endsWith('**')
          ? <strong key={i}>{part.slice(2, -2)}</strong>
          : <span key={i}>{part}</span>
      )}
    </>
  );
}

// 把 \n\n 分段，每段用 <p> 包起來
function MarkdownParagraphs({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n\n+/).map((para, i) => (
        <p key={i}><BoldText text={para} /></p>
      ))}
    </>
  );
}
```

**為什麼不用 `dangerouslySetInnerHTML`？**  
因為 `insight` 內容未來可能來自 LLM API（外部輸入），直接插入 HTML 有 XSS 風險。用 React component 渲染，HTML 被 React 安全地 escape。

---

## 9. UI 層：SimulationPage 的 AI 區塊

檔案：[src/features/SimulationPage.tsx](../src/features/SimulationPage.tsx)

```tsx
// 用 useMemo 確保只在 simulation 改變時重新計算
const magicWand = useMemo(() => computeMagicWand(simulation), [simulation]);
const whatIfSimulation = useMemo(
  () => (whatIfActive && magicWand ? applyMagicWandPreview(simulation, magicWand) : null),
  [whatIfActive, magicWand, simulation],
);
```

**Lazy evaluation（惰性求值）**：`whatIfSimulation` 只有在用戶按下 Magic Wand 按鈕後（`whatIfActive = true`）才會真正計算，避免每次 render 都執行不必要的計算。

```tsx
{/* 只在 magicWand 存在（有 bottleneck 且步驟 >= 2）時顯示 */}
{magicWand && (
  <SectionCard title="✨ AI Recommended Adjustments">
    {/* 條件渲染 What-If 預覽 */}
    {whatIfActive && whatIfSimulation && (
      <div className="ai-whatif-preview">
        {/* 顯示 UPH before/after、LBE、新 bottleneck */}
      </div>
    )}
  </SectionCard>
)}
```

---

## 10. 測試策略

檔案：[tests/unit/ai-agent.spec.ts](../tests/unit/ai-agent.spec.ts)（40 個測試）

測試分為六個 describe 群組：

### `extractAiContext` — 資料正確性 + 資料脫敏

```typescript
it('does NOT expose any PII or financial credentials fields', () => {
  const ctx = extractAiContext(project, sim) as unknown as Record<string, unknown>;
  // 確保這些 key 根本不在 context 物件裡
  ['projectId', 'lastModified', 'confirmation', 'lineStandards'].forEach(key => {
    expect(ctx[key]).toBeUndefined();
  });
});
```

### `hashAiContext` — Hash 的正確性

```typescript
it('is deterministic', () => {
  expect(hashAiContext(ctx)).toBe(hashAiContext(ctx)); // 同樣輸入必須同樣輸出
});

it('changes when UPH changes', () => {
  expect(hashAiContext(ctx)).not.toBe(hashAiContext({ ...ctx, uph: ctx.uph + 1 }));
});
```

### `generateOptimizationPrompt` — Prompt 數學準確性

這是最重要的測試群組，確保送給 LLM 的數字和實際計算結果一致：

```typescript
it('contains the exact UPH value from simulation', () => {
  const prompt = generateOptimizationPrompt(project, sim);
  expect(prompt).toContain(sim.uph.toFixed(2)); // 必須是原始計算值，不能是四捨五入後的近似值
});

it('contains a station row for every process step', () => {
  for (const step of sim.steps) {
    expect(prompt).toContain(step.process); // 每一個製程站都必須出現在 prompt 裡
  }
});
```

### `computeMagicWand` — What-If 數學

```typescript
it('projectedCycleTime is exactly half of the bottleneck cycleTime', () => {
  expect(suggestion.projectedCycleTime).toBeCloseTo(bottleneck.cycleTime / 2, 3);
});

it('projectedUph > currentUph', () => {
  expect(suggestion.projectedUph).toBeGreaterThan(suggestion.currentUph);
});
```

### `applyMagicWandPreview` — Pure Function 驗證

```typescript
it('does not mutate the original simulation', () => {
  const originalUph = sim.uph;
  applyMagicWandPreview(sim, suggestion); // 呼叫但不使用回傳值
  expect(sim.uph).toBe(originalUph);       // 原始值必須不變
});
```

### `runMockAiAnalysis` — 快取行為

```typescript
beforeEach(() => {
  clearAiCache(); // 每個 test 前清除快取，確保隔離
});

it('memoizes — second call returns identical object reference', () => {
  const first = runMockAiAnalysis(project, sim);
  const second = runMockAiAnalysis(project, sim);
  expect(first).toBe(second); // 物件參考相等，不是值相等
});

it('cache is invalidated when simulation data changes', () => {
  const first = runMockAiAnalysis(project, sim);
  const second = runMockAiAnalysis(altProject, sim); // 不同 project
  expect(first).not.toBe(second);
  expect(first.contextHash).not.toBe(second.contextHash);
});
```

---

## 11. 把 Mock 換成真實 LLM API 的步驟

目前所有 AI 邏輯都在 `runMockAiAnalysis`。換成真實 API 只需要替換這個函數：

### 方案 A — 直接呼叫 OpenAI / Anthropic（前端）

```typescript
// 替換 runMockAiAnalysis 的實作
export async function runAiAnalysis(
  project: ProjectState,
  simulation: SimulationResults,
  displayName = 'Avery, Yeh',
  apiKey: string,  // 從環境變數或用戶輸入取得，不要 hardcode
): Promise<AiInsight> {
  const ctx = extractAiContext(project, simulation);
  const hash = hashAiContext(ctx);
  if (hash === _cachedHash && _cachedInsight !== null) return _cachedInsight;

  const prompt = generateOptimizationPrompt(project, simulation, displayName);

  // 只是呼叫 API，回傳格式整理跟之前一樣
  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [{ role: 'user', content: prompt }],
      max_tokens: 1024,
    }),
  });

  const data = await response.json();
  const text: string = data.choices[0].message.content;

  // 把 LLM 的 Markdown 文字解析成 AiInsight 結構
  const insight: AiInsight = parseInsightFromMarkdown(text, displayName, hash);
  _cachedHash = hash;
  _cachedInsight = insight;
  return insight;
}
```

### 方案 B — 透過自己的 Backend（推薦，安全）

```
前端 ──► POST /api/ai-analyze ──► 你的 Node/Python server ──► LLM API
         { context: AiAgentContext }        (API key 在 server 端，不暴露給瀏覽器)
```

前端只需要：
```typescript
const response = await fetch('/api/ai-analyze', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(extractAiContext(project, simulation)), // 只送 AiAgentContext
});
const insight: AiInsight = await response.json();
```

### AiInsightsPanel 對應的修改

只需要把 `handleAnalyze` 改成 `async`：

```typescript
const handleAnalyze = useCallback(async () => {
  setIsLoading(true);
  try {
    const result = await runAiAnalysis(project, simulation); // async 版本
    setInsight(result);
  } catch (err) {
    setError('分析失敗，請稍後重試');
  } finally {
    setIsLoading(false);
  }
}, [project, simulation]);
```

其餘 UI 完全不需要改動。

---

## 12. 設計決策總結

| 決策 | 做法 | 理由 |
|---|---|---|
| **資料脫敏** | `extractAiContext` 白名單機制 | 型別系統強制執行，防止未來開發者誤傳敏感欄位 |
| **快取** | Module-level LRU-1 + djb2 hash | 跨 component 共享，無需 React context；djb2 快速且碰撞率低 |
| **Pure function** | `applyMagicWandPreview` 不 mutate | 可安全用於 `useMemo`，且 unit test 容易驗證 |
| **Mock engine** | Local TypeScript 邏輯 | 開發環境零依賴，40 個 unit test 完全確定性 |
| **Markdown renderer** | 自製 BoldText + MarkdownParagraphs | 避免 XSS；不引入額外套件；只需支援 LLM 常用的 `**bold**` 和段落 |
| **`displayName` 參數** | 函數參數而非全域 | 可測試性；未來支援多用戶只需改呼叫方式 |
| **`readonly` 型別** | 所有 interface 欄位都是 `readonly` | 確保 insight 物件不可變，物件參考比較（快取 hit 判斷）可靠 |
