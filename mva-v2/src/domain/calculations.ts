import { baseMachineRateByGroup, defaultMachines } from './defaults';
import type {
  EquipmentComputedRow,
  EquipmentDeltaResult,
  EquipmentItem,
  GroupedBreakdownRow,
  L10Station,
  L10StationMeta,
  L6Station,
  LaborBreakdownRow,
  LaborRow,
  Machine,
  MvaResults,
  ProjectState,
  SimulationResults,
  SimulationStepResult,
  SpaceAllocationRow,
  SpaceComputedRow,
} from './models';

const clamp01 = (value: number, fallback = 0): number => {
  const safe = Number.isFinite(value) ? value : fallback;
  return Math.min(1, Math.max(0, safe));
};

/**
 * Epsilon-corrected half-up rounding. Returns 0 for NaN / ±Infinity so that
 * floating-point noise never propagates into cost-rollup tables as NaN strings.
 */
const round = (value: number, digits = 4): number => {
  if (!Number.isFinite(value)) return 0;
  const factor = Math.pow(10, digits);
  return Math.round((value + Number.EPSILON) * factor) / factor;
};

export function normalizeSpaceProcessName(raw: unknown): string {
  const value = String(raw ?? '').trim();
  if (!value) return '';
  const upper = value.toUpperCase();
  if (upper.includes('SMT')) return 'SMT';
  if (upper.includes('FBT') || upper.includes('FUNCTION')) return 'FBT';
  if (upper.includes('OFFICE') || upper.includes('NON') || upper.includes('WAREHOUSE') || upper.includes('W/H')) return 'Non-Prod';
  if (upper.includes('F/A') || upper.includes('FA') || upper.includes('ASSEMB') || upper.includes('ASSY')) return 'F/A';
  return value;
}

export function splitFloorSpace4060({ totalFloorSqft, hiMode }: { totalFloorSqft: number; hiMode: 'FA' | 'FBT' | 'FA_FBT' }) {
  const total = Math.max(0, Number(totalFloorSqft) || 0);
  // Round at 2 decimal places to avoid floating-point noise in sqft values.
  const smt = round(total * 0.4, 2);
  const hi = round(total - smt, 2);
  if (hiMode === 'FA') return [{ process: 'SMT', areaSqft: smt }, { process: 'F/A', areaSqft: hi }];
  if (hiMode === 'FBT') return [{ process: 'SMT', areaSqft: smt }, { process: 'FBT', areaSqft: hi }];
  const hiHalf = round(hi / 2, 2);
  return [
    { process: 'SMT', areaSqft: smt },
    { process: 'F/A', areaSqft: hiHalf },
    { process: 'FBT', areaSqft: round(hi - hiHalf, 2) },
  ];
}

export function buildMatrixSpaceAllocation(project: ProjectState): SpaceAllocationRow[] {
  const settings = project.plant.spaceSettings;
  const totalArea = Math.max(0, settings.lineLengthFt) * Math.max(0, settings.lineWidthFt);
  const distribution = settings.processDistribution ?? {};
  const entries = Object.entries(distribution).filter(([, percent]) => Number(percent) > 0);
  return entries.map(([process, percent], index) => ({
    id: `matrix-${index}`,
    floor: 'F1',
    process: normalizeSpaceProcessName(process),
    areaSqft: round(totalArea * (Number(percent) / 100), 2),
    ratePerSqft: settings.spaceRatePerSqft,
    monthlyCost: null,
  }));
}

/**
 * Computes monthly depreciation cost for a single equipment item.
 *
 * Priority order:
 *  1. `costPerMonth` if explicitly set (e.g. lease / pre-negotiated value)
 *  2. Straight-line depreciation: (qty × unitPrice) / (depreciationYears × 12)
 *
 * Returns 0 if the result is invalid or zero-length depreciation period.
 */
export function equipmentMonthlyCost(item: EquipmentItem): number {
  if (item.costPerMonth !== undefined && item.costPerMonth !== null && Number.isFinite(item.costPerMonth)) {
    return Math.max(0, Number(item.costPerMonth));
  }
  const years = Math.max(0, Number(item.depreciationYears) || 0);
  if (years === 0) return 0;
  return round((Math.max(0, Number(item.qty) || 0) * Math.max(0, Number(item.unitPrice) || 0)) / (years * 12));
}

/**
 * Computes the effective monthly floor-space cost for a single allocation row.
 *
 * Area is adjusted by the `spaceAreaMultiplier` (common-area loading factor):
 * - When `buildingAreaOverrideSqft` is set, the multiplier is applied in *reverse*
 *   (the override is already the gross building area, so we back out the net floor area).
 * - Otherwise the multiplier inflates net area → gross billed area.
 *
 * Returns 0 if the result is invalid.
 */
export function spaceMonthlyCost(row: SpaceAllocationRow, project: ProjectState): number {
  if (row.monthlyCost !== undefined && row.monthlyCost !== null && Number.isFinite(row.monthlyCost)) {
    return Math.max(0, Number(row.monthlyCost));
  }
  const settings = project.plant.spaceSettings;
  const rate = row.ratePerSqft ?? settings.spaceRatePerSqft;
  const rawArea = Math.max(0, Number(row.areaSqft) || 0);
  const effectiveArea = settings.buildingAreaOverrideSqft > 0
    ? rawArea / Math.max(1, settings.spaceAreaMultiplier)
    : rawArea * Math.max(1, settings.spaceAreaMultiplier);
  return round(effectiveArea * Math.max(0, Number(rate) || 0));
}

function groupSumByField(rows: LaborBreakdownRow[], field: 'process' | 'department'): GroupedBreakdownRow[] {
  const grouped = new Map<string, number>();
  rows.forEach((row) => {
    const key = String(row[field] || 'Unassigned');
    grouped.set(key, round((grouped.get(key) || 0) + row.costPerUnit));
  });
  return Array.from(grouped.entries()).map(([key, total]) => ({ key, total }));
}

/**
 * Distributes a pool of labor rows into per-unit cost based on line UPH and efficiency.
 *
 * Formula per row:
 *   costPerUnit = (hourlyRate × effectiveHeadcount) / (uphUsed × efficiency)
 *
 * where `effectiveHeadcount = headcount × allocationPercent` and `uphUsed` is
 * optionally derated by the yield factor when strategy = 'apply_to_capacity'.
 *
 * An allocation percent of `null` is treated as 100 % (full charge to this line).
 */
function laborBreakdown(
  rows: LaborRow[],
  hourlyRate: number,
  lineUph: number,
  efficiency: number,
  strategy: ProjectState['plant']['yield']['strategy'],
  yieldFactor: number,
): LaborBreakdownRow[] {
  return rows.map((row) => {
    const baseUph = row.uphSource === 'override' && row.overrideUph ? Number(row.overrideUph) : lineUph;
    const uphUsed = strategy === 'apply_to_capacity' ? baseUph * yieldFactor : baseUph;
    const allocation = row.allocationPercent ?? 1;
    const effectiveHeadcount = Math.max(0, Number(row.headcount) || 0) * Math.max(0, Number(allocation) || 0);
    const costPerUnit = uphUsed > 0 && efficiency > 0 ? (Math.max(0, hourlyRate) * effectiveHeadcount) / (uphUsed * efficiency) : 0;
    return {
      ...row,
      effectiveHeadcount: round(effectiveHeadcount),
      uphUsed: round(uphUsed),
      costPerUnit: round(costPerUnit),
    };
  });
}

/**
 * Runs the SMT/FBT/F-A process simulation and returns capacity metrics.
 *
 * Key outputs:
 * - `uph` (units per hour): 3600 / bottleneck cycle time
 * - `taktTime`: available seconds per week ÷ weekly demand — the heartbeat of the line
 * - `lineBalanceEfficiency`: ratio of sum(cycle times) to (bottleneck × #steps),
 *    100 % means perfectly balanced; lower values indicate wasted parallel capacity
 * - `weeklyOutput`: theoretical weekly throughput at target utilization
 *
 * Cycle time for placement machines is component-count driven
 * (componentCount / rate × 3600), then divided by OEE and padded by setup loss.
 * All other machine types use a direct rate-to-cycle conversion (3600 / rate).
 */
export function calculateSimulation(project: ProjectState): SimulationResults {
  const derivedMachines = project.plant.showEquipmentSimMapping
    ? deriveMachineRatesFromEquipmentLinks({
        baseMachines: project.machines,
        equipmentList: project.plant.equipmentList,
        extraEquipmentList: project.plant.extraEquipmentList,
        includeExtra: project.plant.includeExtraEquipmentInSimMapping,
      }).machines
    : project.machines;
  const { basicInfo, bomMap, processSteps } = project;
  const machines = derivedMachines;
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
        rawCycleTime: 0,
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
    const setupLossPerBoard = basicInfo.lotSize > 0 ? (basicInfo.setupTimeMin * 60) / basicInfo.lotSize : 0;
    const finalCycle = effectiveCycle + setupLossPerBoard;

    return {
      ...step,
      machineDescription: machine.description,
      cycleTime: round(finalCycle),
      utilization: 0,
      componentCount,
      isBottleneck: false,
      rawCycleTime: round(rawCycle),
    };
  });

  const maxCycleTime = Math.max(...steps.map((step) => step.cycleTime), 0);
  const totalCycleTime = steps.reduce((sum, step) => sum + step.cycleTime, 0);
  const normalized = steps.map((step) => ({
    ...step,
    isBottleneck: maxCycleTime > 0 && step.cycleTime === maxCycleTime,
    utilization: maxCycleTime > 0 ? round((step.cycleTime / maxCycleTime) * 100) : 0,
  }));

  const uph = maxCycleTime > 0 ? round(3600 / maxCycleTime) : 0;
  const lineBalanceEfficiency = maxCycleTime > 0 && normalized.length > 0
    ? round((totalCycleTime / (maxCycleTime * normalized.length)) * 100)
    : 0;
  const weeklyOutput = round(uph * (availableMinutesPerDay / 60) * basicInfo.workDays * basicInfo.targetUtilization);
  const bottleneckProcess = normalized.find((step) => step.isBottleneck)?.process ?? 'N/A';

  return {
    taktTime: round(taktTime),
    uph,
    lineBalanceEfficiency,
    weeklyOutput,
    bottleneckProcess,
    steps: normalized,
  };
}

function resolveSpaceRows(project: ProjectState): SpaceAllocationRow[] {
  const settings = project.plant.spaceSettings;
  if (settings.mode === 'matrix') return buildMatrixSpaceAllocation(project);
  if (settings.split4060Enabled && settings.split4060TotalFloorSqft > 0) {
    return splitFloorSpace4060({
      totalFloorSqft: settings.split4060TotalFloorSqft,
      hiMode: settings.split4060HiMode,
    }).map((row, index) => ({
      id: `split-${index}`,
      floor: 'F1',
      process: row.process,
      areaSqft: row.areaSqft,
      ratePerSqft: settings.spaceRatePerSqft,
      monthlyCost: null,
    }));
  }
  return project.plant.spaceAllocation;
}

/**
 * Computes the full MVA cost breakdown for a given project + simulation run.
 *
 * Cost structure (all values are $/unit unless noted):
 *
 * | Line item           | Driver                                          |
 * |---------------------|-------------------------------------------------|
 * | Direct Labor        | laborBreakdown(directLaborRows, DL rate)        |
 * | Indirect Labor      | laborBreakdown(indirectLaborRows, IDL rate)     |
 * | Equipment Dep.      | equipmentDepPerMonth / monthlyVolume            |
 * | Equipment Maint.    | equipmentDep × maintenanceRatio / volume        |
 * | Space               | spaceMonthlyCost(rows) / monthlyVolume          |
 * | Power               | direct per-unit constant from plant settings    |
 * | Material Attrition  | bomCostPerUnit × attritionRate                  |
 * | Consumables         | direct per-unit constant                        |
 * | SG&A                | (corpBurden + siteMaint + itSecurity) / volume  |
 * | Profit              | bcBomCostPerUnit × profitRate                   |
 * | ICC                 | inventoryValuePerUnit × iccRate                 |
 *
 * Yield strategy options:
 * - `apply_to_capacity` — reduces effective UPH, increasing labor cost/unit
 * - `apply_to_volume`   — reduces output volume, increasing overhead cost/unit
 *
 * @param project - Full project state including plant, labor, and overhead settings
 * @param simulation - Pre-computed simulation results (defaults to a fresh calculation)
 */
export function calculateMva(project: ProjectState, simulation = calculateSimulation(project)): MvaResults {
  const { plant, basicInfo, laborTimeL10 } = project;
  // Guard: meta.fpy may be null (not yet configured). Number(null) === 0 which is a valid
  // yield value and would pass isFinite(), silently collapsing all capacity to zero.
  const rawL10Fpy = laborTimeL10.meta.fpy;
  const l10Fpy = (rawL10Fpy !== null && rawL10Fpy !== undefined) ? Number(rawL10Fpy) : NaN;
  const fpyEffective = plant.yield.useL10Fpy && Number.isFinite(l10Fpy) && l10Fpy > 0
    ? clamp01(l10Fpy, 1)
    : clamp01(plant.yield.fpy, 1);
  const vpyEffective = clamp01(plant.yield.vpy, 1);
  const yieldFactor = round(fpyEffective * vpyEffective);
  const lineUphRaw = simulation.uph;
  const lineUphUsedForCapacity = plant.yield.strategy === 'apply_to_capacity' ? round(lineUphRaw * yieldFactor) : lineUphRaw;

  const monthlyVolume = plant.mvaVolume.demandMode === 'monthly'
    ? Math.max(0, Number(plant.mvaVolume.rfqQtyPerMonth) || 0)
    : round((Math.max(0, Number(plant.mvaVolume.weeklyDemand) || 0) * Math.max(1, Number(plant.mvaVolume.workDaysPerMonth) || 0)) / Math.max(1, Number(plant.mvaVolume.workDaysPerWeek) || 0));
  const inferredVolume = monthlyVolume > 0
    ? monthlyVolume
    : round(lineUphUsedForCapacity * basicInfo.shiftsPerDay * basicInfo.hoursPerShift * plant.mvaVolume.workDaysPerMonth * basicInfo.targetUtilization);
  const volume = Math.max(0, inferredVolume);

  const efficiency = Math.max(0, Number(plant.mvaRates.efficiency) || 0);
  const activeOverhead = plant.overheadByProcess[plant.processType];
  const dlBreakdown = laborBreakdown(plant.directLaborRows, plant.mvaRates.directLaborHourlyRate, lineUphRaw, efficiency, plant.yield.strategy, yieldFactor);
  const idlBreakdown = laborBreakdown(plant.indirectLaborRows, plant.mvaRates.indirectLaborHourlyRate, lineUphRaw, efficiency, plant.yield.strategy, yieldFactor);
  const dlPerUnit = round(dlBreakdown.reduce((sum, row) => sum + row.costPerUnit, 0));
  const idlPerUnit = round(idlBreakdown.reduce((sum, row) => sum + row.costPerUnit, 0));

  const equipmentComputedBase: EquipmentComputedRow[] = plant.equipmentList.map((item) => ({ ...item, source: 'base', costPerMonthUsed: equipmentMonthlyCost(item) }));
  const equipmentComputedExtra: EquipmentComputedRow[] = plant.extraEquipmentList.map((item) => ({ ...item, source: 'extra', costPerMonthUsed: equipmentMonthlyCost(item) }));
  const totalEquipmentCostPerMonth = round([...equipmentComputedBase, ...equipmentComputedExtra]
    .filter((item) => item.source === 'base' || plant.useExtraEquipmentInTotal)
    .reduce((sum, item) => sum + item.costPerMonthUsed, 0));
  const equipmentDepPerMonthUsed = plant.useEquipmentListTotal && totalEquipmentCostPerMonth > 0
    ? totalEquipmentCostPerMonth
    : Math.max(0, Number(activeOverhead.equipmentDepreciationPerMonth) || 0);

  const spaceRows = resolveSpaceRows(project);
  const spaceComputed: SpaceComputedRow[] = spaceRows.map((row) => ({ ...row, monthlyCostUsed: spaceMonthlyCost(row, project) }));
  const spaceFloorTotalSqft = round(spaceComputed.reduce((sum, row) => sum + Math.max(0, Number(row.areaSqft) || 0), 0), 2);
  const spaceBuildingAreaSqft = project.plant.spaceSettings.buildingAreaOverrideSqft > 0
    ? project.plant.spaceSettings.buildingAreaOverrideSqft
    : round(spaceFloorTotalSqft * Math.max(1, project.plant.spaceSettings.spaceAreaMultiplier), 2);
  const spaceMonthlyCostUsed = plant.spaceSettings.useSpaceAllocationTotal && spaceComputed.length > 0
    ? round(spaceComputed.reduce((sum, row) => sum + row.monthlyCostUsed, 0))
    : Math.max(0, Number(activeOverhead.spaceMonthlyCost) || 0);

  const equipmentDepPerUnit = volume > 0 ? round(equipmentDepPerMonthUsed / volume) : 0;
  const equipmentMaintPerUnit = volume > 0 ? round((equipmentDepPerMonthUsed * Math.max(0, Number(activeOverhead.equipmentMaintenanceRatio) || 0)) / volume) : 0;
  const spacePerUnit = volume > 0 ? round(spaceMonthlyCostUsed / volume) : 0;
  const powerPerUnit = round(Math.max(0, Number(activeOverhead.powerCostPerUnit) || 0));
  const totalOverheadPerUnit = round(equipmentDepPerUnit + equipmentMaintPerUnit + spacePerUnit + powerPerUnit);

  const materialAttritionPerUnit = round(Math.max(0, Number(plant.materials.bomCostPerUnit) || 0) * clamp01(Number(plant.materials.attritionRate) || 0));
  const consumablesPerUnit = round(Math.max(0, Number(plant.materials.consumablesCostPerUnit) || 0));
  const corporateBurdenPerUnit = volume > 0 ? round((Math.max(0, Number(plant.sga.corporateBurdenPerMonth) || 0)) / volume) : 0;
  const siteMaintenancePerUnit = volume > 0 ? round((Math.max(0, Number(plant.sga.siteMaintenancePerMonth) || 0)) / volume) : 0;
  const itSecurityPerUnit = volume > 0 ? round((Math.max(0, Number(plant.sga.itSecurityPerMonth) || 0)) / volume) : 0;
  const totalSgaPerUnit = round(corporateBurdenPerUnit + siteMaintenancePerUnit + itSecurityPerUnit);
  const profitPerUnit = round(Math.max(0, Number(plant.profit.bcBomCostPerUnit) || 0) * clamp01(Number(plant.profit.profitRate) || 0));
  const iccPerUnit = round(Math.max(0, Number(plant.icc.inventoryValuePerUnit) || 0) * clamp01(Number(plant.icc.iccRate) || 0));

  const costLines = [
    { key: 'dl', label: 'Direct Labor / Unit', value: dlPerUnit },
    { key: 'idl', label: 'Indirect Labor / Unit', value: idlPerUnit },
    { key: 'equipDep', label: 'Equipment Depreciation / Unit', value: equipmentDepPerUnit },
    { key: 'equipMaint', label: 'Equipment Maintenance / Unit', value: equipmentMaintPerUnit },
    { key: 'space', label: 'Space / Unit', value: spacePerUnit },
    { key: 'power', label: 'Power / Unit', value: powerPerUnit },
    { key: 'materialAttrition', label: 'Material Attrition / Unit', value: materialAttritionPerUnit },
    { key: 'consumables', label: 'Consumables / Unit', value: consumablesPerUnit },
    { key: 'sga', label: 'SGA / Unit', value: totalSgaPerUnit },
    { key: 'profit', label: 'Profit / Unit', value: profitPerUnit },
    { key: 'icc', label: 'ICC / Unit', value: iccPerUnit },
  ];
  const totalPerUnit = round(costLines.reduce((sum, line) => sum + line.value, 0));

  const warnings: string[] = [];
  if (lineUphRaw <= 0) warnings.push('Simulation UPH is zero. Review machine rates and process routing.');
  if (efficiency <= 0) warnings.push('Efficiency must be greater than zero for labor costing.');
  if (volume <= 0) warnings.push('Monthly volume is zero. Overhead cannot be distributed.');
  if (simulation.steps.some((step) => step.machineDescription === 'Unknown')) {
    warnings.push('One or more process steps are not mapped to a known machine group.');
  }
  if (!project.confirmation.decision) warnings.push('Review gate is not confirmed. Exports should stay blocked.');

  return {
    monthlyVolume: round(volume),
    lineUphRaw,
    lineUphUsedForCapacity,
    yield: {
      fpy: fpyEffective,
      vpy: vpyEffective,
      yieldFactor,
      strategy: plant.yield.strategy,
    },
    dlBreakdown,
    idlBreakdown,
    directLabor: dlBreakdown,
    indirectLabor: idlBreakdown,
    dlPerUnit,
    idlPerUnit,
    grouped: {
      dlByProcess: groupSumByField(dlBreakdown, 'process'),
      idlByDepartment: groupSumByField(idlBreakdown, 'department'),
    },
    equipmentList: [...equipmentComputedBase, ...equipmentComputedExtra],
    spaceAllocation: spaceComputed,
    materials: {
      materialAttritionPerUnit,
      consumablesPerUnit,
    },
    sga: {
      corporateBurdenPerUnit,
      siteMaintenancePerUnit,
      itSecurityPerUnit,
      totalSgaPerUnit,
    },
    profit: {
      profitPerUnit,
    },
    icc: {
      iccPerUnit,
    },
    overhead: {
      equipmentDepPerMonthUsed,
      equipmentDepPerUnit,
      equipmentMaintPerUnit,
      totalEquipmentCostPerMonth,
      spaceMonthlyCostUsed,
      spacePerUnit,
      powerPerUnit,
      totalOverheadPerUnit,
      spaceFloorTotalSqft,
      spaceBuildingAreaSqft,
    },
    costLines,
    totalPerUnit,
    warnings,
  };
}

function equipmentKey(item: Pick<EquipmentItem, 'process' | 'item'>): string {
  return `${String(item.process || '').trim()}::${String(item.item || '').trim()}`;
}

/**
 * Compares a line-standard equipment template against the current baseline and
 * extra equipment lists, returning per-item delta rows and aggregate totals.
 *
 * Rows are keyed by `process::item` to enable cross-list matching. A negative
 * `deltaCostPerMonth` means the current setup costs less than the template
 * (favorable); positive means over-template spend.
 *
 * Typical use: Plant Equipment page → "vs Template" delta column.
 *
 * @param template - Reference line standard (null → all template values are 0)
 * @param equipmentList - Current project baseline equipment
 * @param extraEquipmentList - Additional equipment rows (future expansion)
 */
export function computeEquipmentDelta({
  template,
  equipmentList,
  extraEquipmentList,
}: {
  template?: { equipmentList?: EquipmentItem[] } | null;
  equipmentList: EquipmentItem[];
  extraEquipmentList: EquipmentItem[];
}): EquipmentDeltaResult {
  const map = new Map<string, {
    process: string;
    item: string;
    templateQty: number;
    baselineQty: number;
    extraQty: number;
    templateCostPerMonth: number;
    baselineCostPerMonth: number;
    extraCostPerMonth: number;
  }>();

  const apply = (source: 'template' | 'baseline' | 'extra', items: EquipmentItem[]) => {
    items.forEach((item) => {
      const key = equipmentKey(item);
      const current = map.get(key) || {
        process: item.process,
        item: item.item,
        templateQty: 0,
        baselineQty: 0,
        extraQty: 0,
        templateCostPerMonth: 0,
        baselineCostPerMonth: 0,
        extraCostPerMonth: 0,
      };
      const qty = Math.max(0, Number(item.qty) || 0);
      const cost = equipmentMonthlyCost(item);
      if (source === 'template') {
        current.templateQty += qty;
        current.templateCostPerMonth += cost;
      }
      if (source === 'baseline') {
        current.baselineQty += qty;
        current.baselineCostPerMonth += cost;
      }
      if (source === 'extra') {
        current.extraQty += qty;
        current.extraCostPerMonth += cost;
      }
      map.set(key, current);
    });
  };

  apply('template', template?.equipmentList ?? []);
  apply('baseline', equipmentList);
  apply('extra', extraEquipmentList);

  const rows = Array.from(map.values()).map((row) => {
    const totalCostPerMonth = round(row.baselineCostPerMonth + row.extraCostPerMonth);
    return {
      ...row,
      templateCostPerMonth: round(row.templateCostPerMonth),
      baselineCostPerMonth: round(row.baselineCostPerMonth),
      extraCostPerMonth: round(row.extraCostPerMonth),
      totalCostPerMonth,
      deltaCostPerMonth: round(totalCostPerMonth - row.templateCostPerMonth),
    };
  }).sort((left, right) => `${left.process}${left.item}`.localeCompare(`${right.process}${right.item}`));

  return {
    rows,
    totals: {
      templateCostPerMonth: round(rows.reduce((sum, row) => sum + row.templateCostPerMonth, 0)),
      baselineCostPerMonth: round(rows.reduce((sum, row) => sum + row.baselineCostPerMonth, 0)),
      extraCostPerMonth: round(rows.reduce((sum, row) => sum + row.extraCostPerMonth, 0)),
      totalCostPerMonth: round(rows.reduce((sum, row) => sum + row.totalCostPerMonth, 0)),
      deltaCostPerMonth: round(rows.reduce((sum, row) => sum + row.deltaCostPerMonth, 0)),
    },
  };
}

/**
 * Derives machine-rate overrides from equipment list `simMachineGroup` links.
 *
 * Each equipment item can optionally carry `simMachineGroup` (which group in
 * the simulation it feeds) and `simParallelQty` (how many units run in
 * parallel). The derived rate for a group is:
 *
 *   derivedRate = sum of (simRateOverride OR baseRate × simParallelQty)
 *                 across all linked items in that group
 *
 * This allows the Plant Equipment setup to drive simulation capacity without
 * manual duplication in the Machine Rates table.
 *
 * @param baseMachines - Fallback machine list when no item is linked to a group
 * @param equipmentList - Baseline equipment with optional sim-linking fields
 * @param extraEquipmentList - Additional equipment (included when `includeExtra` is true)
 * @param includeExtra - Whether extra-equipment items contribute to derived rates
 * @returns `{ machines, rows }` — updated machine array and derivation audit rows
 */
export function deriveMachineRatesFromEquipmentLinks({
  baseMachines = defaultMachines,
  equipmentList,
  extraEquipmentList,
  includeExtra,
}: {
  baseMachines?: Machine[];
  equipmentList: EquipmentItem[];
  extraEquipmentList: EquipmentItem[];
  includeExtra: boolean;
}) {
  const sourceItems = includeExtra ? [...equipmentList, ...extraEquipmentList] : equipmentList;
  const baseRateLookup = Object.fromEntries(baseMachines.map((machine) => [machine.group, Math.max(0, Number(machine.rate) || 0)]));
  const totals = new Map<string, {
    machineGroup: string;
    currentRate: number;
    linkedItems: string[];
    totalParallelQty: number;
    derivedRate: number;
  }>();
  sourceItems.forEach((item) => {
    const group = String(item.simMachineGroup || '').trim();
    if (!group) return;
    const qty = Math.max(0, Number(item.simParallelQty) || 0);
    const override = Math.max(0, Number(item.simRateOverride) || 0);
    const baseRate = Math.max(0, Number(baseMachineRateByGroup[group]) || 0);
    const derivedRate = override > 0 ? override : baseRate * qty;
    if (derivedRate <= 0) return;
    const current = totals.get(group) || {
      machineGroup: group,
      currentRate: baseRateLookup[group] ?? 0,
      linkedItems: [],
      totalParallelQty: 0,
      derivedRate: 0,
    };
    current.totalParallelQty += qty;
    current.derivedRate = round(current.derivedRate + derivedRate);
    current.linkedItems.push(`${item.process || 'Unassigned'} / ${item.item || 'Unnamed'}`);
    totals.set(group, current);
  });

  const rows = Array.from(totals.values())
    .map((row) => ({
      ...row,
      linkedItems: Array.from(new Set(row.linkedItems)),
    }))
    .sort((left, right) => left.machineGroup.localeCompare(right.machineGroup));

  return {
    machines: baseMachines.map((machine) => {
      const derivedRate = totals.get(machine.group)?.derivedRate;
      return derivedRate && derivedRate > 0 ? { ...machine, rate: derivedRate } : machine;
    }),
    rows,
  };
}

export function calcL10StationComputed(station: Partial<L10Station>, meta: L10StationMeta): L10Station {
  const cycleTimeSec = station.cycleTimeSec === null || station.cycleTimeSec === undefined ? null : Number(station.cycleTimeSec);
  const allowanceFactor = Number.isFinite(Number(station.allowanceFactor)) ? Number(station.allowanceFactor) : 1;
  const parallelStations = Number.isFinite(Number(station.parallelStations)) && Number(station.parallelStations) > 0 ? Number(station.parallelStations) : 1;
  const avgCycleTimeSec = cycleTimeSec !== null && cycleTimeSec > 0 ? round((cycleTimeSec * allowanceFactor) / parallelStations) : null;
  const uph = avgCycleTimeSec !== null && avgCycleTimeSec > 0 ? round(3600 / avgCycleTimeSec) : null;

  // Guard: meta fields use number|null. Number(null)===0 passes isFinite(), which would
  // silently treat "not configured" as zero and collapse all capacity fields to 0.
  const hoursPerShift = (meta.hoursPerShift !== null && meta.hoursPerShift !== undefined) ? Number(meta.hoursPerShift) : null;
  const shiftsPerDay = (meta.shiftsPerDay !== null && meta.shiftsPerDay !== undefined) ? Number(meta.shiftsPerDay) : null;
  const workDaysPerWeek = (meta.workDaysPerWeek !== null && meta.workDaysPerWeek !== undefined) ? Number(meta.workDaysPerWeek) : null;
  const workDaysPerMonth = (meta.workDaysPerMonth !== null && meta.workDaysPerMonth !== undefined) ? Number(meta.workDaysPerMonth) : null;

  const perShiftCapa = uph !== null && hoursPerShift !== null ? round(uph * hoursPerShift, 2) : null;
  const dailyCapa = perShiftCapa !== null && shiftsPerDay !== null ? round(perShiftCapa * shiftsPerDay, 2) : null;
  const weeklyCapa = dailyCapa !== null && workDaysPerWeek !== null ? round(dailyCapa * workDaysPerWeek, 2) : null;
  const monthlyCapa = dailyCapa !== null && workDaysPerMonth !== null ? round(dailyCapa * workDaysPerMonth, 2) : null;

  return {
    id: station.id || `l10-${Date.now()}`,
    name: station.name || 'New Station',
    laborHc: Number(station.laborHc) || 0,
    parallelStations,
    cycleTimeSec,
    allowanceFactor,
    touchTimeMin: station.touchTimeMin ?? null,
    handlingTimeSec: station.handlingTimeSec ?? null,
    oneManMultiMachineCount: station.oneManMultiMachineCount ?? null,
    timeMinPerCycle: station.timeMinPerCycle ?? null,
    runPerDay: station.runPerDay ?? null,
    avgCycleTimeSec,
    uph,
    perShiftCapa,
    dailyCapa,
    weeklyCapa,
    monthlyCapa,
  };
}

export function calcL6StationComputed(station: Partial<L6Station>): L6Station {
  const cycleTimeSec = station.cycleTimeSec === null || station.cycleTimeSec === undefined ? null : Number(station.cycleTimeSec);
  const allowanceRateRaw = station.allowanceRate === null || station.allowanceRate === undefined ? null : Number(station.allowanceRate);
  const allowanceFactor = allowanceRateRaw === null
    ? 1
    : (allowanceRateRaw > 1 ? 1 + allowanceRateRaw / 100 : 1 + allowanceRateRaw);
  const parallelStations = Number.isFinite(Number(station.parallelStations)) && Number(station.parallelStations) > 0 ? Number(station.parallelStations) : 1;
  const avgCycleTimeSec = cycleTimeSec !== null && cycleTimeSec > 0 ? round((cycleTimeSec * allowanceFactor) / parallelStations) : null;
  const uph = avgCycleTimeSec !== null && avgCycleTimeSec > 0 ? round(3600 / avgCycleTimeSec) : null;
  const laborHc = station.dlOnline !== null && station.dlOnline !== undefined ? Number(station.dlOnline) || 0 : Number(station.laborHc) || 0;
  const stdManHours = uph && uph > 0 ? round(laborHc / uph, 4) : (station.stdManHours ?? null);

  return {
    id: station.id || `l6-${Date.now()}`,
    stationNo: station.stationNo,
    name: station.name || 'New Process',
    laborHc,
    parallelStations,
    cycleTimeSec,
    allowanceRate: allowanceRateRaw,
    stdManHours,
    vpy: station.vpy ?? null,
    shiftRate: station.shiftRate ?? null,
    dlOnline: station.dlOnline ?? laborHc,
    avgCycleTimeSec,
    uph,
    isTotal: station.isTotal ?? false,
  };
}

export function buildL10SummaryStationCostTable({
  directLaborBreakdown,
  equipmentDepPerUnit,
  equipmentMaintPerUnit,
}: {
  directLaborBreakdown: LaborBreakdownRow[];
  equipmentDepPerUnit: number;
  equipmentMaintPerUnit: number;
}) {
  const rows = Array.isArray(directLaborBreakdown) ? directLaborBreakdown : [];
  const machineCost = rows.length > 0 ? round((equipmentDepPerUnit + equipmentMaintPerUnit) / rows.length) : 0;
  return rows.map((row) => ({
    stationName: row.name,
    machineCost,
    laborCost: round(row.costPerUnit),
    totalCost: round(machineCost + row.costPerUnit),
  }));
}

export function summarizeForCsv(project: ProjectState, simulation = calculateSimulation(project), mva = calculateMva(project, simulation)): string[][] {
  return [
    ['Section', 'Item', 'Value'],
    ['Simulation', 'Model', project.basicInfo.modelName],
    ['Simulation', 'UPH', String(simulation.uph)],
    ['Simulation', 'Takt Time', String(simulation.taktTime)],
    ['Simulation', 'Weekly Output', String(simulation.weeklyOutput)],
    ['Simulation', 'Bottleneck', simulation.bottleneckProcess],
    ['MVA', 'Monthly Volume', String(mva.monthlyVolume)],
    ['MVA', 'Line UPH Raw', String(mva.lineUphRaw)],
    ['MVA', 'Line UPH Used', String(mva.lineUphUsedForCapacity)],
    ['MVA', 'Yield Strategy', mva.yield.strategy],
    ['MVA', 'Yield Factor', String(mva.yield.yieldFactor)],
    ...mva.costLines.map((line) => ['MVA', line.label, String(line.value)]),
    ['MVA', 'Total / Unit', String(mva.totalPerUnit)],
    ['Confirmation', 'Decision', project.confirmation.decision ?? ''],
    ['Confirmation', 'Reviewer', project.confirmation.reviewer],
  ];
}

/**
 * Resolves the active indirect-labor row set for a given process type.
 *
 * When `mode` is `'auto'`, the process type from `plant.processType` is used
 * to select the correct `idlRowsByMode` bucket. Explicit mode values (`'L10'`
 * or `'L6'`) override the plant-wide process type, which is needed when
 * computing process-specific summaries (e.g. the L10 / L6 MPM pages).
 *
 * @param plant - The plant sub-state containing `idlRowsByMode` and `idlUiMode`
 * @param mode  - Override mode; defaults to `plant.idlUiMode`
 * @param processType - Process type used when mode is `'auto'`
 */
export function resolveIndirectLaborRows(
  plant: ProjectState['plant'],
  mode: ProjectState['plant']['idlUiMode'] = plant.idlUiMode,
  processType: ProjectState['plant']['processType'] = plant.processType,
): LaborRow[] {
  const resolvedMode = mode === 'auto' ? processType : mode;
  return (plant.idlRowsByMode[resolvedMode] ?? []).map((row) => ({ ...row }));
}

/**
 * Creates a shallow project snapshot scoped to a specific process type.
 *
 * This is the primary adapter between the single shared `ProjectState` and the
 * per-process summary pages (L10 / L6). It overrides `plant.processType` and,
 * when IDL mode is `'auto'`, backfills `plant.indirectLaborRows` so that any
 * downstream consumer (e.g. `calculateMva`) sees the correct IDL rows without
 * needing to know which process is active.
 *
 * Used by `useProcessSummary` to compute L10 and L6 summaries in parallel
 * without mutating the canonical project state.
 *
 * @param project - The full canonical project state
 * @param processType - The target process ('L10' | 'L6')
 * @returns A new project reference with `plant.processType` set accordingly
 */
export function projectForProcess(project: ProjectState, processType: ProjectState['plant']['processType']): ProjectState {
  const plant: ProjectState['plant'] = { ...project.plant, processType };
  if (plant.idlUiMode === 'auto') {
    plant.indirectLaborRows = resolveIndirectLaborRows(plant, 'auto', processType);
  }
  return { ...project, plant };
}
