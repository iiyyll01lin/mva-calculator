import { describe, expect, it, beforeEach } from 'vitest';
import {
  applyMagicWandPreview,
  clearAiCache,
  computeMagicWand,
  extractAiContext,
  generateOptimizationPrompt,
  hashAiContext,
  runMockAiAnalysis,
} from '../../src/domain/ai-agent';
import { calculateSimulation } from '../../src/domain/calculations';
import { defaultProject } from '../../src/domain/defaults';
import type { ProjectState, SimulationResults } from '../../src/domain/models';

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeBalancedSimulation(): SimulationResults {
  // Override the default project to have two process steps with identical CTs
  // so that lineBalanceEfficiency is 100%.
  const project: ProjectState = {
    ...defaultProject,
    processSteps: [
      { step: 1, process: 'Printer', side: 'Top' },
      { step: 2, process: 'Printer', side: 'Bottom' },
    ],
  };
  return calculateSimulation(project);
}

// ─── extractAiContext ─────────────────────────────────────────────────────────

describe('extractAiContext', () => {
  it('produces the correct weekly demand from basicInfo', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    expect(ctx.weeklyDemand).toBe(defaultProject.basicInfo.demandWeekly);
  });

  it('computes capacityGap as weeklyOutput - weeklyDemand', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    expect(ctx.capacityGap).toBeCloseTo(sim.weeklyOutput - defaultProject.basicInfo.demandWeekly, 4);
  });

  it('does NOT expose any PII or financial credentials fields', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim) as unknown as Record<string, unknown>;
    // These fields from ProjectState must not pass through to the AI context
    const forbiddenKeys = ['projectId', 'lastModified', 'confirmation', 'lineStandards'];
    for (const key of forbiddenKeys) {
      expect(ctx[key]).toBeUndefined();
    }
  });

  it('stationSummary length matches simulation.steps length', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    expect(ctx.stationSummary.length).toBe(sim.steps.length);
  });

  it('stationSummary cycle times match simulation step cycle times exactly', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    sim.steps.forEach((step, i) => {
      expect(ctx.stationSummary[i].cycleTime).toBe(step.cycleTime);
      expect(ctx.stationSummary[i].utilization).toBe(step.utilization);
      expect(ctx.stationSummary[i].componentCount).toBe(step.componentCount);
    });
  });

  it('totalDirectHeadcount sums all directLaborRows headcount values', () => {
    const sim = calculateSimulation(defaultProject);
    const expected = defaultProject.plant.directLaborRows.reduce(
      (sum, r) => sum + Math.max(0, Number(r.headcount) || 0),
      0,
    );
    const ctx = extractAiContext(defaultProject, sim);
    expect(ctx.totalDirectHeadcount).toBe(expected);
  });
});

// ─── hashAiContext ────────────────────────────────────────────────────────────

describe('hashAiContext', () => {
  it('returns an 8-character hex string', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    const hash = hashAiContext(ctx);
    expect(hash).toMatch(/^[0-9a-f]{8}$/);
  });

  it('is deterministic — same context produces the same hash', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    expect(hashAiContext(ctx)).toBe(hashAiContext(ctx));
  });

  it('changes when UPH changes', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    const hashA = hashAiContext(ctx);
    const hashB = hashAiContext({ ...ctx, uph: ctx.uph + 1 });
    expect(hashA).not.toBe(hashB);
  });

  it('changes when weeklyDemand changes', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    const hashA = hashAiContext(ctx);
    const hashB = hashAiContext({ ...ctx, weeklyDemand: ctx.weeklyDemand + 100 });
    expect(hashA).not.toBe(hashB);
  });
});

// ─── generateOptimizationPrompt ───────────────────────────────────────────────

describe('generateOptimizationPrompt', () => {
  it('contains the model name from basicInfo', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(defaultProject.basicInfo.modelName);
  });

  it('contains the exact UPH value from simulation', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    // UPH appears as a 2-decimal fixed number inside the prompt
    expect(prompt).toContain(sim.uph.toFixed(2));
  });

  it('contains the exact takt time from simulation', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(sim.taktTime.toFixed(2));
  });

  it('contains the exact weekly demand from basicInfo', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(String(defaultProject.basicInfo.demandWeekly));
  });

  it('contains the bottleneck process name', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(sim.bottleneckProcess);
  });

  it('contains a station row for every process step', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    for (const step of sim.steps) {
      expect(prompt).toContain(step.process);
    }
  });

  it('addresses output to "Avery, Yeh"', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain('Avery, Yeh');
  });

  it('includes the three required Markdown sections', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain('### Bottleneck Analysis');
    expect(prompt).toContain('### Headcount Re-Allocation Recommendations');
    expect(prompt).toContain('### Projected UPH Improvement');
  });

  it('weekly output figure in prompt matches simulation value', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(sim.weeklyOutput.toFixed(0));
  });

  it('line balance efficiency figure in prompt matches simulation value', () => {
    const sim = calculateSimulation(defaultProject);
    const prompt = generateOptimizationPrompt(defaultProject, sim);
    expect(prompt).toContain(sim.lineBalanceEfficiency.toFixed(1));
  });
});

// ─── computeMagicWand ─────────────────────────────────────────────────────────

describe('computeMagicWand', () => {
  it('returns null for an empty steps simulation', () => {
    const sim = calculateSimulation({ ...defaultProject, processSteps: [] });
    expect(computeMagicWand(sim)).toBeNull();
  });

  it('returns null for a single-step line (nothing to rebalance)', () => {
    const sim = calculateSimulation({
      ...defaultProject,
      processSteps: [{ step: 1, process: 'Printer', side: 'Top' }],
    });
    expect(computeMagicWand(sim)).toBeNull();
  });

  it('targets the bottleneck station', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return; // skip if defaultProject has < 2 steps
    expect(suggestion.targetProcess).toBe(sim.bottleneckProcess);
  });

  it('projectedCycleTime is exactly half of the bottleneck cycleTime', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const bottleneck = sim.steps.find((s) => s.isBottleneck)!;
    expect(suggestion.projectedCycleTime).toBeCloseTo(bottleneck.cycleTime / 2, 3);
  });

  it('projectedUph > currentUph when bottleneck is the sole constraint', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    expect(suggestion.projectedUph).toBeGreaterThan(suggestion.currentUph);
  });

  it('uphGainPercent is positive', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    expect(suggestion.uphGainPercent).toBeGreaterThan(0);
  });
});

// ─── applyMagicWandPreview ────────────────────────────────────────────────────

describe('applyMagicWandPreview', () => {
  it('does not mutate the original simulation', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const originalUph = sim.uph;
    applyMagicWandPreview(sim, suggestion);
    expect(sim.uph).toBe(originalUph);
  });

  it('returns a higher UPH than the original when bottleneck was the sole gate', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const preview = applyMagicWandPreview(sim, suggestion);
    expect(preview.uph).toBeGreaterThan(sim.uph);
  });

  it('reduces or maintains the bottleneck cycleTime in the preview', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const preview = applyMagicWandPreview(sim, suggestion);
    const previewBottleneck = preview.steps.find((s) => s.isBottleneck);
    const originalBottleneck = sim.steps.find((s) => s.isBottleneck)!;
    expect((previewBottleneck?.cycleTime ?? 0)).toBeLessThanOrEqual(originalBottleneck.cycleTime);
  });

  it('preview UPH matches suggestion.projectedUph', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const preview = applyMagicWandPreview(sim, suggestion);
    expect(preview.uph).toBeCloseTo(suggestion.projectedUph, 2);
  });

  it('keeps taktTime unchanged', () => {
    const sim = calculateSimulation(defaultProject);
    const suggestion = computeMagicWand(sim);
    if (!suggestion) return;
    const preview = applyMagicWandPreview(sim, suggestion);
    expect(preview.taktTime).toBe(sim.taktTime);
  });
});

// ─── runMockAiAnalysis + cache ────────────────────────────────────────────────

describe('runMockAiAnalysis', () => {
  beforeEach(() => {
    clearAiCache();
  });

  it('returns an AiInsight with the correct generatedFor', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim, 'Avery, Yeh');
    expect(insight.generatedFor).toBe('Avery, Yeh');
  });

  it('contextHash matches the hash of extracted context', () => {
    const sim = calculateSimulation(defaultProject);
    const ctx = extractAiContext(defaultProject, sim);
    const expectedHash = hashAiContext(ctx);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(insight.contextHash).toBe(expectedHash);
  });

  it('executiveSummary contains the model name', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(insight.executiveSummary).toContain(defaultProject.basicInfo.modelName);
  });

  it('executiveSummary contains the UPH value', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(insight.executiveSummary).toContain(sim.uph.toFixed(2));
  });

  it('returns at most 2 topBottlenecks entries', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(insight.topBottlenecks.length).toBeLessThanOrEqual(2);
  });

  it('memoizes results — second call returns the identical object reference', () => {
    const sim = calculateSimulation(defaultProject);
    const first = runMockAiAnalysis(defaultProject, sim);
    const second = runMockAiAnalysis(defaultProject, sim);
    expect(first).toBe(second);
  });

  it('cache is invalidated when simulation data changes', () => {
    const sim = calculateSimulation(defaultProject);
    const first = runMockAiAnalysis(defaultProject, sim);

    // Change the project demand so UPH context differs
    const altProject: ProjectState = {
      ...defaultProject,
      basicInfo: { ...defaultProject.basicInfo, demandWeekly: defaultProject.basicInfo.demandWeekly + 500 },
    };
    const second = runMockAiAnalysis(altProject, sim);
    expect(first).not.toBe(second);
    expect(first.contextHash).not.toBe(second.contextHash);
  });

  it('timestamp is a valid ISO-8601 string', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(() => new Date(insight.timestamp).toISOString()).not.toThrow();
  });

  it('uphPrediction is a non-empty string', () => {
    const sim = calculateSimulation(defaultProject);
    const insight = runMockAiAnalysis(defaultProject, sim);
    expect(typeof insight.uphPrediction).toBe('string');
    expect(insight.uphPrediction.length).toBeGreaterThan(0);
  });
});
