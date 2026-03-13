export type TabId = 'dashboard' | 'simulation' | 'plant' | 'product' | 'reports';

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
  equipmentList: EquipmentItem[];
  extraEquipmentList: EquipmentItem[];
  spaceAllocation: SpaceAllocationRow[];
  spaceSettings: SpaceSettings;
  materials: MaterialsSettings;
  sga: SgaSettings;
  profit: ProfitSettings;
  icc: IccSettings;
}

export interface ProductSetupL10 {
  bu: string;
  customer: string;
  projectName: string;
  modelName: string;
  handlingSec: number;
  functionSec: number;
  yield: number;
  rfqQtyPerMonth: number;
}

export interface ProductSetupL6 {
  bu: string;
  customer: string;
  pn: string;
  rfqQtyPerMonth: number;
  boardsPerPanel: number;
  pcbSize: string;
  stationPath: string;
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
  equipmentList: EquipmentItem[];
}

export interface ProjectState {
  schemaVersion: number;
  basicInfo: BasicInfo;
  machines: Machine[];
  bomMap: BomRow[];
  processSteps: ProcessStep[];
  plant: PlantInputs;
  productL10: ProductSetupL10;
  productL6: ProductSetupL6;
  confirmation: ConfirmationState;
  lineStandards: LineStandard[];
}

export interface SimulationStepResult extends ProcessStep {
  machineDescription: string;
  cycleTime: number;
  utilization: number;
  componentCount: number;
  isBottleneck: boolean;
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

export interface MvaResults {
  monthlyVolume: number;
  lineUphRaw: number;
  lineUphUsedForCapacity: number;
  directLabor: LaborBreakdownRow[];
  indirectLabor: LaborBreakdownRow[];
  costLines: CostLine[];
  totalPerUnit: number;
  warnings: string[];
}
