import { describe, expect, it } from 'vitest';
import { buildSummaryCsv } from '../../src/domain/exporters';
import { calculateMva, calculateSimulation, projectForProcess } from '../../src/domain/calculations';
import { defaultProject } from '../../src/domain/defaults';
import type { ProjectState } from '../../src/domain/models';

describe('summary regression', () => {
  it('matches the release-candidate summary snapshot', () => {
    expect(buildSummaryCsv(defaultProject)).toMatchInlineSnapshot(
      `
      "Section,Item,Value
      Simulation,Model,1395A3368201
      Simulation,UPH,6.4156
      Simulation,Takt Time,265.2
      Simulation,Weekly Output,2552.1257
      Simulation,Bottleneck,Placement_NXT_Chips
      MVA,Monthly Volume,10000
      MVA,Line UPH Raw,6.4156
      MVA,Line UPH Used,6.4156
      MVA,Yield Strategy,ignore
      MVA,Yield Factor,0.96
      MVA,Direct Labor / Unit,104.7514
      MVA,Indirect Labor / Unit,89.0152
      MVA,Equipment Depreciation / Unit,1.425
      MVA,Equipment Maintenance / Unit,0.1425
      MVA,Space / Unit,4.16
      MVA,Power / Unit,0.28
      MVA,Material Attrition / Unit,0.54
      MVA,Consumables / Unit,0.8
      MVA,SGA / Unit,4.05
      MVA,Profit / Unit,3.6
      MVA,ICC / Unit,0.6
      MVA,Total / Unit,209.3641
      Confirmation,Decision,OK
      Confirmation,Reviewer,System"
    `,
    );
  });
});

// ─── L10 / L6 process-specific regression locks ──────────────────────────

describe('L10 process path regression', () => {
  it('L10 summary contains expected cost line keys', () => {
    const l10Project = projectForProcess(defaultProject, 'L10');
    const csv = buildSummaryCsv(l10Project);
    expect(csv).toContain('Direct Labor / Unit');
    expect(csv).toContain('Equipment Depreciation / Unit');
    expect(csv).toContain('Total / Unit');
    expect(csv).not.toContain('NaN');
  });

  it('L10 totalPerUnit is a stable positive number', () => {
    const l10Project = projectForProcess(defaultProject, 'L10');
    const sim = calculateSimulation(l10Project);
    const mva = calculateMva(l10Project, sim);
    expect(mva.totalPerUnit).toBeGreaterThan(0);
    expect(Number.isFinite(mva.totalPerUnit)).toBe(true);
    // Lock: total should be in a plausible manufacturing range for this product
    expect(mva.totalPerUnit).toBeGreaterThan(50);
    expect(mva.totalPerUnit).toBeLessThan(10000);
  });

  it('L10 dlPerUnit and idlPerUnit are both > 0', () => {
    const l10Project = projectForProcess(defaultProject, 'L10');
    const mva = calculateMva(l10Project);
    expect(mva.dlPerUnit).toBeGreaterThan(0);
    expect(mva.idlPerUnit).toBeGreaterThan(0);
  });

  it('L10 overhead breakdown is internally consistent', () => {
    const l10Project = projectForProcess(defaultProject, 'L10');
    const mva = calculateMva(l10Project);
    const calculatedOverheadSum = mva.overhead.equipmentDepPerUnit
      + mva.overhead.equipmentMaintPerUnit
      + mva.overhead.spacePerUnit
      + mva.overhead.powerPerUnit;
    // Exported totalOverheadPerUnit must match the sum of its components
    expect(Math.abs(calculatedOverheadSum - mva.overhead.totalOverheadPerUnit)).toBeLessThan(0.01);
  });
});

describe('L6 process path regression', () => {
  it('L6 summary contains expected cost line keys', () => {
    const l6Project = projectForProcess(defaultProject, 'L6');
    const csv = buildSummaryCsv(l6Project);
    expect(csv).toContain('Direct Labor / Unit');
    expect(csv).toContain('Equipment Depreciation / Unit');
    expect(csv).toContain('Total / Unit');
    expect(csv).not.toContain('NaN');
  });

  it('L6 totalPerUnit is a stable positive number', () => {
    const l6Project = projectForProcess(defaultProject, 'L6');
    const mva = calculateMva(l6Project);
    expect(mva.totalPerUnit).toBeGreaterThan(0);
    expect(Number.isFinite(mva.totalPerUnit)).toBe(true);
    expect(mva.totalPerUnit).toBeGreaterThan(50);
    expect(mva.totalPerUnit).toBeLessThan(10000);
  });

  it('L6 overhead breakdown is internally consistent', () => {
    const l6Project = projectForProcess(defaultProject, 'L6');
    const mva = calculateMva(l6Project);
    const calculatedOverheadSum = mva.overhead.equipmentDepPerUnit
      + mva.overhead.equipmentMaintPerUnit
      + mva.overhead.spacePerUnit
      + mva.overhead.powerPerUnit;
    expect(Math.abs(calculatedOverheadSum - mva.overhead.totalOverheadPerUnit)).toBeLessThan(0.01);
  });
});

// ─── Edge-case regression: zero volume must not produce NaN ──────────────

describe('zero-volume edge-case regression', () => {
  it('CSV summary contains no NaN when volume=0', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        mvaVolume: { ...defaultProject.plant.mvaVolume, demandMode: 'monthly', rfqQtyPerMonth: 0 },
      },
      basicInfo: { ...defaultProject.basicInfo, targetUtilization: 0 },
    };
    const csv = buildSummaryCsv(project);
    expect(csv).not.toContain('NaN');
    expect(csv).not.toContain('Infinity');
  });

  it('per-unit costs are 0 but not NaN when monthly volume is 0', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        mvaVolume: { ...defaultProject.plant.mvaVolume, demandMode: 'monthly', rfqQtyPerMonth: 0 },
      },
      basicInfo: { ...defaultProject.basicInfo, targetUtilization: 0 },
    };
    const mva = calculateMva(project);
    expect(mva.overhead.equipmentDepPerUnit).toBe(0);
    expect(mva.overhead.spacePerUnit).toBe(0);
    expect(mva.sga.totalSgaPerUnit).toBe(0);
    expect(mva.totalPerUnit).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(mva.totalPerUnit)).toBe(true);
  });
});

// ─── apply_to_capacity yield mode regression lock ────────────────────────

describe('apply_to_capacity yield strategy regression', () => {
  it('lineUphUsedForCapacity is consistently lower than lineUphRaw', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'apply_to_capacity', fpy: 0.95, vpy: 0.98, useL10Fpy: false },
      },
    };
    const sim = calculateSimulation(project);
    const mva = calculateMva(project, sim);
    const expectedYieldFactor = 0.95 * 0.98;
    expect(mva.yield.yieldFactor).toBeCloseTo(expectedYieldFactor, 4);
    expect(mva.lineUphUsedForCapacity).toBeCloseTo(mva.lineUphRaw * expectedYieldFactor, 3);
  });

  it('CSV summary for apply_to_capacity contains no NaN', () => {
    const project: ProjectState = {
      ...defaultProject,
      plant: {
        ...defaultProject.plant,
        yield: { strategy: 'apply_to_capacity', fpy: 0.9, vpy: 0.9, useL10Fpy: false },
      },
    };
    const csv = buildSummaryCsv(project);
    expect(csv).not.toContain('NaN');
    expect(csv).not.toContain('Infinity');
  });
});

