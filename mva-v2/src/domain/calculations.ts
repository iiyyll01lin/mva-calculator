import type {
  EquipmentItem,
  LaborBreakdownRow,
  LaborRow,
  MvaResults,
  ProjectState,
  SimulationResults,
  SimulationStepResult,
  SpaceAllocationRow,
} from './models';

const clamp01 = (value: number) => Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
const round = (value: number) => Number(value.toFixed(4));

function equipmentMonthlyCost(item: EquipmentItem): number {
  if (item.costPerMonth !== undefined && item.costPerMonth !== null && Number.isFinite(item.costPerMonth)) {
    return Math.max(0, item.costPerMonth);
  }
  const years = Math.max(0, item.depreciationYears);
  if (years === 0) return 0;
  return (Math.max(0, item.qty) * Math.max(0, item.unitPrice)) / (years * 12);
}

function spaceMonthlyCost(row: SpaceAllocationRow, defaultRate: number, multiplier: number, buildingAreaOverrideSqft: number): number {
  if (row.monthlyCost !== undefined && row.monthlyCost !== null && Number.isFinite(row.monthlyCost)) {
    return Math.max(0, row.monthlyCost);
  }
  const rate = row.ratePerSqft !== undefined && row.ratePerSqft !== null ? row.ratePerSqft : defaultRate;
  const area = Math.max(0, row.areaSqft);
  const effectiveArea = buildingAreaOverrideSqft > 0 ? area / Math.max(1, multiplier) : area * Math.max(1, multiplier);
  return Math.max(0, effectiveArea * Math.max(0, rate ?? 0));
}

function laborBreakdown(rows: LaborRow[], rate: number, lineUph: number, efficiency: number, strategy: ProjectState['plant']['yield']['strategy'], yieldFactor: number): LaborBreakdownRow[] {
  return rows.map((row) => {
    const baseUph = row.uphSource === 'override' && row.overrideUph ? row.overrideUph : lineUph;
    const uphUsed = strategy === 'apply_to_capacity' ? baseUph * yieldFactor : baseUph;
    const effectiveHeadcount = Math.max(0, row.headcount) * (row.allocationPercent ?? 1);
    const costPerUnit = uphUsed > 0 && efficiency > 0 ? (Math.max(0, rate) * effectiveHeadcount) / (uphUsed * efficiency) : 0;
    return {
      ...row,
      effectiveHeadcount: round(effectiveHeadcount),
      uphUsed: round(uphUsed),
      costPerUnit: round(costPerUnit),
    };
  });
}

export function calculateSimulation(project: ProjectState): SimulationResults {
  const { basicInfo, machines, bomMap, processSteps } = project;
  const availableMinutesPerDay = basicInfo.shiftsPerDay * basicInfo.hoursPerShift * 60 - basicInfo.shiftsPerDay * basicInfo.breaksPerShiftMin;
  const availableSecondsPerWeek = availableMinutesPerDay * 60 * basicInfo.workDays;
  const taktTime = basicInfo.demandWeekly > 0 ? availableSecondsPerWeek / basicInfo.demandWeekly : 0;

  const steps: SimulationStepResult[] = processSteps.map((step) => {
    const machine = machines.find((item) => item.group === step.process);
    if (!machine) {
      return {
        ...step,
        machineDescription: 'Unknown',
        cycleTime: 0,
        utilization: 0,
        componentCount: 0,
        isBottleneck: false,
      };
    }

    const componentCount = bomMap
      .filter((row) => row.machineGroup === step.process && row.side === step.side)
      .reduce((sum, row) => sum + row.count, 0);

    const oee = machine.type === 'placement' ? basicInfo.oeePlacement : basicInfo.oeeOthers;
    const rawCycle = machine.type === 'placement'
      ? (componentCount > 0 && machine.rate > 0 ? componentCount / machine.rate * 3600 : 0)
      : (machine.rate > 0 ? 3600 / machine.rate : 0);
    const effectiveCycle = oee > 0 ? rawCycle / oee : 0;
    const setupLossPerBoard = basicInfo.lotSize > 0 ? basicInfo.setupTimeMin * 60 / basicInfo.lotSize : 0;

    return {
      ...step,
      machineDescription: machine.description,
      cycleTime: round(effectiveCycle + setupLossPerBoard),
      utilization: 0,
      componentCount,
      isBottleneck: false,
    };
  });

  const maxCycleTime = Math.max(...steps.map((step) => step.cycleTime), 0);
  const totalCycleTime = steps.reduce((sum, step) => sum + step.cycleTime, 0);
  const normalized = steps.map((step) => ({
    ...step,
    isBottleneck: maxCycleTime > 0 && step.cycleTime === maxCycleTime,
    utilization: maxCycleTime > 0 ? round(step.cycleTime / maxCycleTime * 100) : 0,
  }));
  const uph = maxCycleTime > 0 ? round(3600 / maxCycleTime) : 0;
  const lbe = maxCycleTime > 0 && normalized.length > 0 ? round(totalCycleTime / (maxCycleTime * normalized.length) * 100) : 0;
  const weeklyOutput = round(uph * (availableMinutesPerDay / 60) * basicInfo.workDays * basicInfo.targetUtilization);
  const bottleneck = normalized.find((step) => step.isBottleneck)?.process ?? 'N/A';

  return {
    taktTime: round(taktTime),
    uph,
    lineBalanceEfficiency: lbe,
    weeklyOutput,
    bottleneckProcess: bottleneck,
    steps: normalized,
  };
}

export function calculateMva(project: ProjectState, simulation = calculateSimulation(project)): MvaResults {
  const { plant, basicInfo } = project;
  const yieldFactor = clamp01(plant.yield.fpy) * clamp01(plant.yield.vpy);
  const lineUphRaw = simulation.uph;
  const lineUphUsedForCapacity = plant.yield.strategy === 'apply_to_capacity' ? round(lineUphRaw * yieldFactor) : lineUphRaw;

  const monthlyVolume = plant.mvaVolume.demandMode === 'monthly'
    ? Math.max(0, plant.mvaVolume.rfqQtyPerMonth)
    : round(Math.max(0, plant.mvaVolume.weeklyDemand) * (Math.max(1, plant.mvaVolume.workDaysPerMonth) / Math.max(1, plant.mvaVolume.workDaysPerWeek)));
  const inferredVolume = monthlyVolume > 0
    ? monthlyVolume
    : round(lineUphUsedForCapacity * basicInfo.shiftsPerDay * basicInfo.hoursPerShift * plant.mvaVolume.workDaysPerMonth * basicInfo.targetUtilization);

  const activeOverhead = plant.overheadByProcess[plant.processType];
  const directLabor = laborBreakdown(
    plant.directLaborRows,
    plant.mvaRates.directLaborHourlyRate,
    lineUphRaw,
    plant.mvaRates.efficiency,
    plant.yield.strategy,
    yieldFactor,
  );
  const indirectLabor = laborBreakdown(
    plant.indirectLaborRows,
    plant.mvaRates.indirectLaborHourlyRate,
    lineUphRaw,
    plant.mvaRates.efficiency,
    plant.yield.strategy,
    yieldFactor,
  );

  const directLaborPerUnit = directLabor.reduce((sum, row) => sum + row.costPerUnit, 0);
  const indirectLaborPerUnit = indirectLabor.reduce((sum, row) => sum + row.costPerUnit, 0);

  const equipmentTotalPerMonth = [...plant.equipmentList, ...plant.extraEquipmentList].reduce((sum, item) => sum + equipmentMonthlyCost(item), 0);
  const effectiveEquipmentPerMonth = equipmentTotalPerMonth > 0 ? equipmentTotalPerMonth : activeOverhead.equipmentDepreciationPerMonth;
  const spaceTotalPerMonth = plant.spaceAllocation.reduce(
    (sum, row) => sum + spaceMonthlyCost(row, plant.spaceSettings.spaceRatePerSqft, plant.spaceSettings.spaceAreaMultiplier, plant.spaceSettings.buildingAreaOverrideSqft),
    0,
  );
  const effectiveSpacePerMonth = plant.spaceSettings.useSpaceAllocationTotal && spaceTotalPerMonth > 0 ? spaceTotalPerMonth : activeOverhead.spaceMonthlyCost;

  const volume = Math.max(0, inferredVolume);
  const equipmentDep = volume > 0 ? effectiveEquipmentPerMonth / volume : 0;
  const equipmentMaint = volume > 0 ? effectiveEquipmentPerMonth * activeOverhead.equipmentMaintenanceRatio / volume : 0;
  const spacePerUnit = volume > 0 ? effectiveSpacePerMonth / volume : 0;
  const powerPerUnit = Math.max(0, activeOverhead.powerCostPerUnit);
  const materialAttrition = Math.max(0, plant.materials.bomCostPerUnit) * clamp01(plant.materials.attritionRate);
  const consumables = Math.max(0, plant.materials.consumablesCostPerUnit);
  const sgaPerUnit = volume > 0
    ? (Math.max(0, plant.sga.corporateBurdenPerMonth) + Math.max(0, plant.sga.siteMaintenancePerMonth) + Math.max(0, plant.sga.itSecurityPerMonth)) / volume
    : 0;
  const profitPerUnit = Math.max(0, plant.profit.bcBomCostPerUnit) * clamp01(plant.profit.profitRate);
  const iccPerUnit = Math.max(0, plant.icc.inventoryValuePerUnit) * clamp01(plant.icc.iccRate);

  const costLines = [
    { key: 'dl', label: 'Direct Labor / Unit', value: round(directLaborPerUnit) },
    { key: 'idl', label: 'Indirect Labor / Unit', value: round(indirectLaborPerUnit) },
    { key: 'equipDep', label: 'Equipment Depreciation / Unit', value: round(equipmentDep) },
    { key: 'equipMaint', label: 'Equipment Maintenance / Unit', value: round(equipmentMaint) },
    { key: 'space', label: 'Space / Unit', value: round(spacePerUnit) },
    { key: 'power', label: 'Power / Unit', value: round(powerPerUnit) },
    { key: 'materialAttrition', label: 'Material Attrition / Unit', value: round(materialAttrition) },
    { key: 'consumables', label: 'Consumables / Unit', value: round(consumables) },
    { key: 'sga', label: 'SGA / Unit', value: round(sgaPerUnit) },
    { key: 'profit', label: 'Profit / Unit', value: round(profitPerUnit) },
    { key: 'icc', label: 'ICC / Unit', value: round(iccPerUnit) },
  ];

  const totalPerUnit = round(costLines.reduce((sum, line) => sum + line.value, 0));
  const warnings: string[] = [];
  if (lineUphRaw <= 0) warnings.push('Simulation UPH is zero. Review machine rates and process routing.');
  if (plant.mvaRates.efficiency <= 0) warnings.push('Efficiency must be greater than zero for labor costing.');
  if (volume <= 0) warnings.push('Monthly volume is zero. Overhead cannot be distributed.');
  if (simulation.steps.some((step) => step.machineDescription === 'Unknown')) {
    warnings.push('One or more process steps are not mapped to a known machine group.');
  }

  return {
    monthlyVolume: round(volume),
    lineUphRaw,
    lineUphUsedForCapacity,
    directLabor,
    indirectLabor,
    costLines,
    totalPerUnit,
    warnings,
  };
}

export function summarizeForCsv(project: ProjectState, simulation = calculateSimulation(project), mva = calculateMva(project, simulation)): string[][] {
  return [
    ['Section', 'Item', 'Value'],
    ['Simulation', 'Model', project.basicInfo.modelName],
    ['Simulation', 'UPH', String(simulation.uph)],
    ['Simulation', 'Takt Time', String(simulation.taktTime)],
    ['Simulation', 'Weekly Output', String(simulation.weeklyOutput)],
    ['MVA', 'Monthly Volume', String(mva.monthlyVolume)],
    ['MVA', 'Line UPH Raw', String(mva.lineUphRaw)],
    ['MVA', 'Line UPH Used', String(mva.lineUphUsedForCapacity)],
    ...mva.costLines.map((line) => ['MVA', line.label, String(line.value)]),
    ['MVA', 'Total / Unit', String(mva.totalPerUnit)],
    ['Confirmation', 'Decision', project.confirmation.decision ?? ''],
    ['Confirmation', 'Reviewer', project.confirmation.reviewer],
  ];
}
