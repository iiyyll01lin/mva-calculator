/**
 * Performance stress tests — "Monster Project" scenario.
 *
 * These tests verify that the CPU-bound domain functions remain within
 * acceptable latency budgets even under extreme factory data loads.
 *
 * Targets (measured in Vitest / Node.js, which runs faster than a browser
 * main thread, so the assertions are deliberately conservative):
 *   • calculateSimulation  < 50 ms
 *   • calculateMva         < 200 ms
 *
 * Data integrity assertions ensure that scaling up the input does not cause
 * NaN / Infinity propagation in the output.
 */
import { describe, expect, it } from 'vitest';
import { calculateMva, calculateSimulation } from '../../src/domain/calculations';
import { defaultProject } from '../../src/domain/defaults';
import type { BomRow, EquipmentItem, L10Station, LaborRow, ProjectState } from '../../src/domain/models';

// ─── Monster project factory ───────────────────────────────────────────────

function buildMonsterProject(): ProjectState {
  // 5 000 BOM rows spread across the two most common machine groups.
  const bomMap: BomRow[] = Array.from({ length: 5000 }, (_, i) => ({
    id: `bom-${i}`,
    side: i % 2 === 0 ? 'Top' : 'Bottom',
    bucket: `bucket-${i % 20}`,
    count: Math.floor(Math.random() * 500) + 1,
    machineGroup: i % 3 === 0 ? 'Placement_NXT_Chips' : 'Placement_NXT_IC',
  }));

  // 50 equipment items in the base list (depreciation rollup stress).
  const equipmentList: EquipmentItem[] = Array.from({ length: 50 }, (_, i) => ({
    id: `eq-${i}`,
    process: `Process_${i % 5}`,
    item: `Machine_${i}`,
    qty: Math.floor(Math.random() * 4) + 1,
    unitPrice: 50000 + i * 1000,
    depreciationYears: 5,
    costPerMonth: undefined,
  }));

  // 30 direct-labor rows (labor-breakdown stress).
  const directLaborRows: LaborRow[] = Array.from({ length: 30 }, (_, i) => ({
    id: `dl-${i}`,
    name: `Role_${i}`,
    process: `Dept_${i % 5}`,
    headcount: (i % 10) + 1,
    allocationPercent: 1,
    uphSource: 'line' as const,
  }));

  return {
    ...defaultProject,
    bomMap,
    plant: {
      ...defaultProject.plant,
      equipmentList,
      directLaborRows,
      useEquipmentListTotal: true,
    },
  };
}

const monsterProject = buildMonsterProject();

// ─── Timing helper ─────────────────────────────────────────────────────────

function elapsed(fn: () => void): number {
  const start = performance.now();
  fn();
  return performance.now() - start;
}

// ─── Tests ─────────────────────────────────────────────────────────────────

describe('stress test — 5 000 BOM rows + 50 equipment items', () => {
  it('calculateSimulation completes within 50 ms', () => {
    const ms = elapsed(() => calculateSimulation(monsterProject));
    expect(ms).toBeLessThan(50);
  });

  it('calculateMva completes within 200 ms', () => {
    const sim = calculateSimulation(monsterProject);
    const ms = elapsed(() => calculateMva(monsterProject, sim));
    expect(ms).toBeLessThan(200);
  });

  it('calculateMva produces finite totals (no NaN / Infinity propagation)', () => {
    const sim = calculateSimulation(monsterProject);
    const result = calculateMva(monsterProject, sim);

    expect(Number.isFinite(result.totalPerUnit)).toBe(true);
    for (const line of result.costLines) {
      expect(Number.isFinite(line.value)).toBe(true);
    }
    expect(Number.isFinite(result.dlPerUnit)).toBe(true);
    expect(Number.isFinite(result.idlPerUnit)).toBe(true);
    expect(Number.isFinite(result.overhead.totalOverheadPerUnit)).toBe(true);
  });

  it('calculateSimulation produces a valid UPH with 5 000 BOM rows', () => {
    const sim = calculateSimulation(monsterProject);
    expect(Number.isFinite(sim.uph)).toBe(true);
    expect(sim.uph).toBeGreaterThanOrEqual(0);
    expect(sim.steps.length).toBe(defaultProject.processSteps.length);
  });

  it('equipment rollup covers all 50 items', () => {
    const sim = calculateSimulation(monsterProject);
    const result = calculateMva(monsterProject, sim);
    // All 50 equipment items are in the computed list.
    expect(result.equipmentList.filter((e) => e.source === 'base').length).toBe(50);
    // The total monthly cost must be positive.
    expect(result.overhead.totalEquipmentCostPerMonth).toBeGreaterThan(0);
  });
});

describe('stress test — repeated computation stability', () => {
  it('produces identical results across 10 consecutive runs (deterministic)', () => {
    const sim = calculateSimulation(monsterProject);
    const baseline = calculateMva(monsterProject, sim);

    for (let i = 0; i < 9; i++) {
      const s = calculateSimulation(monsterProject);
      const r = calculateMva(monsterProject, s);
      expect(r.totalPerUnit).toBe(baseline.totalPerUnit);
      expect(r.overhead.totalEquipmentCostPerMonth).toBe(baseline.overhead.totalEquipmentCostPerMonth);
    }
  });
});

// ─── 200-station L10 scenario ─────────────────────────────────────────────

/**
 * Simulates an enterprise L10 line with 200 manual stations — the kind of
 * routing that triggers UI jank when rendered without virtualisation.
 *
 * This test validates that the domain layer handles the labour breakdown
 * rollup for such a large station table within acceptable latency.
 */
function buildL10HeavyProject(): ProjectState {
  const stations: L10Station[] = Array.from({ length: 200 }, (_, i) => ({
    id: `l10-st-${i}`,
    name: `Station_${i + 1}`,
    laborHc: (i % 4) + 1,
    parallelStations: (i % 3) + 1,
    cycleTimeSec: 10 + (i % 60),
    allowanceFactor: 1.1 + (i % 5) * 0.02,
  }));

  const directLaborRows: LaborRow[] = stations.map((s, i) => ({
    id: `l10-dl-${i}`,
    name: s.name,
    process: 'L10',
    headcount: s.laborHc,
    allocationPercent: 1,
    uphSource: 'line' as const,
  }));

  return {
    ...defaultProject,
    laborTimeL10: {
      ...defaultProject.laborTimeL10,
      stations,
      meta: {
        hoursPerShift: 8,
        shiftsPerDay: 2,
        workDaysPerWeek: 5,
        workDaysPerMonth: 22,
        fpy: 0.95,
      },
    },
    plant: {
      ...defaultProject.plant,
      directLaborRows,
    },
  };
}

const l10HeavyProject = buildL10HeavyProject();

describe('stress test — 200 L10 stations', () => {
  it('calculateSimulation completes within 50 ms with 200-station L10 project', () => {
    const ms = elapsed(() => calculateSimulation(l10HeavyProject));
    expect(ms).toBeLessThan(50);
  });

  it('calculateMva completes within 200 ms with 200-station L10 project', () => {
    const sim = calculateSimulation(l10HeavyProject);
    const ms = elapsed(() => calculateMva(l10HeavyProject, sim));
    expect(ms).toBeLessThan(200);
  });

  it('calculateMva produces finite totals for 200-station L10 project', () => {
    const sim = calculateSimulation(l10HeavyProject);
    const result = calculateMva(l10HeavyProject, sim);
    expect(Number.isFinite(result.totalPerUnit)).toBe(true);
    expect(Number.isFinite(result.dlPerUnit)).toBe(true);
    for (const line of result.costLines) {
      expect(Number.isFinite(line.value)).toBe(true);
    }
  });

  it('snapshot: l10StationSnapshots derivation is O(N) and stays under 5 ms for 200 stations', () => {
    // Mirrors the useMemo computation in LaborTimeL10Page so we can assert
    // that the per-render snapshot derivation is non-blocking even without React.
    const meta = l10HeavyProject.laborTimeL10.meta;
    const hoursPerShift = meta.hoursPerShift ?? 0;
    const shiftsPerDay = meta.shiftsPerDay ?? 0;
    const workDaysPerWeek = meta.workDaysPerWeek ?? 0;
    const workDaysPerMonth = meta.workDaysPerMonth ?? 0;

    const ms = elapsed(() => {
      l10HeavyProject.laborTimeL10.stations.map((station) => {
        const parallelStations = Math.max(1, station.parallelStations || 1);
        const cycleTimeSec = station.cycleTimeSec ?? 0;
        const avgCycleTimeSec = cycleTimeSec > 0 ? (cycleTimeSec * station.allowanceFactor) / parallelStations : 0;
        const uph = avgCycleTimeSec > 0 ? 3600 / avgCycleTimeSec : 0;
        return {
          ...station,
          avgCycleTimeSec,
          uph,
          perShiftCapa: uph * hoursPerShift,
          dailyCapa: uph * hoursPerShift * shiftsPerDay,
          weeklyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerWeek,
          monthlyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerMonth,
        };
      });
    });
    expect(ms).toBeLessThan(5);
  });
});
