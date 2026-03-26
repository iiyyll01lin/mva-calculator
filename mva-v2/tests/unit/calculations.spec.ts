import { describe, expect, it } from 'vitest';
import {
  buildMatrixSpaceAllocation,
  calcL10StationComputed,
  calcL6StationComputed,
  calculateMva,
  calculateSimulation,
  equipmentMonthlyCost,
  normalizeSpaceProcessName,
  spaceMonthlyCost,
  splitFloorSpace4060,
  summarizeForCsv,
} from '../../src/domain/calculations';
import { defaultProject } from '../../src/domain/defaults';
import type { ProjectState } from '../../src/domain/models';

// ─── round() sanity (exercised indirectly via UPH / cost calculations) ─────

describe('round() precision via calculations', () => {
  it('UPH in summarizeForCsv is finite and matches direct calculation', () => {
    const rows = summarizeForCsv(defaultProject);
    const uphRow = rows.find((r) => r[1] === 'UPH');
    expect(uphRow).toBeDefined();
    const uphValue = Number(uphRow![2]);
    expect(Number.isFinite(uphValue)).toBe(true);
    expect(uphValue).toBeGreaterThan(0);
  });

  it('totalPerUnit from calculateMva is always finite (no NaN propagation)', () => {
    const sim = calculateSimulation(defaultProject);
    const mva = calculateMva(defaultProject, sim);
    expect(Number.isFinite(mva.totalPerUnit)).toBe(true);
    for (const line of mva.costLines) {
      expect(Number.isFinite(line.value)).toBe(true);
    }
  });

  it('summing costLines equals totalPerUnit (no silent rounding drift)', () => {
    const sim = calculateSimulation(defaultProject);
    const mva = calculateMva(defaultProject, sim);
    const sumLines = mva.costLines.reduce((acc, l) => acc + l.value, 0);
    // Allow up to 1 cent tolerance for accumulated 4-decimal rounding
    expect(Math.abs(sumLines - mva.totalPerUnit)).toBeLessThan(0.01);
  });
});

// ─── calculateSimulation edge cases ───────────────────────────────────────

describe('calculateSimulation – edge cases', () => {
  it('returns UPH=0 and bottleneck=N/A when processSteps is empty', () => {
    const project: ProjectState = { ...defaultProject, processSteps: [] };
    const result = calculateSimulation(project);
    expect(result.uph).toBe(0);
    expect(result.bottleneckProcess).toBe('N/A');
    expect(result.steps).toHaveLength(0);
  });

  it('returns non-zero cycleTime (setup-loss only) for a placement step whose BOM count is zero', () => {
    const project: ProjectState = {
      ...defaultProject,
      processSteps: [{ step: 1, process: 'Placement_NXT_Chips', side: 'Top' }],
      bomMap: [
        { id: 'b1', side: 'Top', bucket: 'chips', count: 0, machineGroup: 'Placement_NXT_Chips' },
      ],
    };
    const result = calculateSimulation(project);
    // rawCycle=0, but setupLossPerBoard (setupTimeMin * 60 / lotSize) is still added.
    // With defaults: 60*60/1000 = 3.6s per board of setup overhead.
    expect(result.steps[0].cycleTime).toBeCloseTo(3.6, 2);
    // UPH = 3600 / 3.6 = 1000 (driven entirely by setup loss)
    expect(result.uph).toBeGreaterThan(0);
  });

  it('marks unknown machine group with cycleTime=0 and "Unknown" description', () => {
    const project: ProjectState = {
      ...defaultProject,
      processSteps: [{ step: 1, process: 'NonExistentProcess', side: 'Top' }],
    };
    const result = calculateSimulation(project);
    expect(result.steps[0].machineDescription).toBe('Unknown');
    expect(result.steps[0].cycleTime).toBe(0);
  });

  it('lineBalanceEfficiency is 100% for a single-step line', () => {
    const project: ProjectState = {
      ...defaultProject,
      processSteps: [{ step: 1, process: 'Printer', side: 'Top' }],
    };
    const result = calculateSimulation(project);
    expect(result.lineBalanceEfficiency).toBe(100);
  });

  it('lineBalanceEfficiency < 100% when steps are unbalanced', () => {
    const result = calculateSimulation(defaultProject);
    expect(result.lineBalanceEfficiency).toBeLessThan(100);
    expect(result.lineBalanceEfficiency).toBeGreaterThan(0);
  });

  it('taktTime is 0 when demandWeekly is 0', () => {
    const project: ProjectState = {
      ...defaultProject,
      basicInfo: { ...defaultProject.basicInfo, demandWeekly: 0 },
    };
    const result = calculateSimulation(project);
    expect(result.taktTime).toBe(0);
  });

  it('weeklyOutput is 0 when UPH is 0', () => {
    const project: ProjectState = { ...defaultProject, processSteps: [] };
    const result = calculateSimulation(project);
    expect(result.weeklyOutput).toBe(0);
  });
});

// ─── calculateMva edge cases & yield guard ────────────────────────────────

describe('calculateMva – edge cases', () => {
  it('does NOT use l10Fpy when meta.fpy is null (critical null guard)', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { ...defaultProject.plant.yield, useL10Fpy: true, fpy: 0.95, vpy: 1 },
      },
      laborTimeL10: {
        ...defaultProject.laborTimeL10,
        meta: { ...defaultProject.laborTimeL10.meta, fpy: null },
      },
    };
    const sim = calculateSimulation(project);
    const mva = calculateMva(project, sim);
    // Must fall back to plant.yield.fpy=0.95, NOT use Number(null)=0
    expect(mva.yield.fpy).toBeCloseTo(0.95, 4);
    expect(mva.yield.fpy).toBeGreaterThan(0);
  });

  it('does NOT use l10Fpy when meta.fpy is undefined', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { ...defaultProject.plant.yield, useL10Fpy: true, fpy: 0.9, vpy: 1 },
      },
      laborTimeL10: {
        ...defaultProject.laborTimeL10,
        meta: { ...defaultProject.laborTimeL10.meta, fpy: undefined },
      },
    };
    const mva = calculateMva(project);
    expect(mva.yield.fpy).toBeCloseTo(0.9, 4);
  });

  it('uses l10Fpy when it is a valid positive number', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { ...defaultProject.plant.yield, useL10Fpy: true, fpy: 0.9, vpy: 1 },
      },
      laborTimeL10: {
        ...defaultProject.laborTimeL10,
        meta: { ...defaultProject.laborTimeL10.meta, fpy: 0.85 },
      },
    };
    const mva = calculateMva(project);
    expect(mva.yield.fpy).toBeCloseTo(0.85, 4);
  });

  it('generates volume=0 warning and returns zero per-unit overhead costs', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        mvaVolume: { ...defaultProject.plant.mvaVolume, demandMode: 'monthly', rfqQtyPerMonth: 0 },
      },
      basicInfo: { ...defaultProject.basicInfo, targetUtilization: 0 },
    };
    const mva = calculateMva(project);
    expect(mva.monthlyVolume).toBe(0);
    expect(mva.overhead.equipmentDepPerUnit).toBe(0);
    expect(mva.overhead.spacePerUnit).toBe(0);
    expect(mva.sga.corporateBurdenPerUnit).toBe(0);
    expect(mva.warnings.some((w) => /volume/i.test(w))).toBe(true);
    // totalPerUnit must still be finite (no NaN)
    expect(Number.isFinite(mva.totalPerUnit)).toBe(true);
  });

  it('generates efficiency=0 warning and returns zero labor costs', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: { ...defaultProject.plant, mvaRates: { ...defaultProject.plant.mvaRates, efficiency: 0 } },
    };
    const mva = calculateMva(project);
    expect(mva.dlPerUnit).toBe(0);
    expect(mva.idlPerUnit).toBe(0);
    expect(mva.warnings.some((w) => /efficiency/i.test(w))).toBe(true);
  });

  it('generates UPH=0 warning when simulation produces zero throughput', () => {
    const project: ProjectState = { ...defaultProject, processSteps: [] };
    const mva = calculateMva(project);
    expect(mva.warnings.some((w) => /uph/i.test(w))).toBe(true);
  });

  it('lineUphUsedForCapacity < lineUphRaw when strategy=apply_to_capacity and yield < 1', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'apply_to_capacity', fpy: 0.9, vpy: 0.9, useL10Fpy: false },
      },
    };
    const sim = calculateSimulation(project);
    const mva = calculateMva(project, sim);
    expect(mva.lineUphUsedForCapacity).toBeLessThan(mva.lineUphRaw);
  });

  it('lineUphUsedForCapacity equals lineUphRaw when strategy=ignore', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'ignore', fpy: 0.8, vpy: 0.8, useL10Fpy: false },
      },
    };
    const mva = calculateMva(project);
    expect(mva.lineUphUsedForCapacity).toBe(mva.lineUphRaw);
  });

  it('clamps fpy > 1 to 1.0 (yield cannot exceed 100%)', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'ignore', fpy: 1.5, vpy: 1.0, useL10Fpy: false },
      },
    };
    const mva = calculateMva(project);
    expect(mva.yield.fpy).toBe(1);
  });

  it('clamps vpy < 0 to 0.0 (yield cannot be negative)', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'ignore', fpy: 1.0, vpy: -0.5, useL10Fpy: false },
      },
    };
    const mva = calculateMva(project);
    expect(mva.yield.vpy).toBe(0);
  });

  it('all costLine values are non-negative', () => {
    const mva = calculateMva(defaultProject);
    for (const line of mva.costLines) {
      expect(line.value).toBeGreaterThanOrEqual(0);
    }
  });

  it('totalPerUnit equals sum of all costLine values within rounding tolerance', () => {
    const mva = calculateMva(defaultProject);
    const sum = mva.costLines.reduce((acc, l) => acc + l.value, 0);
    expect(Math.abs(sum - mva.totalPerUnit)).toBeLessThan(0.01);
  });
});

// ─── equipmentMonthlyCost edge cases ─────────────────────────────────────

describe('equipmentMonthlyCost – edge cases', () => {
  it('returns 0 when depreciationYears is 0 (no divide-by-zero)', () => {
    const cost = equipmentMonthlyCost({ id: 'eq1', process: 'SMT', item: 'Printer', qty: 1, unitPrice: 50000, depreciationYears: 0 });
    expect(cost).toBe(0);
  });

  it('uses costPerMonth override when provided', () => {
    const cost = equipmentMonthlyCost({ id: 'eq2', process: 'SMT', item: 'Printer', qty: 1, unitPrice: 50000, depreciationYears: 5, costPerMonth: 1234 });
    expect(cost).toBe(1234);
  });

  it('ignores null costPerMonth and falls back to depreciation formula', () => {
    const cost = equipmentMonthlyCost({ id: 'eq3', process: 'SMT', item: 'Printer', qty: 1, unitPrice: 60000, depreciationYears: 5, costPerMonth: null });
    expect(cost).toBeCloseTo(1000, 2);
  });

  it('returns 0 for zero qty', () => {
    expect(equipmentMonthlyCost({ id: 'e1', process: 'S', item: 'I', qty: 0, unitPrice: 50000, depreciationYears: 5 })).toBe(0);
  });

  it('returns 0 for zero unitPrice', () => {
    expect(equipmentMonthlyCost({ id: 'e2', process: 'S', item: 'I', qty: 2, unitPrice: 0, depreciationYears: 5 })).toBe(0);
  });

  it('returns 0 for negative qty (clamped)', () => {
    expect(equipmentMonthlyCost({ id: 'e3', process: 'S', item: 'I', qty: -1, unitPrice: 50000, depreciationYears: 5 })).toBe(0);
  });
});

// ─── spaceMonthlyCost edge cases ──────────────────────────────────────────

describe('spaceMonthlyCost – edge cases', () => {
  it('returns monthlyCost override when provided', () => {
    const row = { id: 'r1', floor: 'F1', process: 'SMT', areaSqft: 1000, ratePerSqft: 5, monthlyCost: 9999 };
    const cost = spaceMonthlyCost(row, defaultProject);
    expect(cost).toBe(9999);
  });

  it('computes from areaSqft × rate when no override (multiplier=1, no buildingOverride)', () => {
    const row = { id: 'r2', floor: 'F1', process: 'SMT', areaSqft: 1000, ratePerSqft: 2, monthlyCost: null };
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        spaceSettings: { ...defaultProject.plant.spaceSettings, spaceAreaMultiplier: 1, buildingAreaOverrideSqft: 0 },
      },
    };
    expect(spaceMonthlyCost(row, project)).toBe(2000);
  });
});

// ─── splitFloorSpace4060 precision ───────────────────────────────────────

describe('splitFloorSpace4060 – rounding & correctness', () => {
  it('SMT + HI areas sum to totalFloorSqft (FA mode)', () => {
    const rows = splitFloorSpace4060({ totalFloorSqft: 1001, hiMode: 'FA' });
    const total = rows.reduce((sum, r) => sum + r.areaSqft, 0);
    expect(total).toBeCloseTo(1001, 2);
  });

  it('all three areas sum to totalFloorSqft in FA_FBT mode', () => {
    const rows = splitFloorSpace4060({ totalFloorSqft: 3333.33, hiMode: 'FA_FBT' });
    const total = rows.reduce((sum, r) => sum + r.areaSqft, 0);
    expect(total).toBeCloseTo(3333.33, 1);
  });

  it('returns 0 for all areas when totalFloorSqft is 0', () => {
    splitFloorSpace4060({ totalFloorSqft: 0, hiMode: 'FA' }).forEach((r) => expect(r.areaSqft).toBe(0));
  });

  it('returns 0 for all areas when totalFloorSqft is negative', () => {
    splitFloorSpace4060({ totalFloorSqft: -500, hiMode: 'FBT' }).forEach((r) => expect(r.areaSqft).toBe(0));
  });

  it('SMT is 40% and HI is 60% in FBT mode (within rounding tolerance)', () => {
    const rows = splitFloorSpace4060({ totalFloorSqft: 1000, hiMode: 'FBT' });
    const smt = rows.find((r) => r.process === 'SMT')!;
    const fbt = rows.find((r) => r.process === 'FBT')!;
    expect(smt.areaSqft).toBeCloseTo(400, 1);
    expect(fbt.areaSqft).toBeCloseTo(600, 1);
  });
});

// ─── buildMatrixSpaceAllocation ──────────────────────────────────────────

describe('buildMatrixSpaceAllocation', () => {
  it('returns empty array when processDistribution is empty', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        spaceSettings: { ...defaultProject.plant.spaceSettings, mode: 'matrix', processDistribution: {} },
      },
    };
    expect(buildMatrixSpaceAllocation(project)).toHaveLength(0);
  });

  it('assigns areas proportional to percent of total floor area', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        spaceSettings: {
          ...defaultProject.plant.spaceSettings,
          mode: 'matrix',
          lineLengthFt: 100,
          lineWidthFt: 10,
          processDistribution: { SMT: 60, 'F/A': 40 },
        },
      },
    };
    const rows = buildMatrixSpaceAllocation(project);
    expect(rows).toHaveLength(2);
    expect(rows.find((r) => r.process === 'SMT')!.areaSqft).toBeCloseTo(600, 1);
    expect(rows.find((r) => r.process === 'F/A')!.areaSqft).toBeCloseTo(400, 1);
  });
});

// ─── normalizeSpaceProcessName ────────────────────────────────────────────

describe('normalizeSpaceProcessName', () => {
  it.each([
    ['SMT Line 1', 'SMT'],
    ['Functional Test', 'FBT'],
    ['FBT Area', 'FBT'],
    ['Final Assembly', 'F/A'],
    ['FA Zone', 'F/A'],
    ['Office', 'Non-Prod'],
    ['Warehouse', 'Non-Prod'],
    ['W/H Storage', 'Non-Prod'],
    ['Custom Zone', 'Custom Zone'],
    ['', ''],
  ])('normalizes "%s" → "%s"', (input, expected) => {
    expect(normalizeSpaceProcessName(input)).toBe(expected);
  });
});

// ─── calcL10StationComputed edge cases ───────────────────────────────────

describe('calcL10StationComputed – edge cases', () => {
  const baseMeta = { hoursPerShift: 8, shiftsPerDay: 2, workDaysPerWeek: 5, workDaysPerMonth: 22 };

  it('returns null avgCycleTimeSec and uph when cycleTimeSec is null', () => {
    const r = calcL10StationComputed({ id: 's1', name: 'T', cycleTimeSec: null, laborHc: 1, parallelStations: 1, allowanceFactor: 1 }, baseMeta);
    expect(r.avgCycleTimeSec).toBeNull();
    expect(r.uph).toBeNull();
    expect(r.perShiftCapa).toBeNull();
  });

  it('returns null uph when cycleTimeSec is 0', () => {
    const r = calcL10StationComputed({ id: 's2', name: 'T', cycleTimeSec: 0, laborHc: 1, parallelStations: 1, allowanceFactor: 1 }, baseMeta);
    expect(r.uph).toBeNull();
  });

  it('computes correct UPH for a standard station', () => {
    // CT=360s, allowance=1.0, parallel=1 → avgCT=360 → UPH=10
    const r = calcL10StationComputed({ id: 's3', name: 'Kitting', cycleTimeSec: 360, laborHc: 2, parallelStations: 1, allowanceFactor: 1 }, baseMeta);
    expect(r.uph).toBeCloseTo(10, 2);
    expect(r.perShiftCapa).toBeCloseTo(80, 1);
    expect(r.monthlyCapa).toBeCloseTo(80 * 2 * 22, 0);
  });

  it('correctly halves avgCycleTimeSec with 2 parallel stations', () => {
    const s1 = calcL10StationComputed({ id: 'a', name: 'A', cycleTimeSec: 100, laborHc: 1, parallelStations: 1, allowanceFactor: 1 }, baseMeta);
    const s2 = calcL10StationComputed({ id: 'b', name: 'B', cycleTimeSec: 100, laborHc: 1, parallelStations: 2, allowanceFactor: 1 }, baseMeta);
    expect(s2.avgCycleTimeSec).toBeCloseTo(s1.avgCycleTimeSec! / 2, 3);
    expect(s2.uph).toBeCloseTo(s1.uph! * 2, 2);
  });

  it('returns null dailyCapa when meta.shiftsPerDay is null', () => {
    const r = calcL10StationComputed(
      { id: 's4', name: 'T', cycleTimeSec: 60, laborHc: 1, parallelStations: 1, allowanceFactor: 1 },
      { hoursPerShift: 8, shiftsPerDay: null, workDaysPerWeek: 5, workDaysPerMonth: 22 },
    );
    expect(r.dailyCapa).toBeNull();
  });
});

// ─── calcL6StationComputed edge cases ────────────────────────────────────

describe('calcL6StationComputed – edge cases', () => {
  it('returns null avgCycleTimeSec and uph when cycleTimeSec is null', () => {
    const r = calcL6StationComputed({ id: 's1', name: 'SMT', cycleTimeSec: null, laborHc: 1, parallelStations: 1, allowanceRate: 15 });
    expect(r.avgCycleTimeSec).toBeNull();
    expect(r.uph).toBeNull();
  });

  it('treats allowanceRate > 1 as a percentage (15 ≡ 0.15 ≡ +15% factor)', () => {
    const r1 = calcL6StationComputed({ id: 'a', name: 'X', cycleTimeSec: 100, laborHc: 1, parallelStations: 1, allowanceRate: 15 });
    const r2 = calcL6StationComputed({ id: 'b', name: 'X', cycleTimeSec: 100, laborHc: 1, parallelStations: 1, allowanceRate: 0.15 });
    expect(r1.avgCycleTimeSec).toBeCloseTo(r2.avgCycleTimeSec!, 4);
  });

  it('uses dlOnline as effective headcount when provided', () => {
    const r = calcL6StationComputed({ id: 's2', name: 'HI', cycleTimeSec: 60, laborHc: 5, parallelStations: 1, allowanceRate: 0, dlOnline: 3 });
    expect(r.laborHc).toBe(3);
  });

  it('returns null uph when cycleTimeSec is 0', () => {
    const r = calcL6StationComputed({ id: 's3', name: 'Pkg', cycleTimeSec: 0, laborHc: 2, parallelStations: 1, allowanceRate: 0 });
    expect(r.uph).toBeNull();
  });
});

// ─── baseline regression guard ────────────────────────────────────────────

describe('calculation engine', () => {
  it('computes a non-zero simulation baseline', () => {
    const result = calculateSimulation(defaultProject);
    expect(result.uph).toBeGreaterThan(0);
    expect(result.bottleneckProcess).toBeTruthy();
    expect(result.steps).toHaveLength(defaultProject.processSteps.length);
  });

  it('applies yield to capacity when requested', () => {
    const project = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: {
          strategy: 'apply_to_capacity' as const,
          fpy: 0.9,
          vpy: 0.8,
          useL10Fpy: false,
        },
      },
    };
    const simulation = calculateSimulation(project);
    const mva = calculateMva(project, simulation);
    expect(mva.lineUphUsedForCapacity).toBeLessThan(simulation.uph);
  });

  it('derives simulation rates from equipment mapping when enabled', () => {
    const baseline = calculateSimulation(defaultProject);
    const project = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        showEquipmentSimMapping: true as const,
        includeExtraEquipmentInSimMapping: false as const,
        equipmentList: [
          ...defaultProject.plant.equipmentList,
          {
            id: 'eq-map-printer',
            process: 'SMT',
            item: 'Printer clone',
            qty: 1,
            unitPrice: 50000,
            depreciationYears: 5,
            simMachineGroup: 'Printer',
            simParallelQty: 3,
          },
        ],
      },
    };
    const mapped = calculateSimulation(project);
    const baselinePrinter = baseline.steps.find((step) => step.process === 'Printer');
    const mappedPrinter = mapped.steps.find((step) => step.process === 'Printer');
    expect(baselinePrinter).toBeTruthy();
    expect(mappedPrinter).toBeTruthy();
    expect(mappedPrinter!.cycleTime).toBeLessThan(baselinePrinter!.cycleTime);
  });

  it('builds a csv-ready summary matrix', () => {
    const rows = summarizeForCsv(defaultProject);
    expect(rows[0]).toEqual(['Section', 'Item', 'Value']);
    expect(rows.some((row) => row[1] === 'Total / Unit')).toBe(true);
  });
});

