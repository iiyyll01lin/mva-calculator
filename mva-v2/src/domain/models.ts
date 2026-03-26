/** Product line classification used by the portfolio management layer. */
export type ProductLine = 'Server' | 'Switch' | 'Storage' | 'Other';

/**
 * Standardised SKU descriptor within a product line.
 * The 'Custom' sentinel allows free-form entries from the dashboard form.
 */
export type SkuType = '1U-Standard' | '1U-HighEnd' | '2U-Standard' | '2U-HighEnd' | '4U-HighEnd' | 'Custom';

export type TabId =
  | 'basic'
  | 'machine_rates'
  | 'bom_map'
  | 'model_process'
  | 'simulation_results'
  | 'mva_plant_rates'
  | 'mva_plant_env'
  | 'mva_plant_equipment'
  | 'mva_plant_space'
  | 'mva_plant_dloh_idl'
  | 'mpm_l10'
  | 'mpm_l6'
  | 'mva_labor_l10'
  | 'mva_labor_l6'
  | 'mva_summary_l10'
  | 'mva_summary_l6';

export interface BasicInfo {
  modelName: string;
  workDays: number;
  demandWeekly: number;
  shiftsPerDay: number;
  hoursPerShift: number;
  breaksPerShiftMin: number;
  targetUtilization: number;
  fpy: number;
  vpy: number;
  boardsPerPanel: number;
  hasTopSide: boolean;
  hasBottomSide: boolean;
  lotSize: number;
  setupTimeMin: number;
  oeePlacement: number;
  oeeOthers: number;
}

export interface Machine {
  id: string;
  group: string;
  description: string;
  rate: number;
  type: 'fixed' | 'placement';
}

export interface BomRow {
  id: string;
  side: 'Top' | 'Bottom';
  bucket: string;
  count: number;
  machineGroup: string;
}

export interface ProcessStep {
  step: number;
  process: string;
  side: string;
}

export interface LaborRow {
  id: string;
  name: string;
  process?: string;
  department?: string;
  role?: string;
  rrNote?: string;
  headcount: number;
  allocationPercent?: number | null;
  uphSource: 'line' | 'override';
  overrideUph?: number | null;
}

export interface EquipmentItem {
  id: string;
  process: string;
  item: string;
  qty: number;
  unitPrice: number;
  depreciationYears: number;
  costPerMonth?: number | null;
  simMachineGroup?: string;
  simParallelQty?: number | null;
  simRateOverride?: number | null;
}

export interface SpaceAllocationRow {
  id: string;
  floor: string;
  process: string;
  areaSqft: number;
  ratePerSqft?: number | null;
  monthlyCost?: number | null;
}

export interface MvaRates {
  directLaborHourlyRate: number;
  indirectLaborHourlyRate: number;
  efficiency: number;
}

export interface MvaVolume {
  demandMode: 'monthly' | 'weekly';
  rfqQtyPerMonth: number;
  weeklyDemand: number;
  workDaysPerWeek: number;
  workDaysPerMonth: number;
}

export interface MvaOverhead {
  equipmentAverageUsefulLifeYears: number;
  equipmentDepreciationPerMonth: number;
  equipmentMaintenanceRatio: number;
  spaceMonthlyCost: number;
  powerCostPerUnit: number;
}

export interface YieldSettings {
  strategy: 'ignore' | 'apply_to_capacity';
  fpy: number;
  vpy: number;
  useL10Fpy?: boolean;
}

export interface MaterialsSettings {
  bomCostPerUnit: number;
  attritionRate: number;
  consumablesCostPerUnit: number;
}

export interface SgaSettings {
  corporateBurdenPerMonth: number;
  siteMaintenancePerMonth: number;
  itSecurityPerMonth: number;
}

export interface ProfitSettings {
  bcBomCostPerUnit: number;
  profitRate: number;
}

export interface IccSettings {
  inventoryValuePerUnit: number;
  iccRate: number;
}

export interface SpaceSettings {
  useSpaceAllocationTotal: boolean;
  spaceRatePerSqft: number;
  spaceAreaMultiplier: number;
  buildingAreaOverrideSqft: number;
  mode: 'matrix' | 'manual';
  lineLengthFt: number;
  lineWidthFt: number;
  floorAreas: Record<string, number>;
  processDistribution: Record<string, number>;
  split4060Enabled: boolean;
  split4060TotalFloorSqft: number;
  split4060HiMode: 'FA' | 'FBT' | 'FA_FBT';
}

export interface L10StationMeta {
  hoursPerShift?: number | null;
  targetTime?: number | null;
  shiftsPerDay?: number | null;
  workDaysPerWeek?: number | null;
  workDaysPerMonth?: number | null;
  fpy?: number | null;
}

export interface L10Station {
  id: string;
  name: string;
  laborHc: number;
  parallelStations: number;
  cycleTimeSec: number | null;
  allowanceFactor: number;
  touchTimeMin?: number | null;
  handlingTimeSec?: number | null;
  oneManMultiMachineCount?: number | null;
  timeMinPerCycle?: number | null;
  runPerDay?: number | null;
  avgCycleTimeSec?: number | null;
  uph?: number | null;
  perShiftCapa?: number | null;
  dailyCapa?: number | null;
  weeklyCapa?: number | null;
  monthlyCapa?: number | null;
}

export interface L10LaborTimeEstimation {
  meta: L10StationMeta;
  stations: L10Station[];
  source: string;
}

export interface L6HeaderInfo {
  category?: string;
  productionLine?: string;
  pcbParams?: string;
  printing?: string;
  lineChange?: string;
  ictToFbt?: string;
  annualDemand?: number;
  monthlyDemand?: number;
  multiPanelQty?: number;
  lineCapabilityPerShift?: number;
  exRateRmbUsd?: number;
  serviceHourlyWage?: number;
  manufacturingTimePerUnit?: number;
  outputPerLinePerShift?: number;
}

export interface L6Station {
  id: string;
  stationNo?: number;
  name: string;
  laborHc: number;
  parallelStations: number;
  cycleTimeSec: number | null;
  allowanceRate: number | null;
  stdManHours?: number | null;
  vpy?: number | null;
  shiftRate?: number | null;
  dlOnline?: number | null;
  avgCycleTimeSec?: number | null;
  uph?: number | null;
  isTotal?: boolean;
}

export interface L6LaborSegment {
  id: string;
  name: string;
  stations: L6Station[];
}

export interface L6LaborTimeEstimation {
  header: L6HeaderInfo;
  segments: L6LaborSegment[];
  stations: L6Station[];
  source: string;
}

export interface ProductSetupL10 {
  processType: 'L10';
  bu: string;
  customer: string;
  projectName: string;
  modelName: string;
  sizeMm: string;
  weightKgPerTool: number | null;
  testTime: {
    handlingSec: number;
    functionSec: number;
    totalSec: number;
  };
  yield: number | null;
  rfqQtyPerMonth: number | null;
  probeLifeCycle: number | null;
  rfqQtyLifeCycle: number | null;
  workDaysPerYear: number | null;
  workDaysPerMonth: number | null;
  workDaysPerWeek: number | null;
  shiftsPerDay: number | null;
  hoursPerShift: number | null;
  uph: number | null;
  ctSec: number | null;
  source: string;
}

export interface ProductSetupL6 {
  processType: 'L6';
  bu: string;
  customer: string;
  pn: string;
  sku?: string;
  modelName?: string;
  rfqQtyPerMonth: number | null;
  boardsPerPanel: number;
  pcbSize: string;
  stationPath: string;
  side1PartsCount: number | null;
  side2PartsCount: number | null;
  offLineBlastingPartCount: number | null;
  dipPartsType: string;
  selectiveWs: string;
  assemblyPart: string;
  routingTimesSec: Record<string, number>;
  testTime: {
    handlingSec: number;
    functionSec: number;
  };
  source: string;
}

export interface ConfirmationState {
  decision: 'OK' | 'NG' | null;
  reviewer: string;
  comment: string;
  decidedAt: string | null;
}

export interface LineStandard {
  id: string;
  name: string;
  source: string;
  equipmentCostPerMonth?: number;
  equipmentList: EquipmentItem[];
}

export interface PlantInputs {
  processType: 'L6' | 'L10';
  plantOperationDaysPerYear: number;
  mvaRates: MvaRates;
  mvaVolume: MvaVolume;
  overheadByProcess: Record<'L6' | 'L10', MvaOverhead>;
  yield: YieldSettings;
  directLaborRows: LaborRow[];
  indirectLaborRows: LaborRow[];
  idlUiMode: 'auto' | 'L10' | 'L6';
  idlRowsByMode: Record<'L10' | 'L6', LaborRow[]>;
  equipmentList: EquipmentItem[];
  extraEquipmentList: EquipmentItem[];
  useEquipmentListTotal: boolean;
  useExtraEquipmentInTotal: boolean;
  spaceAllocation: SpaceAllocationRow[];
  spaceSettings: SpaceSettings;
  materials: MaterialsSettings;
  sga: SgaSettings;
  profit: ProfitSettings;
  icc: IccSettings;
  showEquipmentSimMapping: boolean;
  includeExtraEquipmentInSimMapping: boolean;
}

export interface ProjectState {
  /** UUID assigned at project creation by the portfolio layer (Phase 8). */
  projectId?: string;
  /** Product line classification (Portfolio: Phase 8). */
  productLine?: ProductLine;
  /** SKU descriptor within the product line (Portfolio: Phase 8). */
  sku?: SkuType | string;
  /** ISO-8601 timestamp — updated on every portfolio save (Portfolio: Phase 8). */
  lastModified?: string;
  schemaVersion: number;
  basicInfo: BasicInfo;
  machines: Machine[];
  bomMap: BomRow[];
  processSteps: ProcessStep[];
  plant: PlantInputs;
  productL10: ProductSetupL10;
  productL6: ProductSetupL6;
  laborTimeL10: L10LaborTimeEstimation;
  laborTimeL6: L6LaborTimeEstimation;
  confirmation: ConfirmationState;
  lineStandards: LineStandard[];
}

export interface SimulationStepResult extends ProcessStep {
  machineDescription: string;
  cycleTime: number;
  utilization: number;
  componentCount: number;
  isBottleneck: boolean;
  rawCycleTime: number;
}

export interface SimulationResults {
  taktTime: number;
  uph: number;
  lineBalanceEfficiency: number;
  weeklyOutput: number;
  bottleneckProcess: string;
  steps: SimulationStepResult[];
}

export interface CostLine {
  key: string;
  label: string;
  value: number;
}

export interface LaborBreakdownRow extends LaborRow {
  effectiveHeadcount: number;
  uphUsed: number;
  costPerUnit: number;
}

export interface EquipmentComputedRow extends EquipmentItem {
  source: 'base' | 'extra';
  costPerMonthUsed: number;
}

export interface SpaceComputedRow extends SpaceAllocationRow {
  monthlyCostUsed: number;
}

export interface GroupedBreakdownRow {
  key: string;
  total: number;
}

export interface EquipmentDeltaRow {
  process: string;
  item: string;
  templateQty: number;
  baselineQty: number;
  extraQty: number;
  templateCostPerMonth: number;
  baselineCostPerMonth: number;
  extraCostPerMonth: number;
  totalCostPerMonth: number;
  deltaCostPerMonth: number;
}

export interface EquipmentDeltaResult {
  rows: EquipmentDeltaRow[];
  totals: {
    templateCostPerMonth: number;
    baselineCostPerMonth: number;
    extraCostPerMonth: number;
    totalCostPerMonth: number;
    deltaCostPerMonth: number;
  };
}

export interface MvaResults {
  monthlyVolume: number;
  lineUphRaw: number;
  lineUphUsedForCapacity: number;
  yield: {
    fpy: number;
    vpy: number;
    yieldFactor: number;
    strategy: YieldSettings['strategy'];
  };
  dlBreakdown: LaborBreakdownRow[];
  idlBreakdown: LaborBreakdownRow[];
  directLabor: LaborBreakdownRow[];
  indirectLabor: LaborBreakdownRow[];
  dlPerUnit: number;
  idlPerUnit: number;
  grouped: {
    dlByProcess: GroupedBreakdownRow[];
    idlByDepartment: GroupedBreakdownRow[];
  };
  equipmentList: EquipmentComputedRow[];
  spaceAllocation: SpaceComputedRow[];
  materials: {
    materialAttritionPerUnit: number;
    consumablesPerUnit: number;
  };
  sga: {
    corporateBurdenPerUnit: number;
    siteMaintenancePerUnit: number;
    itSecurityPerUnit: number;
    totalSgaPerUnit: number;
  };
  profit: {
    profitPerUnit: number;
  };
  icc: {
    iccPerUnit: number;
  };
  overhead: {
    equipmentDepPerMonthUsed: number;
    equipmentDepPerUnit: number;
    equipmentMaintPerUnit: number;
    totalEquipmentCostPerMonth: number;
    spaceMonthlyCostUsed: number;
    spacePerUnit: number;
    powerPerUnit: number;
    totalOverheadPerUnit: number;
    spaceFloorTotalSqft: number;
    spaceBuildingAreaSqft: number;
  };
  costLines: CostLine[];
  totalPerUnit: number;
  warnings: string[];
}
