/**
 * AI Optimization Agent — domain service for bottleneck analysis and IE recommendations.
 *
 * Data privacy: only non-sensitive manufacturing parameters (cycle times, counts, rates,
 * headcount) are extracted. No PII, financial credentials, or proprietary reference
 * numbers are included in the AI context payload.
 *
 * Caching: results are memoized by a djb2-style hash of the relevant context so that
 * unchanged data never triggers a redundant (or expensive) LLM call.
 */

import type { ProjectState, SimulationResults, SimulationStepResult } from './models';

// ── Exported types ─────────────────────────────────────────────────────────

/** Non-sensitive manufacturing context that may be sent to an AI layer. */
export interface AiAgentContext {
  readonly modelName: string;
  readonly taktTime: number;
  readonly uph: number;
  readonly lineBalanceEfficiency: number;
  readonly weeklyOutput: number;
  readonly weeklyDemand: number;
  /** Positive = surplus, negative = shortfall. */
  readonly capacityGap: number;
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

/** Describes the "add parallel station" what-if scenario for the primary bottleneck. */
export interface MagicWandSuggestion {
  readonly targetProcess: string;
  readonly targetStep: number;
  readonly action: 'add_parallel_station';
  readonly description: string;
  readonly currentCycleTime: number;
  /** Projected CT after adding one parallel station (≈ currentCycleTime / 2). */
  readonly projectedCycleTime: number;
  readonly currentUph: number;
  readonly projectedUph: number;
  readonly uphGainPercent: number;
}

/** Full AI insight report addressed to the active user. */
export interface AiInsight {
  readonly generatedFor: string;
  readonly timestamp: string;
  /** Markdown-formatted executive summary paragraph. */
  readonly executiveSummary: string;
  /** Two-item list of ranked bottleneck descriptions (Markdown). */
  readonly topBottlenecks: string[];
  /** Concrete headcount re-allocation actions. */
  readonly headcountActions: string[];
  /** Narrative UPH improvement prediction (Markdown). */
  readonly uphPrediction: string;
  readonly magicWand: MagicWandSuggestion | null;
  /** Hash of the input context — used for cache invalidation. */
  readonly contextHash: string;
}

// ── Context extraction ─────────────────────────────────────────────────────

/**
 * Extracts the non-sensitive manufacturing subset of ProjectState + SimulationResults.
 * Only timing, counts, and capacity figures are preserved.
 */
export function extractAiContext(project: ProjectState, simulation: SimulationResults): AiAgentContext {
  const { basicInfo, plant } = project;
  const totalDirectHeadcount = plant.directLaborRows.reduce(
    (sum, r) => sum + Math.max(0, Number(r.headcount) || 0),
    0,
  );
  return {
    modelName: basicInfo.modelName,
    taktTime: simulation.taktTime,
    uph: simulation.uph,
    lineBalanceEfficiency: simulation.lineBalanceEfficiency,
    weeklyOutput: simulation.weeklyOutput,
    weeklyDemand: basicInfo.demandWeekly,
    capacityGap: simulation.weeklyOutput - basicInfo.demandWeekly,
    bottleneckProcess: simulation.bottleneckProcess,
    stationSummary: simulation.steps.map((s) => ({
      step: s.step,
      process: s.process,
      side: s.side,
      cycleTime: s.cycleTime,
      utilization: s.utilization,
      componentCount: s.componentCount,
    })),
    totalDirectHeadcount,
    shiftsPerDay: basicInfo.shiftsPerDay,
    hoursPerShift: basicInfo.hoursPerShift,
    workDaysPerWeek: basicInfo.workDays,
  };
}

// ── Hashing / memoization ─────────────────────────────────────────────────

/**
 * Deterministic djb2-style hash of the AI-relevant subset of context.
 * Used to decide whether the cached insight is still valid.
 */
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
    h = (((h << 5) + h) ^ payload.charCodeAt(i)) >>> 0; // unsigned 32-bit
  }
  return h.toString(16).padStart(8, '0');
}

// ── Prompt generator ───────────────────────────────────────────────────────

/**
 * Builds a high-quality LLM system prompt from the manufacturing context.
 * The prompt instructs the model to:
 *   1. Identify the top 2 bottlenecks.
 *   2. Suggest specific headcount re-allocation.
 *   3. Predict the UPH improvement with a calculation.
 */
export function generateOptimizationPrompt(
  project: ProjectState,
  simulation: SimulationResults,
  displayName = 'Avery, Yeh',
): string {
  const ctx = extractAiContext(project, simulation);

  const stationRows = ctx.stationSummary
    .map(
      (s) =>
        `  Step ${s.step}: ${s.process} (${s.side}) | CT=${s.cycleTime.toFixed(2)}s` +
        ` | Util=${s.utilization.toFixed(1)}% | Components=${s.componentCount}`,
    )
    .join('\n');

  return `You are a Lean Six Sigma Black Belt and Manufacturing IE Expert advising ${displayName} at a world-class EMS facility.

## Line Configuration — ${ctx.modelName}
- Demand: ${ctx.weeklyDemand} units/week
- Takt Time: ${ctx.taktTime.toFixed(2)} s/unit
- Current UPH: ${ctx.uph.toFixed(2)}
- Weekly Output Capacity: ${ctx.weeklyOutput.toFixed(0)} units (${ctx.capacityGap >= 0 ? '+' : ''}${ctx.capacityGap.toFixed(0)} vs demand)
- Line Balance Efficiency: ${ctx.lineBalanceEfficiency.toFixed(1)}%
- Bottleneck Station: ${ctx.bottleneckProcess}
- Shifts: ${ctx.shiftsPerDay}/day × ${ctx.hoursPerShift} h, ${ctx.workDaysPerWeek} days/week
- Direct Headcount: ${ctx.totalDirectHeadcount}

## Station Cycle-Time Profile
${stationRows}

## Your Task
1. Identify the top 2 bottleneck stations ranked by cycle time and utilization.
2. Suggest specific, actionable headcount re-allocation steps using exact station names and operator counts (e.g., "Move 1 operator from Station X to Station Y to reduce manual handling time by ~15%").
3. Predict the UPH improvement achievable with your changes, showing the arithmetic.

## Output Format
Reply in Markdown with three sections:
### Bottleneck Analysis
### Headcount Re-Allocation Recommendations
### Projected UPH Improvement

Address the report directly to ${displayName}. Be concise, data-driven, and reference exact numbers from the table above.`;
}

// ── Magic Wand computation ─────────────────────────────────────────────────

/**
 * Computes the "add one parallel station" what-if scenario for the primary bottleneck.
 * A parallel station halves the effective cycle time of that process step.
 * Returns null if the line has fewer than two steps (nothing to rebalance).
 */
export function computeMagicWand(simulation: SimulationResults): MagicWandSuggestion | null {
  const bottleneck = simulation.steps.find((s) => s.isBottleneck);
  if (!bottleneck || simulation.steps.length < 2) return null;

  const projectedCycleTime = parseFloat((bottleneck.cycleTime / 2).toFixed(4));

  // New bottleneck is the maximum CT across all steps with the parallel station applied
  const newMaxCt = Math.max(
    ...simulation.steps.map((s) => (s.isBottleneck ? projectedCycleTime : s.cycleTime)),
  );
  const projectedUph = newMaxCt > 0 ? parseFloat((3600 / newMaxCt).toFixed(2)) : 0;
  const uphGainPercent =
    simulation.uph > 0
      ? parseFloat((((projectedUph - simulation.uph) / simulation.uph) * 100).toFixed(1))
      : 0;

  return {
    targetProcess: bottleneck.process,
    targetStep: bottleneck.step,
    action: 'add_parallel_station',
    description:
      `Add 1 parallel station at **${bottleneck.process}** (${bottleneck.side}): ` +
      `effective CT ${bottleneck.cycleTime.toFixed(2)} s → ${projectedCycleTime.toFixed(2)} s.`,
    currentCycleTime: bottleneck.cycleTime,
    projectedCycleTime,
    currentUph: simulation.uph,
    projectedUph,
    uphGainPercent,
  };
}

/**
 * Returns a modified SimulationResults representing the what-if state after
 * applying the Magic Wand suggestion (parallel station on the bottleneck).
 * Pure function — does not mutate the original simulation.
 */
export function applyMagicWandPreview(
  simulation: SimulationResults,
  suggestion: MagicWandSuggestion,
): SimulationResults {
  const modifiedSteps: SimulationStepResult[] = simulation.steps.map((s) =>
    s.isBottleneck ? { ...s, cycleTime: suggestion.projectedCycleTime, rawCycleTime: s.rawCycleTime } : { ...s },
  );

  const newMaxCt = Math.max(...modifiedSteps.map((s) => s.cycleTime), 0);
  const totalCt = modifiedSteps.reduce((sum, s) => sum + s.cycleTime, 0);

  const recalcedSteps: SimulationStepResult[] = modifiedSteps.map((s) => ({
    ...s,
    isBottleneck: newMaxCt > 0 && s.cycleTime === newMaxCt,
    utilization: newMaxCt > 0 ? parseFloat(((s.cycleTime / newMaxCt) * 100).toFixed(4)) : 0,
  }));

  const newUph = newMaxCt > 0 ? parseFloat((3600 / newMaxCt).toFixed(4)) : 0;
  const newLbe =
    newMaxCt > 0 && recalcedSteps.length > 0
      ? parseFloat(((totalCt / (newMaxCt * recalcedSteps.length)) * 100).toFixed(4))
      : 0;

  const availSecsPerWeek = simulation.taktTime > 0 ? simulation.taktTime * simulation.weeklyOutput / (simulation.uph > 0 ? simulation.uph * simulation.taktTime / 3600 : 1) : 0;
  // Re-use original weeklyOutput scaling ratio
  const weeklyScale = simulation.uph > 0 ? simulation.weeklyOutput / simulation.uph : 0;
  const newWeeklyOutput = parseFloat((newUph * weeklyScale).toFixed(4));

  const newBottleneck = recalcedSteps.find((s) => s.isBottleneck)?.process ?? 'N/A';

  return {
    taktTime: simulation.taktTime,
    uph: newUph,
    lineBalanceEfficiency: newLbe,
    weeklyOutput: newWeeklyOutput,
    bottleneckProcess: newBottleneck,
    steps: recalcedSteps,
  };
}

// ── Mock AI engine with LRU-1 cache ───────────────────────────────────────

let _cachedHash = '';
let _cachedInsight: AiInsight | null = null;

/**
 * Runs the mock AI analysis engine. In production this would be replaced with an
 * async call to an LLM API endpoint. Results are memoized by `contextHash` so
 * identical project states never re-run the engine.
 */
export function runMockAiAnalysis(
  project: ProjectState,
  simulation: SimulationResults,
  displayName = 'Avery, Yeh',
): AiInsight {
  const ctx = extractAiContext(project, simulation);
  const hash = hashAiContext(ctx);
  if (hash === _cachedHash && _cachedInsight !== null) return _cachedInsight;

  // Rank stations by cycle time descending to find top-2 constraints
  const ranked = [...simulation.steps].sort((a, b) => b.cycleTime - a.cycleTime);
  const [first, second] = ranked;

  const topBottlenecks: string[] = [];
  if (first) {
    topBottlenecks.push(
      `**${first.process} (${first.side}) — Step ${first.step}**: CT ${first.cycleTime.toFixed(2)} s, ` +
        `utilization ${first.utilization.toFixed(1)}%. This station gates the entire line at ${ctx.uph.toFixed(2)} UPH.`,
    );
  }
  if (second && second !== first) {
    topBottlenecks.push(
      `**${second.process} (${second.side}) — Step ${second.step}**: CT ${second.cycleTime.toFixed(2)} s, ` +
        `utilization ${second.utilization.toFixed(1)}%. Secondary constraint — resolving the primary bottleneck will expose this as the next gating station.`,
    );
  }

  // Headcount recommendations
  const headcountActions: string[] = [];
  const hasShortfall = ctx.capacityGap < 0;

  if (first && ctx.totalDirectHeadcount > 0) {
    // Find any station whose utilization is meaningfully below the bottleneck
    const lowUtil = simulation.steps.find(
      (s) => !s.isBottleneck && s.utilization < 70 && s.process !== first.process,
    );
    if (lowUtil) {
      headcountActions.push(
        `Reassign 1 operator from **${lowUtil.process}** (${lowUtil.utilization.toFixed(1)}% utilization) ` +
          `to support manual tasks adjacent to **${first.process}** — estimated CT reduction: 8–12%.`,
      );
    } else {
      headcountActions.push(
        `All stations are heavily loaded. Evaluate whether **${first.process}** has any manual-assist tasks ` +
          `that can be redistributed to reduce effective cycle time.`,
      );
    }
  }

  if (hasShortfall) {
    const shortfallPct = Math.abs((ctx.capacityGap / Math.max(ctx.weeklyDemand, 1)) * 100).toFixed(1);
    headcountActions.push(
      `Weekly shortfall: **${Math.abs(ctx.capacityGap).toFixed(0)} units** (${shortfallPct}% below demand). ` +
        `Consider approving 1 overtime shift or adding a 3rd shift to close the gap in the short term.`,
    );
  } else {
    headcountActions.push(
      `Line meets demand with a **+${ctx.capacityGap.toFixed(0)} unit** buffer. ` +
        `Use the surplus capacity for cross-training and preventive maintenance without impacting KPIs.`,
    );
  }

  // UPH prediction
  const magicWand = computeMagicWand(simulation);
  let uphPrediction = `Current UPH: **${ctx.uph.toFixed(2)}**. `;
  if (magicWand) {
    uphPrediction +=
      `Adding a parallel station at **${magicWand.targetProcess}** would raise UPH to ` +
      `**${magicWand.projectedUph.toFixed(2)}** (+${magicWand.uphGainPercent}%) by halving the bottleneck CT ` +
      `from ${magicWand.currentCycleTime.toFixed(2)} s to ${magicWand.projectedCycleTime.toFixed(2)} s. ` +
      `The next bottleneck would then be at CT ${(3600 / magicWand.projectedUph).toFixed(2)} s — ` +
      `verify that station also has capacity before investing in the parallel unit.`;
  } else {
    uphPrediction +=
      `The line is either single-step or already well balanced. ` +
      `Focus on OEE improvement and setup-time reduction for further UPH gains.`;
  }

  // Executive summary
  const lbe = ctx.lineBalanceEfficiency;
  const lbeQual = lbe >= 85 ? 'excellent' : lbe >= 70 ? 'moderate (room to improve)' : 'low — significant imbalance';
  const executiveSummary =
    `**${displayName} — AI Optimization Report**\n\n` +
    `Line **${ctx.modelName}** is running at **${ctx.uph.toFixed(2)} UPH** against a takt of **${ctx.taktTime.toFixed(2)} s**. ` +
    `Line balance efficiency is **${lbe.toFixed(1)}%** (${lbeQual}). ` +
    `Weekly output is **${ctx.weeklyOutput.toFixed(0)}** units vs demand of **${ctx.weeklyDemand}** — ` +
    (ctx.capacityGap >= 0
      ? `✅ on target with a ${ctx.capacityGap.toFixed(0)}-unit buffer.`
      : `⚠️ ${Math.abs(ctx.capacityGap).toFixed(0)} units below target.`);

  const insight: AiInsight = {
    generatedFor: displayName,
    timestamp: new Date().toISOString(),
    executiveSummary,
    topBottlenecks,
    headcountActions,
    uphPrediction,
    magicWand,
    contextHash: hash,
  };

  _cachedHash = hash;
  _cachedInsight = insight;
  return insight;
}

/** Clears the memoization cache (useful for testing). */
export function clearAiCache(): void {
  _cachedHash = '';
  _cachedInsight = null;
}
