import { calcL10StationComputed, calcL6StationComputed, normalizeSpaceProcessName } from './calculations';
import { parseCsv } from './csv';
import type {
  EquipmentItem,
  L10LaborTimeEstimation,
  L10Station,
  L6LaborTimeEstimation,
  L6Station,
  LaborRow,
  ProductSetupL10,
  ProductSetupL6,
  SpaceAllocationRow,
} from './models';

function normalizeHeader(value: string): string {
  return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function asNumber(value: string | undefined): number | null {
  if (value === undefined) return null;
  const trimmed = String(value).trim();
  if (!trimmed) return null;
  const numeric = Number(trimmed.replace(/,/g, ''));
  return Number.isFinite(numeric) ? numeric : null;
}

function makeLookup(header: string[] | undefined): Record<string, number> {
  return Object.fromEntries((header ?? []).map((cell, index) => [normalizeHeader(cell), index]));
}

function getCell(row: string[], lookup: Record<string, number>, ...aliases: string[]): string {
  for (const alias of aliases) {
    const index = lookup[normalizeHeader(alias)];
    if (index !== undefined) return row[index] ?? '';
  }
  return '';
}

function parseKeyValueRows(text: string): Record<string, string> {
  const rows = parseCsv(text);
  const result: Record<string, string> = {};
  rows.forEach((row) => {
    if (row.length < 2) return;
    const key = normalizeHeader(row[0]);
    if (!key) return;
    result[key] = row[1] ?? '';
  });
  return result;
}

export function parseMonthYearUpdateCsv(text: string) {
  const keyValues = parseKeyValueRows(text);
  return {
    directLaborHourlyRate: asNumber(keyValues.directlaborhourlyrate ?? keyValues.directlaborrate ?? keyValues.dlrate),
    indirectLaborHourlyRate: asNumber(keyValues.indirectlaborhourlyrate ?? keyValues.idlrate),
    efficiency: asNumber(keyValues.efficiency),
    equipmentAverageUsefulLifeYears: asNumber(keyValues.equipmentaverageusefullifeyears ?? keyValues.usefullifeyears),
    equipmentMaintenanceRatio: asNumber(keyValues.equipmentmaintenanceratio ?? keyValues.maintenanceratio),
    powerCostPerUnit: asNumber(keyValues.powercostperunit ?? keyValues.powerperunit),
    spaceRatePerSqft: asNumber(keyValues.spaceratepersqft ?? keyValues.spacerate),
  };
}

export function parseEquipmentListSetupCsv(text: string) {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => row.some((cell) => normalizeHeader(cell) === 'process'));
  if (headerIndex < 0) throw new Error('Equipment CSV is missing a process header.');
  const lookup = makeLookup(rows[headerIndex]);
  const equipmentList: EquipmentItem[] = rows.slice(headerIndex + 1)
    .filter((row) => row.some((cell) => String(cell).trim().length > 0))
    .map((row, index) => ({
      id: getCell(row, lookup, 'id') || `equip-${index}`,
      process: getCell(row, lookup, 'process'),
      item: getCell(row, lookup, 'item', 'equipment'),
      qty: Math.max(0, asNumber(getCell(row, lookup, 'qty', 'quantity')) ?? 0),
      unitPrice: Math.max(0, asNumber(getCell(row, lookup, 'unitprice', 'unit price', 'price')) ?? 0),
      depreciationYears: Math.max(1, asNumber(getCell(row, lookup, 'depreciationyears', 'years')) ?? 1),
      costPerMonth: asNumber(getCell(row, lookup, 'costpermonth', 'cost / month')),
      simMachineGroup: getCell(row, lookup, 'simmachinegroup', 'simulationgroup'),
      simParallelQty: asNumber(getCell(row, lookup, 'simparallelqty', 'simqty')),
      simRateOverride: asNumber(getCell(row, lookup, 'simrateoverride', 'rateoverride')),
    }));

  const meta = parseKeyValueRows(text);
  return {
    equipmentList,
    equipmentCostPerMonth: asNumber(meta.equipmentcostpermonth ?? meta.equipmentdepreciationpermonth) ?? equipmentList.reduce((sum, item) => sum + ((item.costPerMonth ?? 0) || ((item.qty * item.unitPrice) / (item.depreciationYears * 12))), 0),
    source: meta.source || 'csv',
  };
}

export function parseSpaceSetupCsv(text: string) {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => row.some((cell) => normalizeHeader(cell) === 'floor'));
  if (headerIndex < 0) throw new Error('Space CSV is missing a floor header.');
  const lookup = makeLookup(rows[headerIndex]);
  const allocations: SpaceAllocationRow[] = rows.slice(headerIndex + 1)
    .filter((row) => row.some((cell) => String(cell).trim().length > 0))
    .map((row, index) => ({
      id: getCell(row, lookup, 'id') || `space-${index}`,
      floor: getCell(row, lookup, 'floor'),
      process: normalizeSpaceProcessName(getCell(row, lookup, 'process')),
      areaSqft: Math.max(0, asNumber(getCell(row, lookup, 'areasqft', 'area')) ?? 0),
      ratePerSqft: asNumber(getCell(row, lookup, 'ratepersqft', 'rate')),
      monthlyCost: asNumber(getCell(row, lookup, 'monthlycost', 'costpermonth')),
    }));

  const meta = parseKeyValueRows(text);
  return {
    allocations,
    areaMultiplier: asNumber(meta.areamultiplier),
  };
}

export function parseDlohIdlSetupCsv(text: string) {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => row.some((cell) => normalizeHeader(cell) === 'mode' || normalizeHeader(cell) === 'name'));
  if (headerIndex < 0) throw new Error('DLOH/IDL CSV is missing a usable header.');
  const lookup = makeLookup(rows[headerIndex]);
  const idlL10: LaborRow[] = [];
  const idlL6: LaborRow[] = [];

  rows.slice(headerIndex + 1).forEach((row, index) => {
    if (!row.some((cell) => String(cell).trim())) return;
    const mode = (getCell(row, lookup, 'mode') || 'L10').toUpperCase();
    const laborRow: LaborRow = {
      id: getCell(row, lookup, 'id') || `idl-${index}`,
      name: getCell(row, lookup, 'name') || getCell(row, lookup, 'department') || `IDL ${index + 1}`,
      department: getCell(row, lookup, 'department'),
      role: getCell(row, lookup, 'role'),
      headcount: Math.max(0, asNumber(getCell(row, lookup, 'headcount', 'hc')) ?? 0),
      allocationPercent: asNumber(getCell(row, lookup, 'allocationpercent', 'allocation')),
      uphSource: getCell(row, lookup, 'uphsource') === 'override' ? 'override' : 'line',
      overrideUph: asNumber(getCell(row, lookup, 'overrideuph')),
      rrNote: getCell(row, lookup, 'rrnote', 'note'),
    };
    if (mode === 'L6') idlL6.push(laborRow);
    else idlL10.push(laborRow);
  });

  return { idlL10, idlL6 };
}

export function parseL10LaborTimeEstimationCsv(text: string): L10LaborTimeEstimation {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => row.some((cell) => normalizeHeader(cell) === 'name' || normalizeHeader(cell) === 'station'));
  if (headerIndex < 0) throw new Error('L10 labor CSV is missing a station header.');
  const lookup = makeLookup(rows[headerIndex]);
  const meta = parseKeyValueRows(text);
  const stations: L10Station[] = rows.slice(headerIndex + 1)
    .filter((row) => row.some((cell) => String(cell).trim().length > 0))
    .map((row, index) => calcL10StationComputed({
      id: getCell(row, lookup, 'id') || `l10-${index}`,
      name: getCell(row, lookup, 'name', 'station'),
      laborHc: asNumber(getCell(row, lookup, 'laborhc', 'hc')) ?? 0,
      parallelStations: asNumber(getCell(row, lookup, 'parallelstations', 'parallel')) ?? 1,
      cycleTimeSec: asNumber(getCell(row, lookup, 'cycletimesec', 'ctsec')),
      allowanceFactor: asNumber(getCell(row, lookup, 'allowancefactor', 'allowance')) ?? 1,
      touchTimeMin: asNumber(getCell(row, lookup, 'touchtimemin')),
      handlingTimeSec: asNumber(getCell(row, lookup, 'handlingtimesec')),
      oneManMultiMachineCount: asNumber(getCell(row, lookup, 'onemanmultimachinecount')),
      timeMinPerCycle: asNumber(getCell(row, lookup, 'timeminpercycle')),
      runPerDay: asNumber(getCell(row, lookup, 'runperday')),
    }, {
      hoursPerShift: asNumber(meta.hourspershift),
      shiftsPerDay: asNumber(meta.shiftsperday),
      workDaysPerWeek: asNumber(meta.workdaysperweek),
      workDaysPerMonth: asNumber(meta.workdayspermonth),
      fpy: asNumber(meta.fpy),
    }));

  return {
    meta: {
      hoursPerShift: asNumber(meta.hourspershift),
      targetTime: asNumber(meta.targettime),
      shiftsPerDay: asNumber(meta.shiftsperday),
      workDaysPerWeek: asNumber(meta.workdaysperweek),
      workDaysPerMonth: asNumber(meta.workdayspermonth),
      fpy: asNumber(meta.fpy),
    },
    stations,
    source: 'l10_labor_csv',
  };
}

export function parseL6LaborTimeEstimationCsv(text: string): L6LaborTimeEstimation {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => row.some((cell) => normalizeHeader(cell) === 'name' || normalizeHeader(cell) === 'process'));
  if (headerIndex < 0) throw new Error('L6 labor CSV is missing a process header.');
  const lookup = makeLookup(rows[headerIndex]);
  const meta = parseKeyValueRows(text);
  const stations: L6Station[] = rows.slice(headerIndex + 1)
    .filter((row) => row.some((cell) => String(cell).trim().length > 0))
    .map((row, index) => calcL6StationComputed({
      id: getCell(row, lookup, 'id') || `l6-${index}`,
      stationNo: asNumber(getCell(row, lookup, 'stationno', 'no')) ?? undefined,
      name: getCell(row, lookup, 'name', 'process'),
      laborHc: asNumber(getCell(row, lookup, 'laborhc', 'hc')) ?? 0,
      parallelStations: asNumber(getCell(row, lookup, 'parallelstations', 'fixture', 'jig')) ?? 1,
      cycleTimeSec: asNumber(getCell(row, lookup, 'cycletimesec', 'ctsec')),
      allowanceRate: asNumber(getCell(row, lookup, 'allowancerate', 'allowance')),
      stdManHours: asNumber(getCell(row, lookup, 'stdmanhours')),
      vpy: asNumber(getCell(row, lookup, 'vpy')),
      shiftRate: asNumber(getCell(row, lookup, 'shiftrate')),
      dlOnline: asNumber(getCell(row, lookup, 'dlonline')),
      isTotal: normalizeHeader(getCell(row, lookup, 'name', 'process')) === 'total',
    }));

  return {
    header: {
      category: meta.category,
      productionLine: meta.productionline,
      annualDemand: asNumber(meta.annualdemand) ?? undefined,
      monthlyDemand: asNumber(meta.monthlydemand) ?? undefined,
      multiPanelQty: asNumber(meta.multipanelqty) ?? undefined,
      lineCapabilityPerShift: asNumber(meta.linecapabilitypershift) ?? undefined,
      exRateRmbUsd: asNumber(meta.exratermbusd) ?? undefined,
      serviceHourlyWage: asNumber(meta.servicehourlywage) ?? undefined,
    },
    segments: [{ id: 'seg-import', name: meta.segmentname || 'Segment 1', stations }],
    stations,
    source: 'l6_labor_csv',
  };
}

export function parseL10MpmSetupCsv(text: string): Partial<ProductSetupL10> {
  const values = parseKeyValueRows(text);
  const handlingSec = asNumber(values.testtimehandlingsec ?? values.handlingsec) ?? 0;
  const functionSec = asNumber(values.testtimefunctionsec ?? values.functionsec) ?? 0;
  return {
    processType: 'L10',
    bu: values.bu || '',
    customer: values.customer || '',
    projectName: values.projectname || '',
    modelName: values.modelname || values.projectname || '',
    sizeMm: values.sizemm || values.size || '',
    weightKgPerTool: asNumber(values.weightkgpertool ?? values.weight),
    testTime: {
      handlingSec,
      functionSec,
      totalSec: handlingSec + functionSec,
    },
    yield: asNumber(values.yield),
    rfqQtyPerMonth: asNumber(values.rfqqtypermonth ?? values.rfqmonth),
    probeLifeCycle: asNumber(values.probelifecycle),
    rfqQtyLifeCycle: asNumber(values.rfqqtylifecycle),
    workDaysPerYear: asNumber(values.workdaysperyear),
    workDaysPerMonth: asNumber(values.workdayspermonth),
    workDaysPerWeek: asNumber(values.workdaysperweek),
    shiftsPerDay: asNumber(values.shiftsperday),
    hoursPerShift: asNumber(values.hourspershift),
    uph: asNumber(values.uph),
    ctSec: asNumber(values.ctsec),
    source: 'l10_mpm_setup_csv',
  };
}

export function parseL6MpmSetupCsv(text: string): Partial<ProductSetupL6> {
  const values = parseKeyValueRows(text);
  const handlingSec = asNumber(values.testtimehandlingsec ?? values.handlingsec) ?? 0;
  const functionSec = asNumber(values.testtimefunctionsec ?? values.functionsec) ?? 0;
  return {
    processType: 'L6',
    bu: values.bu || '',
    customer: values.customer || '',
    pn: values.pn || values.modelname || '',
    sku: values.sku || values.pn || '',
    modelName: values.modelname || values.pn || '',
    rfqQtyPerMonth: asNumber(values.rfqqtypermonth ?? values.rfqmonth),
    boardsPerPanel: asNumber(values.boardsperpanel) ?? 1,
    pcbSize: values.pcbsize || '',
    stationPath: values.stationpath || '',
    side1PartsCount: asNumber(values.side1partscount),
    side2PartsCount: asNumber(values.side2partscount),
    offLineBlastingPartCount: asNumber(values.offlineblastingpartcount),
    dipPartsType: values.dippartstype || '',
    selectiveWs: values.selectivews || '',
    assemblyPart: values.assemblypart || '',
    routingTimesSec: {},
    testTime: {
      handlingSec,
      functionSec,
    },
    source: 'l6_mpm_setup_csv',
  };
}