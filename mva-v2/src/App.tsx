import { startTransition, useEffect, useMemo, useState } from 'react';
import { KpiCard } from './components/KpiCard';
import { SectionCard } from './components/SectionCard';
import { Sidebar } from './components/Sidebar';
import { calculateMva, calculateSimulation, computeEquipmentDelta, deriveMachineRatesFromEquipmentLinks } from './domain/calculations';
import { parseCsv, toCsv } from './domain/csv';
import { defaultProject } from './domain/defaults';
import { buildL10StationsCsv, buildProjectExport, buildSummaryCsv, downloadText } from './domain/exporters';
import {
  parseDlohIdlSetupCsv,
  parseEquipmentListSetupCsv,
  parseL10LaborTimeEstimationCsv,
  parseL10MpmSetupCsv,
  parseL6LaborTimeEstimationCsv,
  parseL6MpmSetupCsv,
  parseMonthYearUpdateCsv,
  parseSpaceSetupCsv,
} from './domain/importers';
import type { EquipmentItem, LaborRow, LineStandard, ProjectState, TabId } from './domain/models';
import { loadSelectedLineStandardId, saveSelectedLineStandardId } from './domain/persistence';
import { useProjectState } from './state/useProjectState';

const MAX_IMPORT_BYTES = 5 * 1024 * 1024;

function numberValue(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function boundedNumber(value: string, { min, max }: { min?: number; max?: number } = {}): number {
  const parsed = numberValue(value);
  if (min !== undefined && parsed < min) return min;
  if (max !== undefined && parsed > max) return max;
  return parsed;
}

function toMoney(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value);
}

function formatTimestamp(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function buildFallbackL6Segments(project: ProjectState): ProjectState['laborTimeL6']['segments'] {
  if (project.laborTimeL6.segments.length > 0) {
    return project.laborTimeL6.segments.map((segment, segmentIndex) => ({
      ...segment,
      id: segment.id || `seg-${segmentIndex + 1}`,
      name: segment.name || project.productL6.sku || project.productL6.pn || `Segment ${segmentIndex + 1}`,
      stations: segment.stations.map((station, stationIndex) => ({
        ...station,
        id: station.id || `${segment.id || `seg-${segmentIndex + 1}`}-station-${stationIndex + 1}`,
      })),
    }));
  }

  return [{
    id: 'seg-1',
    name: project.productL6.sku || project.productL6.pn || 'Segment 1',
    stations: project.laborTimeL6.stations.map((station, stationIndex) => ({
      ...station,
      id: station.id || `seg-1-station-${stationIndex + 1}`,
    })),
  }];
}

function assertFileSize(file: File): void {
  if (file.size > MAX_IMPORT_BYTES) {
    throw new Error(`File exceeds ${MAX_IMPORT_BYTES / (1024 * 1024)} MB limit.`);
  }
}

function assertHeaders(header: string[] | undefined, requiredHeaders: string[]): Record<string, number> {
  if (!header || header.length === 0) {
    throw new Error('CSV header row is missing.');
  }

  const lookup = Object.fromEntries(header.map((cell, index) => [cell.trim().toLowerCase(), index]));
  const missing = requiredHeaders.filter((headerName) => !(headerName in lookup));
  if (missing.length > 0) {
    throw new Error(`CSV is missing required headers: ${missing.join(', ')}`);
  }
  return lookup;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function readJsonRecord(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text) as unknown;
  if (!isRecord(parsed)) {
    throw new Error('JSON payload must be an object.');
  }
  return parsed;
}

function syncL10TestTime(current: ProjectState['productL10']['testTime'], patch: Partial<ProjectState['productL10']['testTime']>) {
  const handlingSec = patch.handlingSec ?? current.handlingSec;
  const functionSec = patch.functionSec ?? current.functionSec;
  return {
    ...current,
    ...patch,
    handlingSec,
    functionSec,
    totalSec: handlingSec + functionSec,
  };
}

function resolveIndirectLaborRows(
  plant: ProjectState['plant'],
  mode: ProjectState['plant']['idlUiMode'] = plant.idlUiMode,
  processType: ProjectState['plant']['processType'] = plant.processType,
): LaborRow[] {
  const resolvedMode = mode === 'auto' ? processType : mode;
  return (plant.idlRowsByMode[resolvedMode] ?? []).map((row) => ({ ...row }));
}

function projectForProcess(project: ProjectState, processType: ProjectState['plant']['processType']): ProjectState {
  const plant: ProjectState['plant'] = { ...project.plant, processType };
  if (plant.idlUiMode === 'auto') {
    plant.indirectLaborRows = resolveIndirectLaborRows(plant, 'auto', processType);
  }
  return {
    ...project,
    plant,
  };
}

export function validateProjectPayload(payload: unknown): ProjectState {
  if (!isRecord(payload)) {
    throw new Error('Project JSON payload must be an object.');
  }
  if (!isRecord(payload.basicInfo) || !Array.isArray(payload.machines) || !Array.isArray(payload.processSteps) || !isRecord(payload.plant)) {
    throw new Error('Project JSON does not match the expected project structure.');
  }
  return payload as unknown as ProjectState;
}

export function validateLineStandardsPayload(payload: unknown): LineStandard[] {
  if (!Array.isArray(payload)) {
    throw new Error('Line standards JSON must be an array.');
  }
  if (!payload.every((item) => isRecord(item) && typeof item.name === 'string' && Array.isArray(item.equipmentList))) {
    throw new Error('One or more line standards are malformed.');
  }
  return payload as LineStandard[];
}

export function parseEquipmentRows(csvText: string): EquipmentItem[] {
  const [header, ...rows] = parseCsv(csvText);
  const lookup = assertHeaders(header, ['process', 'item', 'qty', 'unitprice', 'depreciationyears']);
  if (rows.length === 0) throw new Error('Equipment CSV contains no data rows.');
  return rows.map((row, idx) => ({
    id: row[lookup.id] || `import-eq-${idx}`,
    process: row[lookup.process] || '',
    item: row[lookup.item] || '',
    qty: Math.max(0, numberValue(row[lookup.qty] || '0')),
    unitPrice: Math.max(0, numberValue(row[lookup.unitprice] || '0')),
    depreciationYears: Math.max(1, numberValue(row[lookup.depreciationyears] || '1')),
    costPerMonth: lookup.costpermonth !== undefined && row[lookup.costpermonth] ? Math.max(0, numberValue(row[lookup.costpermonth])) : null,
  }));
}

export function parseSpaceRows(csvText: string): ProjectState['plant']['spaceAllocation'] {
  const [header, ...rows] = parseCsv(csvText);
  const lookup = assertHeaders(header, ['floor', 'process', 'areasqft']);
  if (rows.length === 0) throw new Error('Space CSV contains no data rows.');
  return rows.map((row, idx) => ({
    id: row[lookup.id] || `import-space-${idx}`,
    floor: row[lookup.floor] || '',
    process: row[lookup.process] || '',
    areaSqft: Math.max(0, numberValue(row[lookup.areasqft] || '0')),
    ratePerSqft: lookup.ratepersqft !== undefined && row[lookup.ratepersqft] ? Math.max(0, numberValue(row[lookup.ratepersqft])) : null,
    monthlyCost: lookup.monthlycost !== undefined && row[lookup.monthlycost] ? Math.max(0, numberValue(row[lookup.monthlycost])) : null,
  }));
}

export function parseLaborRows(csvText: string): { direct: LaborRow[]; indirect: LaborRow[] } {
  const [header, ...rows] = parseCsv(csvText);
  const lookup = assertHeaders(header, ['kind', 'name', 'headcount', 'uphsource']);
  if (rows.length === 0) throw new Error('Labor CSV contains no data rows.');
  const direct: LaborRow[] = [];
  const indirect: LaborRow[] = [];
  rows.forEach((row, idx) => {
    const kind = (row[lookup.kind] || 'direct').toLowerCase();
    const laborRow: LaborRow = {
      id: row[lookup.id] || `import-labor-${idx}`,
      name: row[lookup.name] || '',
      process: row[lookup.process] || '',
      department: row[lookup.department] || '',
      role: row[lookup.role] || '',
      headcount: Math.max(0, numberValue(row[lookup.headcount] || '0')),
      allocationPercent: lookup.allocationpercent !== undefined && row[lookup.allocationpercent]
        ? boundedNumber(row[lookup.allocationpercent], { min: 0, max: 1 })
        : null,
      uphSource: (row[lookup.uphsource] || 'line') === 'override' ? 'override' : 'line',
      overrideUph: lookup.overrideuph !== undefined && row[lookup.overrideuph] ? Math.max(0, numberValue(row[lookup.overrideuph])) : null,
    };
    if (kind === 'indirect') {
      indirect.push(laborRow);
    } else {
      direct.push(laborRow);
    }
  });
  return { direct, indirect };
}

export default function App() {
  const { project, setProject } = useProjectState();
  const [activeTab, setActiveTab] = useState<TabId>('basic');
  const [selectedLineStandardId, setSelectedLineStandardId] = useState<string>(() => loadSelectedLineStandardId() || project.lineStandards[0]?.id || '');
  const [statusMessage, setStatusMessage] = useState<string>('');

  const simulation = useMemo(() => calculateSimulation(project), [project]);
  const mva = useMemo(() => calculateMva(project, simulation), [project, simulation]);

  const updateProject = (updater: (current: ProjectState) => ProjectState) => {
    startTransition(() => {
      setProject((current) => updater(current));
    });
  };

  useEffect(() => {
    saveSelectedLineStandardId(selectedLineStandardId);
  }, [selectedLineStandardId]);

  useEffect(() => {
    if (!project.lineStandards.some((item) => item.id === selectedLineStandardId)) {
      setSelectedLineStandardId(project.lineStandards[0]?.id ?? '');
    }
  }, [project.lineStandards, selectedLineStandardId]);

  const updateLineStandardSelection = (id: string) => {
    setSelectedLineStandardId(id);
  };

  const activeOverhead = project.plant.overheadByProcess[project.plant.processType];
  const summaryCsv = useMemo(() => buildSummaryCsv(project), [project]);
  const l10Project = useMemo(() => projectForProcess(project, 'L10'), [project]);
  const l6Project = useMemo(() => projectForProcess(project, 'L6'), [project]);
  const l10Simulation = useMemo(() => calculateSimulation(l10Project), [l10Project]);
  const l6Simulation = useMemo(() => calculateSimulation(l6Project), [l6Project]);
  const l10Mva = useMemo(() => calculateMva(l10Project, l10Simulation), [l10Project, l10Simulation]);
  const l6Mva = useMemo(() => calculateMva(l6Project, l6Simulation), [l6Project, l6Simulation]);
  const l10SummaryCsv = useMemo(() => buildSummaryCsv(l10Project), [l10Project]);
  const l6SummaryCsv = useMemo(() => buildSummaryCsv(l6Project), [l6Project]);
  const selectedLineStandard = useMemo(
    () => project.lineStandards.find((item) => item.id === selectedLineStandardId) ?? null,
    [project.lineStandards, selectedLineStandardId],
  );
  const equipmentDelta = useMemo(
    () => computeEquipmentDelta({
      template: selectedLineStandard,
      equipmentList: project.plant.equipmentList,
      extraEquipmentList: project.plant.extraEquipmentList,
    }),
    [project.plant.equipmentList, project.plant.extraEquipmentList, selectedLineStandard],
  );
  const bottleneckStep = useMemo(
    () => simulation.steps.find((step) => step.isBottleneck) ?? null,
    [simulation.steps],
  );
  const simulationChartCeiling = useMemo(
    () => Math.max(...simulation.steps.map((step) => step.cycleTime), simulation.taktTime, 1),
    [simulation.steps, simulation.taktTime],
  );
  const matrixLineArea = useMemo(
    () => Math.max(0, project.plant.spaceSettings.lineLengthFt * project.plant.spaceSettings.lineWidthFt * project.plant.spaceSettings.spaceAreaMultiplier),
    [project.plant.spaceSettings.lineLengthFt, project.plant.spaceSettings.lineWidthFt, project.plant.spaceSettings.spaceAreaMultiplier],
  );
  const equipmentListTotalPerMonth = useMemo(
    () => project.plant.equipmentList.reduce((sum, item) => {
      const months = Math.max(1, item.depreciationYears * 12);
      const derived = (item.qty * item.unitPrice) / months;
      return sum + (item.costPerMonth ?? derived);
    }, 0),
    [project.plant.equipmentList],
  );
  const extraEquipmentTotalPerMonth = useMemo(
    () => project.plant.extraEquipmentList.reduce((sum, item) => {
      const months = Math.max(1, item.depreciationYears * 12);
      const derived = (item.qty * item.unitPrice) / months;
      return sum + (item.costPerMonth ?? derived);
    }, 0),
    [project.plant.extraEquipmentList],
  );
  const equipmentSimulationMapping = useMemo(
    () => deriveMachineRatesFromEquipmentLinks({
      baseMachines: project.machines,
      equipmentList: project.plant.equipmentList,
      extraEquipmentList: project.plant.extraEquipmentList,
      includeExtra: project.plant.includeExtraEquipmentInSimMapping,
    }),
    [project.machines, project.plant.equipmentList, project.plant.extraEquipmentList, project.plant.includeExtraEquipmentInSimMapping],
  );
  const l10StationSnapshots = useMemo(() => project.laborTimeL10.stations.map((station) => {
    const parallelStations = Math.max(1, station.parallelStations || 1);
    const cycleTimeSec = station.cycleTimeSec ?? 0;
    const avgCycleTimeSec = cycleTimeSec > 0 ? (cycleTimeSec * station.allowanceFactor) / parallelStations : 0;
    const uph = avgCycleTimeSec > 0 ? 3600 / avgCycleTimeSec : 0;
    const hoursPerShift = project.laborTimeL10.meta.hoursPerShift ?? 0;
    const shiftsPerDay = project.laborTimeL10.meta.shiftsPerDay ?? 0;
    const workDaysPerWeek = project.laborTimeL10.meta.workDaysPerWeek ?? 0;
    const workDaysPerMonth = project.laborTimeL10.meta.workDaysPerMonth ?? 0;
    return {
      ...station,
      avgCycleTimeSec,
      uph,
      perShiftCapa: uph * hoursPerShift,
      dailyCapa: uph * hoursPerShift * shiftsPerDay,
      weeklyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerWeek,
      monthlyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerMonth,
    };
  }), [project.laborTimeL10.meta, project.laborTimeL10.stations]);
  const l6SegmentSnapshots = useMemo(() => buildFallbackL6Segments(project).map((segment) => ({
    ...segment,
    stations: segment.stations.map((station) => {
      const parallelStations = Math.max(1, station.parallelStations || 1);
      const allowanceRate = station.allowanceRate ?? 0;
      const allowanceFactor = allowanceRate > 1 ? 1 + allowanceRate / 100 : 1 + allowanceRate;
      const cycleTimeSec = station.cycleTimeSec ?? 0;
      const avgCycleTimeSec = cycleTimeSec > 0 ? (cycleTimeSec * allowanceFactor) / parallelStations : 0;
      const uph = avgCycleTimeSec > 0 ? 3600 / avgCycleTimeSec : 0;
      return {
        ...station,
        avgCycleTimeSec,
        uph,
      };
    }),
  })), [project]);
  const l6StationSnapshots = useMemo(
    () => l6SegmentSnapshots.flatMap((segment) => segment.stations),
    [l6SegmentSnapshots],
  );
  const l6LaborSummary = useMemo(() => {
    const activeStations = l6StationSnapshots.filter((station) => !station.isTotal);
    return {
      segments: l6SegmentSnapshots.length,
      stations: activeStations.length,
      totalHc: activeStations.reduce((sum, station) => sum + (station.dlOnline ?? station.laborHc ?? 0), 0),
      bottleneckUph: activeStations.reduce((lowest, station) => {
        const next = station.uph ?? 0;
        if (lowest === 0) return next;
        if (next === 0) return lowest;
        return Math.min(lowest, next);
      }, 0),
    };
  }, [l6SegmentSnapshots, l6StationSnapshots]);

  const updateDirectLaborRow = (id: string, patch: Partial<LaborRow>) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: current.plant.directLaborRows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
      },
    }));
  };

  const updateIndirectLaborRow = (id: string, patch: Partial<LaborRow>) => {
    updateProject((current) => {
      const indirectLaborRows = current.plant.indirectLaborRows.map((row) => (row.id === id ? { ...row, ...patch } : row));
      const nextPlant: ProjectState['plant'] = {
        ...current.plant,
        indirectLaborRows,
      };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = {
        ...current.plant.idlRowsByMode,
        [mode]: indirectLaborRows,
      };
      return { ...current, plant: nextPlant };
    });
  };

  const addDirectLaborRow = () => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: [...current.plant.directLaborRows, { id: `dl-${Date.now()}`, name: '', process: '', headcount: 0, uphSource: 'line' }],
      },
    }));
  };

  const addIndirectLaborRow = () => {
    updateProject((current) => {
      const newRow: LaborRow = { id: `idl-${Date.now()}`, name: '', department: '', role: '', headcount: 0, allocationPercent: 1, uphSource: 'line' };
      const indirectLaborRows = [...current.plant.indirectLaborRows, newRow];
      const nextPlant: ProjectState['plant'] = {
        ...current.plant,
        indirectLaborRows,
      };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = {
        ...current.plant.idlRowsByMode,
        [mode]: indirectLaborRows,
      };
      return { ...current, plant: nextPlant };
    });
  };

  const updateEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList', id: string, patch: Partial<EquipmentItem>) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        [listKey]: current.plant[listKey].map((item) => (item.id === id ? { ...item, ...patch } : item)),
      },
    }));
  };

  const addEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList') => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        [listKey]: [...current.plant[listKey], { id: `${listKey}-${Date.now()}`, process: '', item: '', qty: 0, unitPrice: 0, depreciationYears: 1, costPerMonth: null }],
      },
    }));
  };

  const deleteEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList', id: string) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        [listKey]: current.plant[listKey].filter((item) => item.id !== id),
      },
    }));
  };

  const updateSpaceAllocationRow = (id: string, patch: Partial<ProjectState['plant']['spaceAllocation'][number]>) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        spaceAllocation: current.plant.spaceAllocation.map((row) => (row.id === id ? { ...row, ...patch } : row)),
      },
    }));
  };

  const addSpaceAllocationRow = () => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        spaceAllocation: [...current.plant.spaceAllocation, { id: `space-${Date.now()}`, floor: 'F1', process: '', areaSqft: 0, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: null }],
      },
    }));
  };

  const deleteSpaceAllocationRow = (id: string) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        spaceAllocation: current.plant.spaceAllocation.filter((row) => row.id !== id),
      },
    }));
  };

  const applyMatrixSpaceAllocation = () => {
    updateProject((current) => {
      const floors = Object.entries(current.plant.spaceSettings.floorAreas)
        .filter(([, area]) => area > 0)
        .map(([floor]) => floor);
      const floorOrder = floors.length > 0 ? floors : ['F1'];
      const processRows = Object.entries(current.plant.spaceSettings.processDistribution)
        .filter(([, percent]) => percent > 0)
        .map(([process, percent], index) => ({
          id: `matrix-${process}-${index}`,
          floor: floorOrder[index % floorOrder.length],
          process,
          areaSqft: matrixLineArea * (percent / 100),
          ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft,
          monthlyCost: matrixLineArea * (percent / 100) * current.plant.spaceSettings.spaceRatePerSqft,
        }));
      return {
        ...current,
        plant: {
          ...current.plant,
          spaceAllocation: processRows,
        },
      };
    });
  };

  const applySplit4060SpaceAllocation = () => {
    updateProject((current) => {
      const totalFloorSqft = current.plant.spaceSettings.split4060TotalFloorSqft;
      const smtSqft = totalFloorSqft * 0.4;
      const hiSqft = totalFloorSqft * 0.6;
      const rows = current.plant.spaceSettings.split4060HiMode === 'FA'
        ? [
            { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
            { id: 'split-fa', floor: 'F1', process: 'F/A', areaSqft: hiSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: hiSqft * current.plant.spaceSettings.spaceRatePerSqft },
          ]
        : current.plant.spaceSettings.split4060HiMode === 'FBT'
          ? [
              { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fbt', floor: 'F1', process: 'FBT', areaSqft: hiSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: hiSqft * current.plant.spaceSettings.spaceRatePerSqft },
            ]
          : [
              { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fa', floor: 'F1', process: 'F/A', areaSqft: hiSqft / 2, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: (hiSqft / 2) * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fbt', floor: 'F1', process: 'FBT', areaSqft: hiSqft / 2, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: (hiSqft / 2) * current.plant.spaceSettings.spaceRatePerSqft },
            ];
      return {
        ...current,
        plant: {
          ...current.plant,
          spaceAllocation: rows,
        },
      };
    });
  };

  const deleteDirectLaborRow = (id: string) => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: current.plant.directLaborRows.filter((row) => row.id !== id),
      },
    }));
  };

  const deleteIndirectLaborRow = (id: string) => {
    updateProject((current) => {
      const indirectLaborRows = current.plant.indirectLaborRows.filter((row) => row.id !== id);
      const nextPlant: ProjectState['plant'] = {
        ...current.plant,
        indirectLaborRows,
      };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = {
        ...current.plant.idlRowsByMode,
        [mode]: indirectLaborRows,
      };
      return { ...current, plant: nextPlant };
    });
  };

  const addProcessStep = () => {
    updateProject((current) => ({
      ...current,
      processSteps: [...current.processSteps, { step: current.processSteps.length + 1, process: '', side: 'N/A' }],
    }));
  };

  const updateProcessStep = (index: number, patch: Partial<ProjectState['processSteps'][number]>) => {
    updateProject((current) => ({
      ...current,
      processSteps: current.processSteps.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    }));
  };

  const deleteProcessStep = (index: number) => {
    updateProject((current) => ({
      ...current,
      processSteps: current.processSteps.filter((_, rowIndex) => rowIndex !== index).map((row, rowIndex) => ({ ...row, step: rowIndex + 1 })),
    }));
  };

  const updateL10Station = (id: string, patch: Partial<ProjectState['laborTimeL10']['stations'][number]>) => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: {
        ...current.laborTimeL10,
        stations: current.laborTimeL10.stations.map((station) => (station.id === id ? { ...station, ...patch } : station)),
      },
    }));
  };

  const addL10Station = () => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: {
        ...current.laborTimeL10,
        stations: [...current.laborTimeL10.stations, { id: `l10-${Date.now()}`, name: '', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceFactor: 1.15 }],
      },
    }));
  };

  const deleteL10Station = (id: string) => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: {
        ...current.laborTimeL10,
        stations: current.laborTimeL10.stations.filter((station) => station.id !== id),
      },
    }));
  };

  const syncL6Segments = (
    current: ProjectState,
    segments: ProjectState['laborTimeL6']['segments'],
    source: string = current.laborTimeL6.source,
  ): ProjectState => ({
    ...current,
    laborTimeL6: {
      ...current.laborTimeL6,
      segments,
      stations: segments.flatMap((segment) => segment.stations),
      source,
    },
  });

  const updateL6SegmentName = (segmentId: string, name: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, name } : segment)),
    ));
  };

  const addL6Segment = () => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      return syncL6Segments(current, [
        ...baseSegments,
        {
          id: `seg-${Date.now()}`,
          name: `Segment ${baseSegments.length + 1}`,
          stations: [{ id: `l6-${Date.now()}`, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }],
        },
      ]);
    });
  };

  const deleteL6Segment = (segmentId: string) => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      const nextSegments = baseSegments.filter((segment) => segment.id !== segmentId);
      if (nextSegments.length === 0) {
        return syncL6Segments(current, [{ id: 'seg-1', name: current.productL6.sku || current.productL6.pn || 'Segment 1', stations: [] }]);
      }
      return syncL6Segments(current, nextSegments);
    });
  };

  const updateL6Station = (segmentId: string, id: string, patch: Partial<ProjectState['laborTimeL6']['stations'][number]>) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId
        ? { ...segment, stations: segment.stations.map((station) => (station.id === id ? { ...station, ...patch } : station)) }
        : segment)),
    ));
  };

  const addL6Station = (segmentId: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => {
        if (segment.id !== segmentId) return segment;
        const maxStationNo = segment.stations.reduce((max, station) => Math.max(max, station.stationNo ?? 0), 0);
        return {
          ...segment,
          stations: [...segment.stations, { id: `l6-${Date.now()}`, stationNo: maxStationNo + 1, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }],
        };
      }),
    ));
  };

  const deleteL6Station = (segmentId: string, id: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, stations: segment.stations.filter((station) => station.id !== id) } : segment)),
    ));
  };

  const applyL10StationsToDirectLabor = () => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: current.laborTimeL10.stations.map((station, index) => ({
          id: `l10-dl-${index}`,
          name: station.name,
          process: station.name,
          headcount: station.laborHc,
          uphSource: 'line',
        })),
      },
    }));
  };

  const applyL6StationsToDirectLabor = () => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: buildFallbackL6Segments(current).flatMap((segment) => segment.stations).filter((station) => !station.isTotal).map((station, index) => ({
          id: `l6-dl-${index}`,
          name: station.name,
          process: station.name,
          headcount: station.laborHc,
          uphSource: 'line',
        })),
      },
    }));
  };

  const addL6RoutingTime = () => {
    updateProject((current) => {
      const nextKey = `Process ${Object.keys(current.productL6.routingTimesSec).length + 1}`;
      return {
        ...current,
        productL6: {
          ...current.productL6,
          routingTimesSec: {
            ...current.productL6.routingTimesSec,
            [nextKey]: 0,
          },
        },
      };
    });
  };

  const updateL6RoutingTime = (key: string, patch: { nextKey?: string; value?: number }) => {
    updateProject((current) => {
      const entries = Object.entries(current.productL6.routingTimesSec);
      const nextEntries = entries.map(([entryKey, entryValue]) => {
        if (entryKey !== key) return [entryKey, entryValue] as const;
        return [patch.nextKey ?? entryKey, patch.value ?? entryValue] as const;
      });
      return {
        ...current,
        productL6: {
          ...current.productL6,
          routingTimesSec: Object.fromEntries(nextEntries.filter(([entryKey]) => entryKey.trim().length > 0)),
        },
      };
    });
  };

  const deleteL6RoutingTime = (key: string) => {
    updateProject((current) => ({
      ...current,
      productL6: {
        ...current.productL6,
        routingTimesSec: Object.fromEntries(Object.entries(current.productL6.routingTimesSec).filter(([entryKey]) => entryKey !== key)),
      },
    }));
  };

  const applyEquipmentMappingToSimulation = () => {
    updateProject((current) => ({
      ...current,
      machines: deriveMachineRatesFromEquipmentLinks({
        baseMachines: current.machines,
        equipmentList: current.plant.equipmentList,
        extraEquipmentList: current.plant.extraEquipmentList,
        includeExtra: current.plant.includeExtraEquipmentInSimMapping,
      }).machines,
    }));
    setStatusMessage('Applied equipment-to-simulation mapping to Machine Rates.');
  };

  const importJsonProject = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = validateProjectPayload(readJsonRecord(await file.text()));
      updateProject(() => ({
        ...defaultProject,
        ...parsed,
        plant: { ...defaultProject.plant, ...parsed.plant },
        lineStandards: parsed.lineStandards?.length ? parsed.lineStandards : defaultProject.lineStandards,
      }));
      setStatusMessage(`Imported project: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import project JSON.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importLineStandards = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = validateLineStandardsPayload(JSON.parse(await file.text()) as unknown);
      updateProject((current) => ({ ...current, lineStandards: parsed }));
      setSelectedLineStandardId(parsed[0]?.id ?? '');
      setStatusMessage(`Imported line standards: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import line standards JSON.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const applySelectedLineStandard = () => {
    const selected = project.lineStandards.find((item) => item.id === selectedLineStandardId);
    if (!selected) {
      setStatusMessage('Selected line standard was not found.');
      return;
    }
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        equipmentList: selected.equipmentList.map((item, idx) => ({ ...item, id: `${selected.id}-${idx}` })),
      },
    }));
    setStatusMessage(`Applied line standard: ${selected.name}`);
  };

  const saveCurrentAsLineStandard = () => {
    const template: LineStandard = {
      id: `std-${Date.now()}`,
      name: `${project.basicInfo.modelName} baseline`,
      source: 'mva-v2',
      equipmentList: project.plant.equipmentList,
    };
    updateProject((current) => ({ ...current, lineStandards: [...current.lineStandards, template] }));
    setSelectedLineStandardId(template.id);
    setStatusMessage(`Saved current baseline as line standard: ${template.name}`);
  };

  const exportProject = () => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_project.json`, buildProjectExport(project), 'application/json');
  const exportSummary = () => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_summary.csv`, summaryCsv, 'text/csv');
  const exportL10Stations = () => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_l10-stations.csv`, buildL10StationsCsv(project), 'text/csv');
  const exportLineStandards = () => downloadText(`line-standards_${formatTimestamp()}.json`, JSON.stringify(project.lineStandards, null, 2), 'application/json');

  const importMonthYearUpdate = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseMonthYearUpdateCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          mvaRates: {
            ...current.plant.mvaRates,
            directLaborHourlyRate: parsed.directLaborHourlyRate ?? current.plant.mvaRates.directLaborHourlyRate,
            indirectLaborHourlyRate: parsed.indirectLaborHourlyRate ?? current.plant.mvaRates.indirectLaborHourlyRate,
            efficiency: parsed.efficiency ?? current.plant.mvaRates.efficiency,
          },
          overheadByProcess: {
            ...current.plant.overheadByProcess,
            [current.plant.processType]: {
              ...current.plant.overheadByProcess[current.plant.processType],
              equipmentAverageUsefulLifeYears: parsed.equipmentAverageUsefulLifeYears ?? current.plant.overheadByProcess[current.plant.processType].equipmentAverageUsefulLifeYears,
              equipmentMaintenanceRatio: parsed.equipmentMaintenanceRatio ?? current.plant.overheadByProcess[current.plant.processType].equipmentMaintenanceRatio,
              powerCostPerUnit: parsed.powerCostPerUnit ?? current.plant.overheadByProcess[current.plant.processType].powerCostPerUnit,
            },
          },
          spaceSettings: {
            ...current.plant.spaceSettings,
            spaceRatePerSqft: parsed.spaceRatePerSqft ?? current.plant.spaceSettings.spaceRatePerSqft,
          },
        },
      }));
      setStatusMessage(`Imported month/year update CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import month/year update CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importEquipmentSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseEquipmentListSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          equipmentList: parsed.equipmentList,
          overheadByProcess: {
            ...current.plant.overheadByProcess,
            [current.plant.processType]: {
              ...current.plant.overheadByProcess[current.plant.processType],
              equipmentDepreciationPerMonth: parsed.equipmentCostPerMonth ?? current.plant.overheadByProcess[current.plant.processType].equipmentDepreciationPerMonth,
            },
          },
        },
      }));
      setStatusMessage(`Imported equipment setup CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import equipment setup CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importSpaceSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseSpaceSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          spaceAllocation: parsed.allocations,
          spaceSettings: {
            ...current.plant.spaceSettings,
            spaceAreaMultiplier: parsed.areaMultiplier ?? current.plant.spaceSettings.spaceAreaMultiplier,
          },
        },
      }));
      setStatusMessage(`Imported space setup CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import space setup CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importDlohIdlSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseDlohIdlSetupCsv(await file.text());
      updateProject((current) => {
        const nextPlant: ProjectState['plant'] = {
          ...current.plant,
          idlRowsByMode: {
            ...current.plant.idlRowsByMode,
            L10: parsed.idlL10,
            L6: parsed.idlL6,
          },
        };
        nextPlant.indirectLaborRows = resolveIndirectLaborRows(nextPlant);
        return { ...current, plant: nextPlant };
      });
      setStatusMessage(`Imported DLOH/IDL setup CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import DLOH/IDL setup CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importL10MpmSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL10MpmSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        productL10: {
          ...current.productL10,
          ...parsed,
          testTime: syncL10TestTime(current.productL10.testTime, parsed.testTime ?? {}),
        },
      }));
      setStatusMessage(`Imported L10 MPM setup CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import L10 MPM setup CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importL6MpmSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL6MpmSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        productL6: {
          ...current.productL6,
          ...parsed,
          routingTimesSec: parsed.routingTimesSec ?? current.productL6.routingTimesSec,
          testTime: {
            ...current.productL6.testTime,
            ...parsed.testTime,
          },
        },
      }));
      setStatusMessage(`Imported L6 MPM setup CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import L6 MPM setup CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importL10LaborEstimation = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL10LaborTimeEstimationCsv(await file.text());
      updateProject((current) => ({ ...current, laborTimeL10: parsed }));
      setStatusMessage(`Imported L10 labor estimation CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import L10 labor estimation CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const importL6LaborEstimation = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL6LaborTimeEstimationCsv(await file.text());
      updateProject((current) => ({ ...current, laborTimeL6: parsed }));
      setStatusMessage(`Imported L6 labor estimation CSV: ${file.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to import L6 labor estimation CSV.';
      setStatusMessage(message);
      console.error(error);
    }
  };

  const renderReviewGate = () => (
    <SectionCard title="Review Gate" description="Exports are only meaningful when reviewer metadata is populated.">
      <div className="form-grid cols-4">
        <label><span>Decision</span><select value={project.confirmation.decision ?? ''} onChange={(event) => {
          updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, decision: event.target.value === 'OK' || event.target.value === 'NG' ? event.target.value : null, decidedAt: new Date().toISOString() } }));
          setStatusMessage(`Review gate updated: ${event.target.value || 'Unreviewed'}`);
        }}><option value="">Unreviewed</option><option value="OK">OK</option><option value="NG">NG</option></select></label>
        <label><span>Reviewer</span><input value={project.confirmation.reviewer} onChange={(event) => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, reviewer: event.target.value } }))} /></label>
        <label className="full-span"><span>Comment</span><input value={project.confirmation.comment} onChange={(event) => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, comment: event.target.value } }))} /></label>
      </div>
      <p className="muted">Recorded at: {project.confirmation.decidedAt ?? 'Not recorded yet'}</p>
    </SectionCard>
  );

  const renderImportsExports = (summaryText: string, processLabel: 'L10' | 'L6') => (
    <SectionCard title="Imports and Exports" description={`Structured exchange for ${processLabel} summary verification and release handoff.`}>
      <div className="action-grid">
        <label className="upload-card"><span>Import Project JSON</span><input type="file" accept="application/json" onChange={(event) => void importJsonProject(event.target.files?.[0] ?? null)} /></label>
        <label className="upload-card"><span>Import Month/Year Update CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importMonthYearUpdate(event.target.files?.[0] ?? null)} /></label>
        <label className="upload-card"><span>Import Equipment Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importEquipmentSetup(event.target.files?.[0] ?? null)} /></label>
        <label className="upload-card"><span>Import Space Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importSpaceSetup(event.target.files?.[0] ?? null)} /></label>
        <label className="upload-card"><span>Import DLOH/IDL Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importDlohIdlSetup(event.target.files?.[0] ?? null)} /></label>
        <label className="upload-card"><span>Import Line Standards JSON</span><input type="file" accept="application/json" onChange={(event) => void importLineStandards(event.target.files?.[0] ?? null)} /></label>
        <button type="button" className="button secondary tall" onClick={() => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_${processLabel.toLowerCase()}_summary.csv`, summaryText, 'text/csv')}>Export {processLabel} Summary CSV</button>
        <button type="button" className="button secondary tall" onClick={exportProject}>Export Project JSON</button>
        <button type="button" className="button secondary tall" onClick={exportL10Stations}>Export L10 Stations CSV</button>
        <button type="button" className="button secondary tall" onClick={exportLineStandards}>Export Line Standards JSON</button>
      </div>
    </SectionCard>
  );

  const renderSummaryPage = ({
    processLabel,
    summarySimulation,
    summaryMva,
    summaryText,
  }: {
    processLabel: 'L10' | 'L6';
    summarySimulation: ReturnType<typeof calculateSimulation>;
    summaryMva: ReturnType<typeof calculateMva>;
    summaryText: string;
  }) => {
    const overheadPerUnit =
      summaryMva.overhead.equipmentDepPerUnit +
      summaryMva.overhead.equipmentMaintPerUnit +
      summaryMva.overhead.spacePerUnit +
      summaryMva.overhead.powerPerUnit;

    return (
    <div className="stack-xl">
      <div className="summary-page-header">
        <div>
          <h2>Summary — {processLabel}</h2>
          <p className="muted">{project.basicInfo.modelName}</p>
        </div>
        <button
          type="button"
          className="button primary"
          onClick={() => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_${processLabel.toLowerCase()}_summary.csv`, summaryText, 'text/csv')}
        >
          Export MVA
        </button>
      </div>

      <SectionCard title={`Confirm (${processLabel})`} description="Decision, reviewer, and comment recorded before release.">
        <div className="form-grid cols-4">
          <div className="decision-group">
            <button type="button" className={project.confirmation.decision === 'OK' ? 'button decision-ok active' : 'button decision-ok'} onClick={() => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, decision: 'OK', decidedAt: new Date().toISOString() } }))}>OK</button>
            <button type="button" className={project.confirmation.decision === 'NG' ? 'button decision-ng active' : 'button decision-ng'} onClick={() => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, decision: 'NG', decidedAt: new Date().toISOString() } }))}>NG</button>
          </div>
          <label><span>Current Status</span><input readOnly value={project.confirmation.decision ?? 'Unreviewed'} /></label>
          <label><span>Reviewer</span><input value={project.confirmation.reviewer} onChange={(event) => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, reviewer: event.target.value } }))} /></label>
          <label><span>Comment</span><input value={project.confirmation.comment} onChange={(event) => updateProject((current) => ({ ...current, confirmation: { ...current.confirmation, comment: event.target.value } }))} /></label>
        </div>
        <p className="muted">Recorded at: {project.confirmation.decidedAt ?? 'Not recorded yet'}</p>
      </SectionCard>

      <SectionCard title="Capacity Assumptions" description="Legacy summary assumptions for demand, schedule, and UPH used in costing.">
        <div className="form-grid cols-4 capacity-grid">
          <div className="summary-metric"><span>Monthly FCST</span><strong>{summaryMva.monthlyVolume.toFixed(2)}</strong></div>
          <div className="summary-metric"><span>Days / Month</span><strong>{project.plant.mvaVolume.workDaysPerMonth}</strong></div>
          <div className="summary-metric"><span>Shifts / Day</span><strong>{project.basicInfo.shiftsPerDay}</strong></div>
          <div className="summary-metric"><span>Hours / Shift</span><strong>{project.basicInfo.hoursPerShift}</strong></div>
          <div className="summary-metric"><span>Line UPH Raw</span><strong>{summaryMva.lineUphRaw.toFixed(2)}</strong></div>
          <div className="summary-metric"><span>Line UPH Used</span><strong>{summaryMva.lineUphUsedForCapacity.toFixed(2)}</strong></div>
          {processLabel === 'L6' ? <div className="summary-metric summary-highlight"><span>Boards / Panel</span><strong>{project.productL6.boardsPerPanel}</strong></div> : null}
          {processLabel === 'L6' ? <div className="summary-metric summary-highlight"><span>PCB Size</span><strong>{project.productL6.pcbSize || '-'}</strong></div> : null}
          {processLabel === 'L6' ? <div className="summary-metric"><span>Parts (S1 / S2)</span><strong>{`${project.productL6.side1PartsCount ?? 0} / ${project.productL6.side2PartsCount ?? 0}`}</strong></div> : null}
        </div>
      </SectionCard>

      <SectionCard title="A. LABOR Cost" description={`DL Rate: ${toMoney(project.plant.mvaRates.directLaborHourlyRate)}/hr  ·  IDL Rate: ${toMoney(project.plant.mvaRates.indirectLaborHourlyRate)}/hr  ·  Efficiency: ${(project.plant.mvaRates.efficiency * 100).toFixed(1)}%`}>
        <div className="data-table-wrapper">
          <table className="data-table" data-testid={`labor-table-${processLabel.toLowerCase()}`}>
            <thead>
              <tr><th colSpan={4} className="section-header">Direct Labor</th></tr>
              <tr><th>Name</th><th>HC</th><th>Line UPH</th><th>Cost / Unit</th></tr>
            </thead>
            <tbody>
              {summaryMva.directLabor.map((row) => (
                <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{row.uphUsed}</td><td>{toMoney(row.costPerUnit)}</td></tr>
              ))}
              <tr className="summary-row">
                <td>Total DL</td>
                <td>{summaryMva.directLabor.reduce((sum, row) => sum + row.effectiveHeadcount, 0).toFixed(2)}</td>
                <td>-</td>
                <td>{toMoney(summaryMva.directLabor.reduce((sum, row) => sum + row.costPerUnit, 0))}</td>
              </tr>
              <tr><th colSpan={4} className="section-header">Indirect Labor</th></tr>
              <tr><th>Name</th><th>HC</th><th>Line UPH</th><th>Cost / Unit</th></tr>
              {summaryMva.indirectLabor.map((row) => (
                <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{row.uphUsed}</td><td>{toMoney(row.costPerUnit)}</td></tr>
              ))}
              <tr className="summary-row">
                <td>Total IDL</td>
                <td>{summaryMva.indirectLabor.reduce((sum, row) => sum + row.effectiveHeadcount, 0).toFixed(2)}</td>
                <td>-</td>
                <td>{toMoney(summaryMva.indirectLabor.reduce((sum, row) => sum + row.costPerUnit, 0))}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <div className="two-column-grid">
        <SectionCard title="D. Overhead" description="Equipment, space, and power costs per unit.">
          <ul className="plain-list">
            <li>Equip Dep / Unit: {toMoney(summaryMva.overhead.equipmentDepPerUnit)}</li>
            <li>Equip Maint / Unit: {toMoney(summaryMva.overhead.equipmentMaintPerUnit)}</li>
            <li>Space / Utility / Unit: {toMoney(summaryMva.overhead.spacePerUnit)}</li>
            <li>Power / Unit: {toMoney(summaryMva.overhead.powerPerUnit)}</li>
            <li><strong>Total Overhead / Unit: {toMoney(overheadPerUnit)}</strong></li>
          </ul>
        </SectionCard>
        <div className="stack-md">
          <SectionCard title="E. SG&A" description="Corporate, site, and IT security burden per unit.">
            <p>Total SG&amp;A / Unit: <strong>{toMoney(summaryMva.sga.totalSgaPerUnit)}</strong></p>
          </SectionCard>
          <SectionCard title="MVA Total Cost / Unit" description="">
            <div className="cost-total-display" data-testid={`total-cost-${processLabel.toLowerCase()}`}>
              {toMoney(summaryMva.totalPerUnit)}
            </div>
          </SectionCard>
        </div>
      </div>

      <SectionCard title="Warnings" description="Release blockers are surfaced here before export.">
        <ul className="plain-list" data-testid={`warning-list-${processLabel.toLowerCase()}`}>
          {summaryMva.warnings.length === 0 ? <li>No blocking warnings.</li> : summaryMva.warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      </SectionCard>

      {renderImportsExports(summaryText, processLabel)}

      <SectionCard title="Summary Preview" description="CSV preview for regression and manual audit.">
        <textarea data-testid={`summary-preview-${processLabel.toLowerCase()}`} readOnly value={summaryText} rows={14} />
      </SectionCard>
    </div>
    );
  };

  const sideBadgeClass = (side: string) => {
    if (side === 'Top') return 'side-badge top';
    if (side === 'Bottom') return 'side-badge bottom';
    return 'side-badge neutral';
  };

  return (
    <div className="app-shell">
      <Sidebar activeTab={activeTab} onSelect={setActiveTab} />
      <main className="content-shell">
        <header className="hero-panel">
          <div className="hero-copy">
            <div className="hero-meta-row">
              <p className="eyebrow">Release Candidate Workspace</p>
              <span className="hero-chip">{activeTab.replace(/_/g, ' ')}</span>
            </div>
            <h1>{project.basicInfo.modelName}</h1>
            <p className="muted">Legacy-aligned MVA workflow with typed state, import pipelines, and verification-friendly summaries.</p>
          </div>
          <div className="hero-actions">
            <button type="button" className="button secondary" onClick={exportProject}>Export Project JSON</button>
            <button type="button" className="button primary" onClick={exportSummary}>Export Summary CSV</button>
          </div>
        </header>

        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

        {activeTab === 'basic' ? (
          <div className="stack-xl">
            <SectionCard title="Basic Information" description="Legacy planning inputs: model, demand, shifts, lot size, utilization, and OEE assumptions.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header">
                    <h3>Production Planning</h3>
                    <p className="muted">Model, planning days, and weekly demand.</p>
                  </div>
                  <div className="form-grid cols-2">
                    <label className="full-span"><span>Model Name</span><input aria-label="Model Name" value={project.basicInfo.modelName} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, modelName: event.target.value } }))} /></label>
                    <label><span>Work Days (Planning)</span><input type="number" min="0" value={project.basicInfo.workDays} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, workDays: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                    <label><span>Weekly Demand</span><input aria-label="Weekly Demand" type="number" min="0" value={project.basicInfo.demandWeekly} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, demandWeekly: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header">
                    <h3>Shift Configuration</h3>
                    <p className="muted">Shift count, working hours, and break assumptions.</p>
                  </div>
                  <div className="form-grid cols-2">
                    <label><span>Shifts per Day</span><input aria-label="Shifts Per Day" type="number" value={project.basicInfo.shiftsPerDay} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, shiftsPerDay: numberValue(event.target.value) } }))} /></label>
                    <label><span>Hours per Shift</span><input aria-label="Hours Per Shift" type="number" value={project.basicInfo.hoursPerShift} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hoursPerShift: numberValue(event.target.value) } }))} /></label>
                    <label className="full-span"><span>Breaks per Shift</span><input type="number" value={project.basicInfo.breaksPerShiftMin} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, breaksPerShiftMin: numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header">
                    <h3>Efficiency and Quality</h3>
                    <p className="muted">Utilization, first pass yield, and side-specific OEE assumptions.</p>
                  </div>
                  <div className="form-grid cols-2">
                    <label><span>Target Utilization</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.targetUtilization} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, targetUtilization: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                    <label><span>First Pass Yield (FPY)</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.fpy} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, fpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                    <label><span>OEE - Placement</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeePlacement} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeePlacement: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                    <label><span>OEE - Others</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeeOthers} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeeOthers: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header">
                    <h3>Batch Settings</h3>
                    <p className="muted">Lot size, setup time, panelization, and build-side toggles.</p>
                  </div>
                  <div className="form-grid cols-2">
                    <label><span>Lot Size</span><input type="number" min="0" value={project.basicInfo.lotSize} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, lotSize: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                    <label><span>Setup Time / Lot</span><input type="number" min="0" value={project.basicInfo.setupTimeMin} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, setupTimeMin: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                    <label><span>Boards per Panel</span><input type="number" min="0" value={project.basicInfo.boardsPerPanel} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, boardsPerPanel: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                    <label><span>Value Pass Yield (VPY)</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.vpy} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, vpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                    <label><span>Top Side</span><select value={project.basicInfo.hasTopSide ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hasTopSide: event.target.value === 'yes' } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
                    <label><span>Bottom Side</span><select value={project.basicInfo.hasBottomSide ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hasBottomSide: event.target.value === 'yes' } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
                  </div>
                </section>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'machine_rates' ? (
          <div className="stack-xl">
            <SectionCard title="Machine Rates" description="Rate edits feed the simulation and downstream MVA calculations immediately.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr><th>Group</th><th>Description</th><th>Rate / Hr</th></tr>
                  </thead>
                  <tbody>
                    {project.machines.map((machine) => (
                      <tr key={machine.id}>
                        <td>{machine.group}</td>
                        <td>{machine.description}</td>
                        <td><input aria-label={`Rate ${machine.group}`} type="number" value={machine.rate} onChange={(event) => updateProject((current) => ({ ...current, machines: current.machines.map((item) => item.id === machine.id ? { ...item, rate: numberValue(event.target.value) } : item) }))} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'bom_map' ? (
          <div className="stack-xl">
            <SectionCard title="BOM Mapping" description="Package counts by side and assigned machine group.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr><th>Side</th><th>Bucket</th><th>Machine Group</th><th>Count</th></tr>
                  </thead>
                  <tbody>
                    {project.bomMap.map((row) => (
                      <tr key={row.id}>
                        <td>{row.side}</td>
                        <td>{row.bucket}</td>
                        <td>{row.machineGroup}</td>
                        <td><input type="number" value={row.count} onChange={(event) => updateProject((current) => ({ ...current, bomMap: current.bomMap.map((item) => item.id === row.id ? { ...item, count: numberValue(event.target.value) } : item) }))} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'model_process' ? (
          <div className="stack-xl">
            <SectionCard title="Model Process" description="Editable process routing matching the legacy model-process worksheet." actions={<button type="button" className="button ghost" onClick={addProcessStep}>Add Step</button>}>
              <div className="data-table-wrapper sticky-header tall-table">
                <table className="data-table">
                  <thead>
                    <tr><th>Seq</th><th>Process / Machine Group</th><th>Side</th><th>Action</th></tr>
                  </thead>
                  <tbody>
                    {project.processSteps.map((row, index) => (
                      <tr key={`${row.step}-${index}`}>
                        <td><input type="number" value={row.step} onChange={(event) => updateProcessStep(index, { step: Math.max(1, numberValue(event.target.value)) })} placeholder="Seq" /></td>
                        <td><input value={row.process} onChange={(event) => updateProcessStep(index, { process: event.target.value })} placeholder="Process / Machine Group" /></td>
                        <td>
                          <div className="process-side-cell">
                            <select value={row.side} onChange={(event) => updateProcessStep(index, { side: event.target.value })}>
                              <option value="Top">Top</option>
                              <option value="Bottom">Bottom</option>
                              <option value="N/A">N/A</option>
                            </select>
                            <span className={sideBadgeClass(row.side)}>{row.side}</span>
                          </div>
                        </td>
                        <td><button type="button" className="button ghost" onClick={() => deleteProcessStep(index)}>Delete</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'simulation_results' ? (
          <div className="stack-xl">
            <section className="kpi-grid" data-testid="kpi-grid">
              <KpiCard label="System UPH" value={simulation.uph.toFixed(2)} />
              <KpiCard label="Takt Time" value={`${simulation.taktTime.toFixed(2)} s`} />
              <KpiCard label="Weekly Capacity" value={`${simulation.weeklyOutput.toFixed(2)} / ${project.basicInfo.demandWeekly}`} tone={simulation.weeklyOutput >= project.basicInfo.demandWeekly ? 'good' : 'warn'} />
              <KpiCard label="Line Balance" value={`${simulation.lineBalanceEfficiency.toFixed(2)}%`} />
            </section>

            {bottleneckStep ? (
              <section className="alert-card warn">
                <div>
                  <p className="eyebrow">Bottleneck Detected</p>
                  <strong>{bottleneckStep.process} ({bottleneckStep.side})</strong>
                </div>
                <p>Cycle time {bottleneckStep.cycleTime.toFixed(2)} s, {((bottleneckStep.cycleTime / Math.max(simulation.taktTime, 0.01)) * 100).toFixed(1)}% of takt.</p>
              </section>
            ) : null}

            <SectionCard title="Cycle Time Analysis" description="CT and bottleneck mountain view with takt reference aligned to the legacy simulation page.">
              <div className="simulation-chart-card">
                <div className="chart-legend">
                  <span><i className="legend-dot bar-normal" />Cycle time</span>
                  <span><i className="legend-dot bar-bottleneck" />Bottleneck</span>
                  <span><i className="legend-line" />Takt time</span>
                </div>
                <div className="mountain-chart" aria-label="CT and bottleneck mountain chart">
                  <div className="takt-reference" style={{ bottom: `${(simulation.taktTime / simulationChartCeiling) * 100}%` }}>
                    <span>Takt {simulation.taktTime.toFixed(2)} s</span>
                  </div>
                  {simulation.steps.map((step) => (
                    <div className="mountain-bar-group" key={`${step.step}-${step.process}`} title={`Step ${step.step} ${step.process} ${step.side} CT ${step.cycleTime.toFixed(2)} s Util ${step.utilization.toFixed(2)}% Components ${step.componentCount}`}>
                      <div className={`mountain-bar ${step.isBottleneck ? 'is-bottleneck' : ''}`} style={{ height: `${(step.cycleTime / simulationChartCeiling) * 100}%` }} />
                      <span className="mountain-bar-value">{step.cycleTime.toFixed(1)}</span>
                      <span className="mountain-bar-label">{step.step}</span>
                    </div>
                  ))}
                </div>
                <div className="mountain-axis-labels">
                  {simulation.steps.map((step) => (
                    <div key={`${step.step}-${step.process}-label`} className="mountain-axis-item">
                      <span>{step.process}</span>
                      <span className={sideBadgeClass(step.side)}>{step.side}</span>
                    </div>
                  ))}
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Simulation Results" description="Raw CT, losses, final CT, and utilization by process step.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr><th>Step</th><th>Process</th><th>Side</th><th>Raw CT (s)</th><th>OEE Loss + Setup</th><th>Final CT (s)</th><th>Utilization</th></tr>
                  </thead>
                  <tbody>
                    {simulation.steps.map((step) => (
                      <tr key={`${step.step}-${step.process}`} className={step.isBottleneck ? 'highlight-row' : ''}>
                        <td>{step.step}</td>
                        <td>{step.process}</td>
                        <td><span className={sideBadgeClass(step.side)}>{step.side}</span></td>
                        <td>{step.rawCycleTime.toFixed(2)}</td>
                        <td>{(step.cycleTime - step.rawCycleTime).toFixed(2)}</td>
                        <td>{step.cycleTime.toFixed(2)}</td>
                        <td>
                          <div className="utilization-cell">
                            <span>{step.utilization.toFixed(2)}%</span>
                            <div className="progress-track"><div className={`progress-fill ${step.isBottleneck ? 'warn' : ''}`} style={{ width: `${Math.min(step.utilization, 100)}%` }} /></div>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_plant_rates' ? (
          <div className="stack-xl">
            <SectionCard title="Labor Rate & Efficiency" description="Legacy labor rates, volume, yield strategy, and imported plant-rate assumptions.">
              <div className="action-grid compact-actions">
                <label className="upload-card"><span>Import Month/Year Update CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importMonthYearUpdate(event.target.files?.[0] ?? null)} /></label>
              </div>
              <div className="legacy-page-grid mt-md">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Labor Rates and Control</h3><p className="muted">Direct, indirect, efficiency, process type, and IDL mode.</p></div>
                  <div className="form-grid cols-2">
                    <label><span>Process Type</span><select value={project.plant.processType} onChange={(event) => updateProject((current) => {
                      const processType = event.target.value as ProjectState['plant']['processType'];
                      const plant: ProjectState['plant'] = { ...current.plant, processType };
                      if (plant.idlUiMode === 'auto') {
                        plant.indirectLaborRows = resolveIndirectLaborRows(plant, 'auto', processType);
                      }
                      return { ...current, plant };
                    })}><option value="L6">L6</option><option value="L10">L10</option></select></label>
                    <label><span>IDL Mode</span><select value={project.plant.idlUiMode} onChange={(event) => updateProject((current) => {
                      const idlUiMode = event.target.value as ProjectState['plant']['idlUiMode'];
                      const plant: ProjectState['plant'] = { ...current.plant, idlUiMode };
                      plant.indirectLaborRows = resolveIndirectLaborRows(plant, idlUiMode);
                      return { ...current, plant };
                    })}><option value="auto">auto</option><option value="L10">L10</option><option value="L6">L6</option></select></label>
                    <label><span>Direct Labor - Hourly Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.directLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, directLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Indirect Labor - Hourly Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.indirectLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, indirectLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
                    <label className="full-span"><span>Efficiency (0-1)</span><input type="number" step="0.01" value={project.plant.mvaRates.efficiency} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, efficiency: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Volume</h3><p className="muted">Monthly and weekly demand assumptions for MVA.</p></div>
                  <div className="form-grid cols-2">
                    <label><span>Demand Mode</span><select value={project.plant.mvaVolume.demandMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, demandMode: event.target.value as 'monthly' | 'weekly' } } }))}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></label>
                    <label><span>Work Days / Month</span><input type="number" value={project.plant.mvaVolume.workDaysPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerMonth: numberValue(event.target.value) } } }))} /></label>
                    <label><span>RFQ Qty / Month</span><input type="number" value={project.plant.mvaVolume.rfqQtyPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, rfqQtyPerMonth: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Weekly Demand</span><input type="number" value={project.plant.mvaVolume.weeklyDemand} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, weeklyDemand: numberValue(event.target.value) } } }))} /></label>
                    <label className="full-span"><span>Work Days / Week</span><input type="number" value={project.plant.mvaVolume.workDaysPerWeek} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerWeek: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Yield / FPY / VPY Strategy</h3><p className="muted">Capacity strategy and whether to reuse L10 imported FPY.</p></div>
                  <div className="form-grid cols-2">
                    <label><span>Strategy</span><select value={project.plant.yield.strategy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, strategy: event.target.value as 'ignore' | 'apply_to_capacity' } } }))}><option value="ignore">Ignore</option><option value="apply_to_capacity">Apply to capacity</option></select></label>
                    <label><span>L10 FPY (loaded)</span><input readOnly value={project.laborTimeL10.meta.fpy ?? ''} /></label>
                    <label><span>FPY (0-1)</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.fpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, fpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                    <label><span>VPY (0-1)</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.vpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, vpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                    <label className="full-span checkbox-field"><input type="checkbox" checked={Boolean(project.plant.yield.useL10Fpy)} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, useL10Fpy: event.target.checked } } }))} /><span>Use FPY from L10 labor time estimation CSV</span></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Plant Summary</h3><p className="muted">Current line and volume assumptions being consumed by MVA.</p></div>
                  <ul className="plain-list compact-list">
                    <li>Plant operation days / year: {project.plant.plantOperationDaysPerYear}</li>
                    <li>Monthly volume used: {mva.monthlyVolume.toFixed(2)}</li>
                    <li>Line UPH raw: {mva.lineUphRaw.toFixed(2)}</li>
                    <li>Line UPH used: {mva.lineUphUsedForCapacity.toFixed(2)}</li>
                  </ul>
                </section>
              </div>
            </SectionCard>

            <SectionCard title="Materials, SGA, Profit, and ICC" description="The same per-unit supporting cost drivers used by the legacy MVA workbook.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Materials</h3><p className="muted">BOM, attrition, and consumables assumptions.</p></div>
                  <div className="form-grid cols-2">
                <label><span>BOM Cost / Unit</span><input type="number" step="0.01" value={project.plant.materials.bomCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, bomCostPerUnit: numberValue(event.target.value) } } }))} /></label>
                <label><span>Attrition Rate</span><input type="number" step="0.0001" value={project.plant.materials.attritionRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, attritionRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                <label><span>Consumables / Unit</span><input type="number" step="0.01" value={project.plant.materials.consumablesCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, consumablesCostPerUnit: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>SG&amp;A</h3><p className="muted">Corporate burden, site maintenance, and IT security.</p></div>
                  <div className="form-grid cols-2">
                <label><span>Corporate Burden / Month</span><input type="number" step="0.01" value={project.plant.sga.corporateBurdenPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, corporateBurdenPerMonth: numberValue(event.target.value) } } }))} /></label>
                <label><span>Site Maintenance / Month</span><input type="number" step="0.01" value={project.plant.sga.siteMaintenancePerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, siteMaintenancePerMonth: numberValue(event.target.value) } } }))} /></label>
                <label><span>IT Security / Month</span><input type="number" step="0.01" value={project.plant.sga.itSecurityPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, itSecurityPerMonth: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Profit</h3><p className="muted">BC BOM cost and profit rate inputs.</p></div>
                  <div className="form-grid cols-2">
                <label><span>BC BOM Cost / Unit</span><input type="number" step="0.01" value={project.plant.profit.bcBomCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, profit: { ...current.plant.profit, bcBomCostPerUnit: numberValue(event.target.value) } } }))} /></label>
                <label><span>Profit Rate</span><input type="number" step="0.0001" value={project.plant.profit.profitRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, profit: { ...current.plant.profit, profitRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>ICC</h3><p className="muted">Inventory value and ICC rate assumptions.</p></div>
                  <div className="form-grid cols-2">
                <label><span>Inventory Value / Unit</span><input type="number" step="0.01" value={project.plant.icc.inventoryValuePerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, icc: { ...current.plant.icc, inventoryValuePerUnit: numberValue(event.target.value) } } }))} /></label>
                <label><span>ICC Rate</span><input type="number" step="0.0001" value={project.plant.icc.iccRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, icc: { ...current.plant.icc, iccRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
              </div>
                </section>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_plant_env' ? (
          <div className="stack-xl">
            <SectionCard title="Environment & Equipment Rate" description="Overhead, building area, equipment, and space assumptions by process type.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Overhead (MVP + P1)</h3><p className="muted">Equipment depreciation, maintenance, space, and power assumptions.</p></div>
                  <div className="form-grid cols-2">
                <label><span>Equipment Useful Life Years</span><input type="number" step="0.1" value={activeOverhead.equipmentAverageUsefulLifeYears} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentAverageUsefulLifeYears: numberValue(event.target.value) } } } }))} /></label>
                <label><span>Equipment Depreciation / Month</span><input type="number" step="0.01" value={activeOverhead.equipmentDepreciationPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentDepreciationPerMonth: numberValue(event.target.value) } } } }))} /></label>
                <label><span>Equipment Maintenance Ratio</span><input type="number" step="0.0001" value={activeOverhead.equipmentMaintenanceRatio} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentMaintenanceRatio: boundedNumber(event.target.value, { min: 0 }) } } } }))} /></label>
                <label><span>Space / Rental / Cleaning (Monthly)</span><input type="number" step="0.01" value={activeOverhead.spaceMonthlyCost} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], spaceMonthlyCost: numberValue(event.target.value) } } } }))} /></label>
                <label><span>Power Cost / Unit</span><input type="number" step="0.01" value={activeOverhead.powerCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], powerCostPerUnit: numberValue(event.target.value) } } } }))} /></label>
                  </div>
                </section>

                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Space Override</h3><p className="muted">Building area, rate, and allocation override controls.</p></div>
                  <div className="form-grid cols-2">
                <label><span>Use Space Allocation Total</span><select value={project.plant.spaceSettings.useSpaceAllocationTotal ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, useSpaceAllocationTotal: event.target.value === 'yes' } } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
                <label><span>Space Rate / Sqft</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceRatePerSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceRatePerSqft: numberValue(event.target.value) } } }))} /></label>
                <label><span>Area Multiplier</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceAreaMultiplier} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: numberValue(event.target.value) } } }))} /></label>
                <label><span>Building Override Sqft</span><input type="number" value={project.plant.spaceSettings.buildingAreaOverrideSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, buildingAreaOverrideSqft: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>
              </div>
            </SectionCard>

            <SectionCard title="Environment Results" description="Resolved overhead and space figures used in the current MVA calculation.">
              <div className="three-column-grid">
                <div className="panel-list">
                  <h3>Equipment</h3>
                  <ul className="plain-list">
                    <li>Total Equipment Cost / Month: {toMoney(mva.overhead.totalEquipmentCostPerMonth)}</li>
                    <li>Equipment Dep / Month Used: {toMoney(mva.overhead.equipmentDepPerMonthUsed)}</li>
                    <li>Equipment Dep / Unit: {toMoney(mva.overhead.equipmentDepPerUnit)}</li>
                    <li>Equipment Maint / Unit: {toMoney(mva.overhead.equipmentMaintPerUnit)}</li>
                  </ul>
                </div>
                <div className="panel-list">
                  <h3>Space</h3>
                  <ul className="plain-list">
                    <li>Floor Total Sqft: {mva.overhead.spaceFloorTotalSqft.toFixed(2)}</li>
                    <li>Building Area Sqft: {mva.overhead.spaceBuildingAreaSqft.toFixed(2)}</li>
                    <li>Space / Month Used: {toMoney(mva.overhead.spaceMonthlyCostUsed)}</li>
                    <li>Space / Unit: {toMoney(mva.overhead.spacePerUnit)}</li>
                  </ul>
                </div>
                <div className="panel-list">
                  <h3>Utilities</h3>
                  <ul className="plain-list">
                    <li>Power / Unit: {toMoney(mva.overhead.powerPerUnit)}</li>
                    <li>Total Overhead / Unit: {toMoney(mva.overhead.totalOverheadPerUnit)}</li>
                    <li>Material Attrition / Unit: {toMoney(mva.materials.materialAttritionPerUnit)}</li>
                    <li>Consumables / Unit: {toMoney(mva.materials.consumablesPerUnit)}</li>
                  </ul>
                </div>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_plant_equipment' ? (
          <div className="stack-xl">
            <SectionCard title="Equipment List" description="Baseline equipment, extra fixture delta, template reuse, and mapping assumptions." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={saveCurrentAsLineStandard}>Save Current as Template</button><button type="button" className="button secondary" onClick={applySelectedLineStandard}>Apply Selected</button></div>}>
              <div className="two-column-grid">
                <div>
                  <label><span>Active line standard</span><select value={selectedLineStandardId} onChange={(event) => updateLineStandardSelection(event.target.value)}>{project.lineStandards.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                </div>
                <div className="panel-list">
                  <h3>Templates</h3>
                  <ul className="plain-list">
                    {project.lineStandards.map((item) => (
                      <li key={item.id}>{item.name} · {item.equipmentList.length} items</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="action-grid compact-actions mt-md">
                <label className="upload-card"><span>Import Equipment List Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importEquipmentSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Line Standards JSON</span><input type="file" accept="application/json" onChange={(event) => void importLineStandards(event.target.files?.[0] ?? null)} /></label>
              </div>
            </SectionCard>

            <SectionCard title="Equipment to Simulation Mapping" description="Legacy parity mapping from equipment rows into machine-group rates, with preview and explicit sync to the simulation sheet." actions={<div className="inline-actions"><button type="button" className="button secondary" onClick={applyEquipmentMappingToSimulation}>Write Derived Rates to Machine Rates</button></div>}>
              <div className="two-column-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Mapping Control</h3><p className="muted">Enable linked-rate simulation and optionally include new-product extra fixtures.</p></div>
                  <div className="form-grid cols-2">
                    <label className="checkbox-field full-span"><input type="checkbox" checked={project.plant.showEquipmentSimMapping} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, showEquipmentSimMapping: event.target.checked } }))} /><span>Use equipment mapping to drive simulation rates</span></label>
                    <label className="checkbox-field full-span"><input type="checkbox" checked={project.plant.includeExtraEquipmentInSimMapping} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, includeExtraEquipmentInSimMapping: event.target.checked } }))} /><span>Include extra equipment in mapping</span></label>
                    <label><span>Mapped Machine Groups</span><input readOnly value={equipmentSimulationMapping.rows.length} /></label>
                    <label><span>Simulation Mode</span><input readOnly value={project.plant.showEquipmentSimMapping ? 'derived from equipment' : 'manual machine sheet'} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Derived Rate Preview</h3><p className="muted">Current rate vs mapped total rate before writing back to the simulation sheet.</p></div>
                  <div className="data-table-wrapper compact">
                    <table className="data-table">
                      <thead><tr><th>Machine Group</th><th>Current Rate</th><th>Mapped Parallel Qty</th><th>Derived Rate</th><th>Linked Items</th></tr></thead>
                      <tbody>
                        {equipmentSimulationMapping.rows.length === 0 ? <tr><td colSpan={5}>No mapping links configured yet.</td></tr> : equipmentSimulationMapping.rows.map((row) => (
                          <tr key={row.machineGroup}>
                            <td>{row.machineGroup}</td>
                            <td>{row.currentRate.toFixed(2)}</td>
                            <td>{row.totalParallelQty.toFixed(2)}</td>
                            <td>{row.derivedRate.toFixed(2)}</td>
                            <td>{row.linkedItems.join(', ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>
            </SectionCard>

            <SectionCard title="Equipment Delta" description="Compares the selected line standard against baseline plus extra equipment.">
              <div className="data-table-wrapper compact">
                <table className="data-table">
                  <thead>
                    <tr><th>Process</th><th>Item</th><th>Template Qty</th><th>Baseline Qty</th><th>Extra Qty</th><th>Template Cost / Mo</th><th>Baseline Cost / Mo</th><th>Extra Cost / Mo</th><th>Total Cost / Mo</th><th>Delta / Mo</th></tr>
                  </thead>
                  <tbody>
                    {equipmentDelta.rows.map((row) => (
                      <tr key={`${row.process}-${row.item}`}>
                        <td>{row.process}</td>
                        <td>{row.item}</td>
                        <td>{row.templateQty}</td>
                        <td>{row.baselineQty}</td>
                        <td>{row.extraQty}</td>
                        <td>{toMoney(row.templateCostPerMonth)}</td>
                        <td>{toMoney(row.baselineCostPerMonth)}</td>
                        <td>{toMoney(row.extraCostPerMonth)}</td>
                        <td>{toMoney(row.totalCostPerMonth)}</td>
                        <td>{toMoney(row.deltaCostPerMonth)}</td>
                      </tr>
                    ))}
                    <tr className="summary-row"><td colSpan={5}>Total</td><td>{toMoney(equipmentDelta.totals.templateCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.baselineCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.extraCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.totalCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.deltaCostPerMonth)}</td></tr>
                  </tbody>
                </table>
              </div>
            </SectionCard>

            <SectionCard title="Equipment List (F-MVA-14)" description="Editable baseline equipment and new-product delta equipment tables.">
              <div className="stack-xl">
                <div>
                  <div className="inline-actions"><h3>Baseline equipment</h3><button type="button" className="button ghost" onClick={() => addEquipmentRow('equipmentList')}>Add</button></div>
                  <div className="data-table-wrapper compact">
                    <table className="data-table">
                      <thead><tr><th>Process</th><th>Item</th><th>Qty</th><th>Unit Price</th><th>Years</th><th>Cost/Month</th><th>Sim Group</th><th>Sim Parallel Qty</th><th>Sim Rate Override</th><th>Used</th><th>Action</th></tr></thead>
                      <tbody>
                        {project.plant.equipmentList.map((item) => {
                          const months = Math.max(1, item.depreciationYears * 12);
                          const derived = (item.qty * item.unitPrice) / months;
                          return (
                            <tr key={item.id}>
                              <td><input value={item.process} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { process: event.target.value })} /></td>
                              <td><input value={item.item} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { item: event.target.value })} /></td>
                              <td><input type="number" value={item.qty} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { qty: numberValue(event.target.value) })} /></td>
                              <td><input type="number" value={item.unitPrice} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { unitPrice: numberValue(event.target.value) })} /></td>
                              <td><input type="number" value={item.depreciationYears} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { depreciationYears: Math.max(1, numberValue(event.target.value)) })} /></td>
                              <td><input type="number" value={item.costPerMonth ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { costPerMonth: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                              <td><input value={item.simMachineGroup ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simMachineGroup: event.target.value })} /></td>
                              <td><input type="number" value={item.simParallelQty ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simParallelQty: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                              <td><input type="number" value={item.simRateOverride ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simRateOverride: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                              <td>{toMoney(item.costPerMonth ?? derived)}</td>
                              <td><button type="button" className="button ghost" onClick={() => deleteEquipmentRow('equipmentList', item.id)}>Delete</button></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <label className="checkbox-field"><input type="checkbox" checked={project.plant.useEquipmentListTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, useEquipmentListTotal: event.target.checked } }))} /><span>Use equipment list total to override Equipment Depreciation / Month</span></label>
                  <p className="muted inline-total">Equipment List Total Per Month: {toMoney(equipmentListTotalPerMonth)}</p>
                </div>
                <div>
                  <div className="inline-actions"><h3>Extra fixture / equipment</h3><button type="button" className="button ghost" onClick={() => addEquipmentRow('extraEquipmentList')}>Add</button></div>
                  <div className="data-table-wrapper compact">
                    <table className="data-table">
                      <thead><tr><th>Process</th><th>Item</th><th>Qty</th><th>Unit Price</th><th>Years</th><th>Cost/Month</th><th>Sim Group</th><th>Sim Parallel Qty</th><th>Sim Rate Override</th><th>Action</th></tr></thead>
                      <tbody>
                        {project.plant.extraEquipmentList.map((item) => (
                          <tr key={item.id}>
                            <td><input value={item.process} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { process: event.target.value })} /></td>
                            <td><input value={item.item} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { item: event.target.value })} /></td>
                            <td><input type="number" value={item.qty} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { qty: numberValue(event.target.value) })} /></td>
                            <td><input type="number" value={item.unitPrice} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { unitPrice: numberValue(event.target.value) })} /></td>
                            <td><input type="number" value={item.depreciationYears} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { depreciationYears: Math.max(1, numberValue(event.target.value)) })} /></td>
                            <td><input type="number" value={item.costPerMonth ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { costPerMonth: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                            <td><input value={item.simMachineGroup ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simMachineGroup: event.target.value })} /></td>
                            <td><input type="number" value={item.simParallelQty ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simParallelQty: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                            <td><input type="number" value={item.simRateOverride ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simRateOverride: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                            <td><button type="button" className="button ghost" onClick={() => deleteEquipmentRow('extraEquipmentList', item.id)}>Delete</button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <label className="checkbox-field"><input type="checkbox" checked={project.plant.useExtraEquipmentInTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, useExtraEquipmentInTotal: event.target.checked } }))} /><span>Include extra fixture / equipment in total</span></label>
                  <p className="muted inline-total">Extra Equipment Total Per Month: {toMoney(extraEquipmentTotalPerMonth)}</p>
                </div>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_plant_space' ? (
          <div className="stack-xl">
            <SectionCard title="Space Setup" description="Matrix mode, manual allocation, and 40/60 split controls aligned to the legacy worksheet.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Mode Selection</h3><p className="muted">Matrix: configure building / distribution and generate allocation. Manual: edit rows directly.</p></div>
                  <div className="form-grid cols-2">
                <label><span>Mode</span><select value={project.plant.spaceSettings.mode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, mode: event.target.value as ProjectState['plant']['spaceSettings']['mode'] } } }))}><option value="manual">manual</option><option value="matrix">matrix</option></select></label>
                <label><span>Space Rate / Sqft</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceRatePerSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceRatePerSqft: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Area Multiplier</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceAreaMultiplier} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Total Line Area</span><input readOnly value={matrixLineArea.toFixed(2)} /></label>
                  </div>
                </section>

                {project.plant.spaceSettings.mode === 'matrix' ? (
                  <section className="legacy-subcard">
                    <div className="legacy-subcard-header"><h3>2. Line Specification</h3><p className="muted">Line length and width used by matrix allocation.</p></div>
                    <div className="form-grid cols-2">
                      <label><span>Line Length Ft</span><input type="number" value={project.plant.spaceSettings.lineLengthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineLengthFt: numberValue(event.target.value) } } }))} /></label>
                      <label><span>Line Width Ft</span><input type="number" value={project.plant.spaceSettings.lineWidthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineWidthFt: numberValue(event.target.value) } } }))} /></label>
                    </div>
                  </section>
                ) : null}

                {project.plant.spaceSettings.mode === 'matrix' ? (
                  <section className="legacy-subcard">
                    <div className="legacy-subcard-header"><h3>1. Building Area by Floor</h3><p className="muted">BF / F1 / F2 space pool used by matrix allocation.</p></div>
                    <div className="form-grid cols-2">
                      {Object.entries(project.plant.spaceSettings.floorAreas).map(([floor, area]) => (
                        <label key={floor}><span>{floor}</span><input type="number" value={area} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, floorAreas: { ...current.plant.spaceSettings.floorAreas, [floor]: numberValue(event.target.value) } } } }))} /></label>
                      ))}
                    </div>
                  </section>
                ) : null}

                {project.plant.spaceSettings.mode === 'matrix' ? (
                  <section className="legacy-subcard full-span-card">
                    <div className="legacy-subcard-header"><h3>3. Process Distribution (%)</h3><p className="muted">Define SMT / HI / Test distribution and apply matrix calculation.</p></div>
                    <div className="form-grid cols-4">
                      {Object.entries(project.plant.spaceSettings.processDistribution).map(([process, percent]) => (
                        <label key={process}><span>{process}</span><input type="number" step="0.1" value={percent} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, processDistribution: { ...current.plant.spaceSettings.processDistribution, [process]: numberValue(event.target.value) } } } }))} /></label>
                      ))}
                    </div>
                    <div className="inline-actions mt-md"><button type="button" className="button primary" onClick={applyMatrixSpaceAllocation}>Apply Matrix Calculation</button></div>
                  </section>
                ) : null}

                <section className="legacy-subcard full-span-card">
                  <div className="legacy-subcard-header"><h3>40 / 60 Split</h3><p className="muted">Quick helper for SMT 40% and HI 60% allocation.</p></div>
                  <div className="form-grid cols-4">
                <label><span>40/60 Split</span><select value={project.plant.spaceSettings.split4060Enabled ? 'enabled' : 'disabled'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060Enabled: event.target.value === 'enabled' } } }))}><option value="disabled">disabled</option><option value="enabled">enabled</option></select></label>
                <label><span>40/60 Total Floor</span><input type="number" value={project.plant.spaceSettings.split4060TotalFloorSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060TotalFloorSqft: numberValue(event.target.value) } } }))} /></label>
                <label><span>40/60 Hi Mode</span><select value={project.plant.spaceSettings.split4060HiMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060HiMode: event.target.value as ProjectState['plant']['spaceSettings']['split4060HiMode'] } } }))}><option value="FA">FA</option><option value="FBT">FBT</option><option value="FA_FBT">FA_FBT</option></select></label>
                    <div className="button-grid-align"><button type="button" className="button secondary" onClick={applySplit4060SpaceAllocation} disabled={!project.plant.spaceSettings.split4060Enabled}>Apply 40/60 Split</button></div>
                  </div>
                </section>
              </div>
            </SectionCard>

            <SectionCard title={`Space Allocation${project.plant.spaceSettings.mode === 'matrix' ? ' (Generated)' : ' (Manual)'}`} description="Editable space rows with process, area, and rate.">
              {project.plant.spaceSettings.mode === 'manual' ? (
                <div className="inline-actions">
                  <button type="button" className="button ghost" onClick={addSpaceAllocationRow} data-testid="mva-space-add">Add Row</button>
                  <label className="upload-inline"><span>Import Space Setup CSV</span><input type="file" accept=".csv,text/csv" data-testid="mva-import-space-setup" onChange={(event) => void importSpaceSetup(event.target.files?.[0] ?? null)} /></label>
                </div>
              ) : null}
              <div className="data-table-wrapper compact mt-md">
                <table className="data-table">
                  <thead><tr><th>Floor</th><th>Process</th><th>Area (sq.ft.)</th><th>Rate</th><th>Monthly Cost</th><th>Action</th></tr></thead>
                  <tbody>
                    {project.plant.spaceAllocation.map((row) => (
                      <tr key={row.id}>
                        <td><input value={row.floor} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { floor: event.target.value })} /></td>
                        <td><input value={row.process} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { process: event.target.value })} /></td>
                        <td><input type="number" value={row.areaSqft} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { areaSqft: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={row.ratePerSqft ?? 0} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { ratePerSqft: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={row.monthlyCost ?? ''} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { monthlyCost: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><button type="button" className="button ghost" disabled={project.plant.spaceSettings.mode === 'matrix'} onClick={() => deleteSpaceAllocationRow(row.id)}>Delete</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <label className="checkbox-field mt-md"><input type="checkbox" checked={project.plant.spaceSettings.useSpaceAllocationTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, useSpaceAllocationTotal: event.target.checked } } }))} /><span>Use space allocation total to override Space / Rental / Cleaning (Monthly)</span></label>
            </SectionCard>

            <SectionCard title="Space Results" description="Resolved per-row space cost and aggregate building impact.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead><tr><th>Floor</th><th>Process</th><th>Area Sqft</th><th>Monthly Cost Used</th></tr></thead>
                  <tbody>
                    {mva.spaceAllocation.map((row) => (
                      <tr key={row.id}><td>{row.floor}</td><td>{row.process}</td><td>{row.areaSqft}</td><td>{toMoney(row.monthlyCostUsed)}</td></tr>
                    ))}
                    <tr className="summary-row"><td colSpan={2}>Total</td><td>{mva.overhead.spaceFloorTotalSqft.toFixed(2)}</td><td>{toMoney(mva.overhead.spaceMonthlyCostUsed)}</td></tr>
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_plant_dloh_idl' ? (
          <div className="stack-xl">
            <SectionCard title="DLOH-L & IDL Setup" description="Import, IDL mode control, and editable direct / indirect labor rows.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Import and Mode</h3><p className="muted">CSV import, effective mode, and row counts.</p></div>
                  <div className="form-grid cols-2">
                    <label className="full-span"><span>Import DLOH-L &amp; IDL Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importDlohIdlSetup(event.target.files?.[0] ?? null)} /></label>
                    <label><span>IDL Mode</span><select value={project.plant.idlUiMode} onChange={(event) => updateProject((current) => {
                      const idlUiMode = event.target.value as ProjectState['plant']['idlUiMode'];
                      const plant: ProjectState['plant'] = { ...current.plant, idlUiMode };
                      plant.indirectLaborRows = resolveIndirectLaborRows(plant, idlUiMode);
                      return { ...current, plant };
                    })}><option value="auto">auto</option><option value="L10">L10</option><option value="L6">L6</option></select></label>
                    <label><span>Effective IDL Mode</span><input readOnly value={project.plant.idlUiMode === 'auto' ? project.plant.processType : project.plant.idlUiMode} /></label>
                    <label><span>L10 Loaded Rows</span><input readOnly value={project.plant.idlRowsByMode.L10.length} /></label>
                    <label><span>L6 Loaded Rows</span><input readOnly value={project.plant.idlRowsByMode.L6.length} /></label>
                  </div>
                </section>
              </div>
            </SectionCard>

            <SectionCard title="Editable Labor Tables" description="Editable direct and indirect labor rows aligned to the legacy plant labor worksheets.">
              <div className="two-column-grid">
                <div>
                  <div className="inline-actions"><h3 className="subsection-title">Direct labor</h3><button type="button" className="button ghost" onClick={addDirectLaborRow}>Add</button></div>
                  <div className="editable-list">
                    {project.plant.directLaborRows.map((row) => (
                      <div className="list-row wide-5" key={row.id}>
                        <input value={row.name} onChange={(event) => updateDirectLaborRow(row.id, { name: event.target.value })} placeholder="Name" />
                        <input value={row.process ?? ''} onChange={(event) => updateDirectLaborRow(row.id, { process: event.target.value })} placeholder="Process" />
                        <input type="number" value={row.headcount} onChange={(event) => updateDirectLaborRow(row.id, { headcount: numberValue(event.target.value) })} placeholder="HC" />
                        <input type="number" value={row.overrideUph ?? ''} onChange={(event) => updateDirectLaborRow(row.id, { overrideUph: event.target.value === '' ? null : numberValue(event.target.value), uphSource: event.target.value === '' ? 'line' : 'override' })} placeholder="Override UPH" />
                        <button type="button" className="button ghost" onClick={() => deleteDirectLaborRow(row.id)}>Delete</button>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="inline-actions"><h3 className="subsection-title">Indirect labor</h3><button type="button" className="button ghost" onClick={addIndirectLaborRow}>Add</button></div>
                  <div className="editable-list">
                    {project.plant.indirectLaborRows.map((row) => (
                      <div className="list-row wide-6" key={row.id}>
                        <input value={row.name} onChange={(event) => updateIndirectLaborRow(row.id, { name: event.target.value })} placeholder="Name" />
                        <input value={row.department ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { department: event.target.value })} placeholder="Department" />
                        <input value={row.role ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { role: event.target.value })} placeholder="Role" />
                        <input value={row.rrNote ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { rrNote: event.target.value })} placeholder="R&R Note" />
                        <input type="number" value={row.headcount} onChange={(event) => updateIndirectLaborRow(row.id, { headcount: numberValue(event.target.value) })} placeholder="HC" />
                        <button type="button" className="button ghost" onClick={() => deleteIndirectLaborRow(row.id)}>Delete</button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Current Costed Labor Tables" description="Effective headcount and cost per unit from the current line assumptions.">
              <div className="two-column-grid">
                <div className="data-table-wrapper compact">
                  <table className="data-table">
                    <thead><tr><th>Name</th><th>HC</th><th>Cost / Unit</th></tr></thead>
                    <tbody>
                      {mva.directLabor.map((row) => (
                        <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{toMoney(row.costPerUnit)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="data-table-wrapper compact">
                  <table className="data-table">
                    <thead><tr><th>Name</th><th>HC</th><th>Cost / Unit</th></tr></thead>
                    <tbody>
                      {mva.indirectLabor.map((row) => (
                        <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{toMoney(row.costPerUnit)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mpm_l10' ? (
          <div className="stack-xl">
            <SectionCard title="MPM Report Setup (L10)" description="Legacy-aligned L10 product setup, schedule, test time, and volume assumptions.">
              <div className="action-grid compact-actions">
                <label className="upload-card"><span>Import L10 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10MpmSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L10 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10LaborEstimation(event.target.files?.[0] ?? null)} /></label>
              </div>
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Product Information</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>BU</span><input value={project.productL10.bu} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, bu: event.target.value } }))} /></label>
                    <label><span>Customer</span><input value={project.productL10.customer} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, customer: event.target.value } }))} /></label>
                    <label><span>Project Name</span><input value={project.productL10.projectName} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, projectName: event.target.value } }))} /></label>
                    <label><span>Yield (FPY)</span><input type="number" step="0.01" value={project.productL10.yield ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, yield: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Product Dimension</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Size</span><input value={project.productL10.sizeMm} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, sizeMm: event.target.value } }))} /></label>
                    <label><span>Weight (Kg/Tool)</span><input type="number" value={project.productL10.weightKgPerTool ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, weightKgPerTool: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Test Time Setup</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Handling (sec)</span><input type="number" value={project.productL10.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { handlingSec: numberValue(event.target.value) }) } }))} /></label>
                    <label><span>Function (sec)</span><input type="number" value={project.productL10.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { functionSec: numberValue(event.target.value) }) } }))} /></label>
                    <label className="full-span"><span>Total (sec)</span><input readOnly value={project.productL10.testTime.totalSec} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Volume and Lifecycle</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>RFQ Qty / Month</span><input type="number" value={project.productL10.rfqQtyPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, rfqQtyPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Probe Life Cycle</span><input type="number" value={project.productL10.probeLifeCycle ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, probeLifeCycle: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label className="full-span"><span>RFQ Qty / Life Cycle</span><input type="number" value={project.productL10.rfqQtyLifeCycle ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, rfqQtyLifeCycle: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard full-span-card">
                  <div className="legacy-subcard-header"><h3>Working Schedule</h3></div>
                  <div className="form-grid cols-4">
                    <label><span>Working Day (Year)</span><input type="number" value={project.productL10.workDaysPerYear ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerYear: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Working Day (Month)</span><input type="number" value={project.productL10.workDaysPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Working Day (Week)</span><input type="number" value={project.productL10.workDaysPerWeek ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerWeek: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Shift</span><input type="number" value={project.productL10.shiftsPerDay ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, shiftsPerDay: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label className="full-span"><span>Working Hour / Shift</span><input type="number" value={project.productL10.hoursPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, hoursPerShift: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
              </div>
            </SectionCard>

            <SectionCard title="Volume and Yield Strategy" description="Legacy L10 volume mode and yield usage controls.">
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Volume</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Demand Mode</span><select value={project.plant.mvaVolume.demandMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, demandMode: event.target.value as 'monthly' | 'weekly' } } }))}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></label>
                    <label><span>Work Days / Month</span><input type="number" value={project.plant.mvaVolume.workDaysPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerMonth: numberValue(event.target.value) } } }))} /></label>
                    <label><span>RFQ Qty / Month</span><input type="number" value={project.plant.mvaVolume.rfqQtyPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, rfqQtyPerMonth: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Weekly Demand</span><input type="number" value={project.plant.mvaVolume.weeklyDemand} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, weeklyDemand: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Yield / FPY / VPY</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Strategy</span><select value={project.plant.yield.strategy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, strategy: event.target.value as 'ignore' | 'apply_to_capacity' } } }))}><option value="ignore">Ignore</option><option value="apply_to_capacity">Apply to capacity</option></select></label>
                    <label><span>Use L10 FPY</span><select value={project.plant.yield.useL10Fpy ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, useL10Fpy: event.target.value === 'yes' } } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
                    <label><span>FPY</span><input type="number" step="0.01" value={project.plant.yield.fpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, fpy: numberValue(event.target.value) } } }))} /></label>
                    <label><span>VPY</span><input type="number" step="0.01" value={project.plant.yield.vpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, vpy: numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard full-span-card">
                  <div className="legacy-subcard-header"><h3>Derived Output</h3></div>
                  <div className="form-grid cols-4">
                    <label><span>UPH</span><input readOnly value={project.productL10.uph ?? l10Mva.lineUphUsedForCapacity.toFixed(2)} /></label>
                    <label><span>CT Sec</span><input readOnly value={project.productL10.ctSec ?? l10Simulation.taktTime.toFixed(2)} /></label>
                    <label><span>Monthly Volume Used</span><input readOnly value={l10Mva.monthlyVolume.toFixed(2)} /></label>
                    <label><span>Yield Factor</span><input readOnly value={l10Mva.yield.yieldFactor.toFixed(4)} /></label>
                  </div>
                </section>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mpm_l6' ? (
          <div className="stack-xl">
            <SectionCard title="MPM Report Setup (L6)" description="Legacy-aligned L6 by-product, header, routing, and test-time inputs.">
              <div className="action-grid compact-actions">
                <label className="upload-card"><span>Import L6 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6MpmSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L6 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6LaborEstimation(event.target.files?.[0] ?? null)} /></label>
              </div>
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>By Product</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>BU</span><input value={project.productL6.bu} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, bu: event.target.value } }))} /></label>
                    <label><span>Customer</span><input value={project.productL6.customer} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, customer: event.target.value } }))} /></label>
                    <label><span>Model Name</span><input value={project.productL6.modelName ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, modelName: event.target.value } }))} /></label>
                    <label><span>PN</span><input value={project.productL6.pn} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pn: event.target.value } }))} /></label>
                    <label><span>SKU</span><input value={project.productL6.sku ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, sku: event.target.value } }))} /></label>
                    <label><span>PCB Params / Size</span><input value={project.productL6.pcbSize} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pcbSize: event.target.value } }))} /></label>
                    <label><span>Boards / Panel</span><input type="number" value={project.productL6.boardsPerPanel} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, boardsPerPanel: numberValue(event.target.value) } }))} /></label>
                    <label><span>Printing</span><input value={project.laborTimeL6.header.printing ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, printing: event.target.value } } }))} /></label>
                    <label><span>Line Change</span><input value={project.laborTimeL6.header.lineChange ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, lineChange: event.target.value } } }))} /></label>
                    <label className="full-span"><span>ICT to FBT</span><input value={project.laborTimeL6.header.ictToFbt ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, ictToFbt: event.target.value } } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Header Parameters</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Category</span><input value={project.laborTimeL6.header.category ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, category: event.target.value } } }))} /></label>
                    <label><span>Production Line</span><input value={project.laborTimeL6.header.productionLine ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, productionLine: event.target.value } } }))} /></label>
                    <label><span>Annual Demand</span><input type="number" value={project.laborTimeL6.header.annualDemand ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, annualDemand: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Monthly Demand</span><input type="number" value={project.laborTimeL6.header.monthlyDemand ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, monthlyDemand: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Multi-Panel Qty</span><input type="number" value={project.laborTimeL6.header.multiPanelQty ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, multiPanelQty: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Line Capability / Shift</span><input type="number" value={project.laborTimeL6.header.lineCapabilityPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, lineCapabilityPerShift: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Ex-Rate RMB/USD</span><input type="number" step="0.01" value={project.laborTimeL6.header.exRateRmbUsd ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, exRateRmbUsd: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Service Hourly Wage</span><input type="number" step="0.01" value={project.laborTimeL6.header.serviceHourlyWage ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, serviceHourlyWage: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard full-span-card">
                  <div className="legacy-subcard-header"><h3>Product Setup</h3></div>
                  <div className="form-grid cols-4">
                    <label><span>Side 1 Parts</span><input type="number" value={project.productL6.side1PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side1PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Side 2 Parts</span><input type="number" value={project.productL6.side2PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side2PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Offline Blasting Part</span><input type="number" value={project.productL6.offLineBlastingPartCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, offLineBlastingPartCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>DIP Parts Type</span><input value={project.productL6.dipPartsType} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, dipPartsType: event.target.value } }))} /></label>
                    <label><span>Selective WS</span><input value={project.productL6.selectiveWs} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, selectiveWs: event.target.value } }))} /></label>
                    <label><span>Assembly Part</span><input value={project.productL6.assemblyPart} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, assemblyPart: event.target.value } }))} /></label>
                    <label><span>RFQ Qty / Month</span><input type="number" value={project.productL6.rfqQtyPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, rfqQtyPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                    <label><span>Handling Sec</span><input type="number" value={project.productL6.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, handlingSec: numberValue(event.target.value) } } }))} /></label>
                    <label><span>Function Sec</span><input type="number" value={project.productL6.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, functionSec: numberValue(event.target.value) } } }))} /></label>
                    <label className="full-span"><span>Station Path</span><input value={project.productL6.stationPath} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, stationPath: event.target.value } }))} /></label>
                  </div>
                </section>
                <section className="legacy-subcard full-span-card">
                  <div className="legacy-subcard-header"><h3>Routing Time (sec)</h3><p className="muted">Per-station routing inputs shown explicitly like the legacy template.</p></div>
                  <div className="editable-list">
                    {Object.entries(project.productL6.routingTimesSec).map(([key, value]) => (
                      <div className="list-row wide-4" key={key}>
                        <input value={key} onChange={(event) => updateL6RoutingTime(key, { nextKey: event.target.value, value })} placeholder="Routing Station" />
                        <input type="number" value={value} onChange={(event) => updateL6RoutingTime(key, { value: numberValue(event.target.value) })} placeholder="Seconds" />
                        <div className="matrix-readonly-cell">{value > 0 ? `${(value / 60).toFixed(2)} min` : '0.00 min'}</div>
                        <button type="button" className="button ghost" onClick={() => deleteL6RoutingTime(key)}>Delete</button>
                      </div>
                    ))}
                    <button type="button" className="button secondary" onClick={addL6RoutingTime}>Add Routing Time Row</button>
                  </div>
                </section>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_labor_l10' ? (
          <div className="stack-xl">
            <SectionCard title="Labor Time (L10)" description="Import, meta, and station matrix for L10 labor time estimation." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={addL10Station}>Add Station</button><button type="button" className="button secondary" onClick={applyL10StationsToDirectLabor}>Apply to Direct Labor</button></div>}>
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Import Mini-MOST Time Estimation Data</h3></div>
                  <div className="form-grid cols-2">
                    <label className="full-span"><span>Import CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10LaborEstimation(event.target.files?.[0] ?? null)} /></label>
                    <label><span>Source</span><input readOnly value={project.laborTimeL10.source} /></label>
                    <label><span>Parsed Stations</span><input readOnly value={project.laborTimeL10.stations.length} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Meta</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Hours / Shift</span><input type="number" value={project.laborTimeL10.meta.hoursPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, hoursPerShift: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Target Time</span><input type="number" value={project.laborTimeL10.meta.targetTime ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, targetTime: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Shifts / Day</span><input type="number" value={project.laborTimeL10.meta.shiftsPerDay ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, shiftsPerDay: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Work Days / Week</span><input type="number" value={project.laborTimeL10.meta.workDaysPerWeek ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, workDaysPerWeek: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Work Days / Month</span><input type="number" value={project.laborTimeL10.meta.workDaysPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, workDaysPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                    <label><span>FPY</span><input type="number" step="0.01" value={project.laborTimeL10.meta.fpy ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, fpy: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
                  </div>
                </section>
              </div>
              <div className="data-table-wrapper sticky-header tall-table mt-md">
                <table className="data-table matrix-table">
                  <thead><tr><th>Station</th><th>HC</th><th>Mach/Para</th><th>CT(s)</th><th>Allow</th><th>Avg CT</th><th>UPH</th><th>Shift Capa</th><th>Day Capa</th><th>Wk Capa</th><th>Mo Capa</th><th>Touch(min)</th><th>Handling(s)</th><th>1-Man-N-MC</th><th>Time(min)/cyc</th><th>Run/Day</th><th>Action</th></tr></thead>
                  <tbody>
                    {l10StationSnapshots.map((station) => (
                      <tr key={station.id}>
                        <td><input value={station.name} onChange={(event) => updateL10Station(station.id, { name: event.target.value })} /></td>
                        <td><input type="number" value={station.laborHc} onChange={(event) => updateL10Station(station.id, { laborHc: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.parallelStations} onChange={(event) => updateL10Station(station.id, { parallelStations: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.cycleTimeSec ?? 0} onChange={(event) => updateL10Station(station.id, { cycleTimeSec: numberValue(event.target.value) })} /></td>
                        <td><input type="number" step="0.01" value={station.allowanceFactor} onChange={(event) => updateL10Station(station.id, { allowanceFactor: numberValue(event.target.value) })} /></td>
                        <td>{station.avgCycleTimeSec.toFixed(2)}</td><td>{station.uph.toFixed(2)}</td><td>{station.perShiftCapa.toFixed(2)}</td><td>{station.dailyCapa.toFixed(2)}</td><td>{station.weeklyCapa.toFixed(2)}</td><td>{station.monthlyCapa.toFixed(2)}</td>
                        <td><input type="number" value={station.touchTimeMin ?? ''} onChange={(event) => updateL10Station(station.id, { touchTimeMin: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.handlingTimeSec ?? ''} onChange={(event) => updateL10Station(station.id, { handlingTimeSec: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.oneManMultiMachineCount ?? ''} onChange={(event) => updateL10Station(station.id, { oneManMultiMachineCount: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.timeMinPerCycle ?? ''} onChange={(event) => updateL10Station(station.id, { timeMinPerCycle: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={station.runPerDay ?? ''} onChange={(event) => updateL10Station(station.id, { runPerDay: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><button type="button" className="button ghost" onClick={() => deleteL10Station(station.id)}>Delete</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_labor_l6' ? (
          <div className="stack-xl">
            <SectionCard title="Labor Time Estimation (L6)" description="Legacy-style L6 segment matrix with per-segment station columns and derived CT / UPH outputs." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={addL6Segment}>Add Segment</button><button type="button" className="button secondary" onClick={applyL6StationsToDirectLabor}>Apply to Direct Labor</button></div>}>
              <div className="legacy-page-grid">
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Import Mini-MOST Time Estimation Data</h3></div>
                  <div className="form-grid cols-2">
                    <label className="full-span"><span>Import L6 labor time estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6LaborEstimation(event.target.files?.[0] ?? null)} /></label>
                    <label><span>Source</span><input readOnly value={project.laborTimeL6.source} /></label>
                    <label><span>Parsed Segments</span><input readOnly value={l6LaborSummary.segments} /></label>
                    <label><span>Parsed Stations</span><input readOnly value={l6LaborSummary.stations} /></label>
                  </div>
                </section>
                <section className="legacy-subcard">
                  <div className="legacy-subcard-header"><h3>Detailed Statistics Breakdown</h3></div>
                  <div className="form-grid cols-2">
                    <label><span>Mfg Time / Unit</span><input type="number" value={project.laborTimeL6.header.manufacturingTimePerUnit ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, manufacturingTimePerUnit: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Output / Line / Shift</span><input type="number" value={project.laborTimeL6.header.outputPerLinePerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, outputPerLinePerShift: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
                    <label><span>Total DL Online</span><input readOnly value={l6LaborSummary.totalHc.toFixed(2)} /></label>
                    <label><span>Bottleneck UPH</span><input readOnly value={l6LaborSummary.bottleneckUph.toFixed(2)} /></label>
                  </div>
                </section>
              </div>
              <div className="stack-xl mt-md">
                {l6SegmentSnapshots.map((segment) => {
                  const activeStations = segment.stations.filter((station) => !station.isTotal);
                  const segmentTotalHc = activeStations.reduce((sum, station) => sum + (station.dlOnline ?? station.laborHc ?? 0), 0);
                  const segmentBottleneck = activeStations.reduce((lowest, station) => {
                    const next = station.uph ?? 0;
                    if (lowest === 0) return next;
                    if (next === 0) return lowest;
                    return Math.min(lowest, next);
                  }, 0);
                  return (
                    <section className="segment-matrix-card" key={segment.id}>
                      <div className="segment-matrix-header">
                        <label>
                          <span>Segment Name</span>
                          <input value={segment.name} onChange={(event) => updateL6SegmentName(segment.id, event.target.value)} />
                        </label>
                        <div className="inline-actions">
                          <button type="button" className="button ghost" onClick={() => addL6Station(segment.id)}>Add Station</button>
                          <button type="button" className="button ghost" onClick={() => deleteL6Segment(segment.id)} disabled={l6SegmentSnapshots.length === 1}>Delete Segment</button>
                        </div>
                      </div>
                      <div className="data-table-wrapper sticky-header tall-table mt-md">
                        <table className="data-table matrix-table">
                          <thead>
                            <tr>
                              <th>Item</th>
                              {segment.stations.map((station) => (
                                <th key={station.id}>{station.name || `Station ${station.stationNo ?? ''}`}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            <tr>
                              <th>Station</th>
                              {segment.stations.map((station) => <td key={`${station.id}-name`}><input value={station.name} onChange={(event) => updateL6Station(segment.id, station.id, { name: event.target.value })} /></td>)}
                            </tr>
                            <tr>
                              <th>No</th>
                              {segment.stations.map((station) => <td key={`${station.id}-no`}><input type="number" value={station.stationNo ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { stationNo: event.target.value === '' ? undefined : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>HC</th>
                              {segment.stations.map((station) => <td key={`${station.id}-hc`}><input type="number" value={station.laborHc} onChange={(event) => updateL6Station(segment.id, station.id, { laborHc: numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Fixture</th>
                              {segment.stations.map((station) => <td key={`${station.id}-fixture`}><input type="number" value={station.parallelStations} onChange={(event) => updateL6Station(segment.id, station.id, { parallelStations: numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Cycle Time</th>
                              {segment.stations.map((station) => <td key={`${station.id}-ct`}><input type="number" value={station.cycleTimeSec ?? 0} onChange={(event) => updateL6Station(segment.id, station.id, { cycleTimeSec: numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Allowance Rate</th>
                              {segment.stations.map((station) => <td key={`${station.id}-allow`}><input type="number" step="0.01" value={station.allowanceRate ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { allowanceRate: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Std Man Hour</th>
                              {segment.stations.map((station) => <td key={`${station.id}-std`}><input type="number" step="0.01" value={station.stdManHours ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { stdManHours: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>VPY</th>
                              {segment.stations.map((station) => <td key={`${station.id}-vpy`}><input type="number" step="0.01" value={station.vpy ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { vpy: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Shift Rate</th>
                              {segment.stations.map((station) => <td key={`${station.id}-shift`}><input type="number" step="0.01" value={station.shiftRate ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { shiftRate: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>DL Online</th>
                              {segment.stations.map((station) => <td key={`${station.id}-dl`}><input type="number" value={station.dlOnline ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { dlOnline: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}
                            </tr>
                            <tr>
                              <th>Avg CT</th>
                              {segment.stations.map((station) => <td key={`${station.id}-avg`} className="matrix-readonly-cell">{(station.avgCycleTimeSec ?? 0).toFixed(2)}</td>)}
                            </tr>
                            <tr>
                              <th>UPH</th>
                              {segment.stations.map((station) => <td key={`${station.id}-uph`} className="matrix-readonly-cell">{(station.uph ?? 0).toFixed(2)}</td>)}
                            </tr>
                            <tr>
                              <th>Action</th>
                              {segment.stations.map((station) => <td key={`${station.id}-action`}><button type="button" className="button ghost" onClick={() => deleteL6Station(segment.id, station.id)}>Delete</button></td>)}
                            </tr>
                          </tbody>
                        </table>
                      </div>
                      <div className="matrix-summary-strip">
                        <div><span>Segment Stations</span><strong>{activeStations.length}</strong></div>
                        <div><span>Segment DL Online</span><strong>{segmentTotalHc.toFixed(2)}</strong></div>
                        <div><span>Segment Bottleneck UPH</span><strong>{segmentBottleneck.toFixed(2)}</strong></div>
                      </div>
                    </section>
                  );
                })}
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'mva_summary_l10' ? renderSummaryPage({ processLabel: 'L10', summarySimulation: l10Simulation, summaryMva: l10Mva, summaryText: l10SummaryCsv }) : null}

        {activeTab === 'mva_summary_l6' ? renderSummaryPage({ processLabel: 'L6', summarySimulation: l6Simulation, summaryMva: l6Mva, summaryText: l6SummaryCsv }) : null}
      </main>
    </div>
  );
}
