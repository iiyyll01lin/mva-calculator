import { describe, expect, it } from 'vitest';
import { calculateMva, calculateSimulation, summarizeForCsv } from '../../src/domain/calculations';
import { defaultProject } from '../../src/domain/defaults';

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
