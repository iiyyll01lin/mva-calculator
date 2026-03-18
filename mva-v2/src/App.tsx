import { startTransition, useEffect, useMemo, useState } from 'react';
import { KpiCard } from './components/KpiCard';
import { SectionCard } from './components/SectionCard';
import { Sidebar } from './components/Sidebar';
import { calculateMva, calculateSimulation, computeEquipmentDelta } from './domain/calculations';
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
  const [activeTab, setActiveTab] = useState<TabId>('dashboard');
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
  const ratesWorkspaceVisible = activeTab === 'rates' || activeTab === 'equipment' || activeTab === 'space' || activeTab === 'labor';

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

  return (
    <div className="app-shell">
      <Sidebar activeTab={activeTab} onSelect={setActiveTab} />
      <main className="content-shell">
        <header className="hero-panel">
          <div>
            <p className="eyebrow">Release Candidate Workspace</p>
            <h1>{project.basicInfo.modelName}</h1>
            <p className="muted">A modular rewrite of the legacy MVA workbook with typed state, repeatable exports, and automated tests.</p>
          </div>
          <div className="hero-actions">
            <button type="button" className="button secondary" onClick={exportProject}>Export Project JSON</button>
            <button type="button" className="button primary" onClick={exportSummary}>Export Summary CSV</button>
          </div>
        </header>

        {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

        {activeTab === 'dashboard' ? (
          <div className="stack-xl">
            <section className="kpi-grid" data-testid="kpi-grid">
              <KpiCard label="UPH" value={simulation.uph.toFixed(2)} />
              <KpiCard label="Takt Time" value={`${simulation.taktTime.toFixed(2)} sec`} />
              <KpiCard label="Weekly Output" value={simulation.weeklyOutput.toFixed(2)} tone={simulation.weeklyOutput >= project.basicInfo.demandWeekly ? 'good' : 'warn'} />
              <KpiCard label="Total Cost / Unit" value={toMoney(mva.totalPerUnit)} />
            </section>

            <SectionCard title="Release Gate" description="The summary view shows what is blocking release readiness right now.">
              <div className="two-column-grid">
                <div className="panel-list">
                  <h3>Simulation health</h3>
                  <ul className="plain-list">
                    <li>Bottleneck process: {simulation.bottleneckProcess}</li>
                    <li>Line balance efficiency: {simulation.lineBalanceEfficiency.toFixed(2)}%</li>
                    <li>Demand coverage: {simulation.weeklyOutput >= project.basicInfo.demandWeekly ? 'Pass' : 'Fail'}</li>
                  </ul>
                </div>
                <div className="panel-list">
                  <h3>MVA warnings</h3>
                  <ul className="plain-list" data-testid="warning-list">
                    {mva.warnings.length === 0 ? <li>No blocking warnings.</li> : mva.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                  </ul>
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Cost Rollup" description="Current per-unit contribution by cost category.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr><th>Category</th><th>Value</th></tr>
                  </thead>
                  <tbody>
                    {mva.costLines.map((line) => (
                      <tr key={line.key}><td>{line.label}</td><td>{toMoney(line.value)}</td></tr>
                    ))}
                    <tr className="summary-row"><td>Total</td><td>{toMoney(mva.totalPerUnit)}</td></tr>
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'simulation' ? (
          <div className="stack-xl">
            <SectionCard title="Planning Inputs" description="Legacy simulation inputs preserved in a typed form.">
              <div className="form-grid cols-4">
                <label><span>Model Name</span><input aria-label="Model Name" value={project.basicInfo.modelName} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, modelName: event.target.value } }))} /></label>
                <label><span>Weekly Demand</span><input aria-label="Weekly Demand" type="number" min="0" value={project.basicInfo.demandWeekly} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, demandWeekly: Math.max(0, numberValue(event.target.value)) } }))} /></label>
                <label><span>Shifts / Day</span><input aria-label="Shifts Per Day" type="number" value={project.basicInfo.shiftsPerDay} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, shiftsPerDay: numberValue(event.target.value) } }))} /></label>
                <label><span>Hours / Shift</span><input aria-label="Hours Per Shift" type="number" value={project.basicInfo.hoursPerShift} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hoursPerShift: numberValue(event.target.value) } }))} /></label>
                <label><span>Break Minutes / Shift</span><input type="number" value={project.basicInfo.breaksPerShiftMin} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, breaksPerShiftMin: numberValue(event.target.value) } }))} /></label>
                <label><span>Target Utilization</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.targetUtilization} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, targetUtilization: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                <label><span>Placement OEE</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeePlacement} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeePlacement: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
                <label><span>Other OEE</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeeOthers} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeeOthers: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
              </div>
            </SectionCard>

            <SectionCard title="Machine Rates" description="Rate edits feed simulation immediately.">
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

            <SectionCard title="BOM Mapping" description="Placement cycle time depends on side and package allocation.">
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

            <SectionCard title="Process Output" description="Current bottleneck and utilization by station.">
              <div className="data-table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr><th>Step</th><th>Process</th><th>Side</th><th>Cycle Time</th><th>Utilization</th></tr>
                  </thead>
                  <tbody>
                    {simulation.steps.map((step) => (
                      <tr key={`${step.step}-${step.process}`} className={step.isBottleneck ? 'highlight-row' : ''}>
                        <td>{step.step}</td>
                        <td>{step.process}</td>
                        <td>{step.side}</td>
                        <td>{step.cycleTime.toFixed(2)}</td>
                        <td>{step.utilization.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {ratesWorkspaceVisible ? (
          <div className="stack-xl">
            <SectionCard title="Labor and Yield" description="The main cost drivers for direct and indirect labor.">
              <div className="form-grid cols-4">
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
                <label><span>Direct Labor Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.directLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, directLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
                <label><span>Indirect Labor Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.indirectLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, indirectLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
                <label><span>Efficiency</span><input type="number" step="0.01" value={project.plant.mvaRates.efficiency} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, efficiency: numberValue(event.target.value) } } }))} /></label>
                <label><span>Demand Mode</span><select value={project.plant.mvaVolume.demandMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, demandMode: event.target.value as 'monthly' | 'weekly' } } }))}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></label>
                <label><span>RFQ / Month</span><input type="number" value={project.plant.mvaVolume.rfqQtyPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, rfqQtyPerMonth: numberValue(event.target.value) } } }))} /></label>
                <label><span>Yield Strategy</span><select value={project.plant.yield.strategy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, strategy: event.target.value as 'ignore' | 'apply_to_capacity' } } }))}><option value="ignore">ignore</option><option value="apply_to_capacity">apply_to_capacity</option></select></label>
                <label><span>FPY</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.fpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, fpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                <label><span>VPY</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.vpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, vpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
                <label><span>Equipment Depreciation / Month</span><input type="number" step="0.01" value={activeOverhead.equipmentDepreciationPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentDepreciationPerMonth: numberValue(event.target.value) } } } }))} /></label>
                <label><span>Space / Month</span><input type="number" step="0.01" value={activeOverhead.spaceMonthlyCost} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], spaceMonthlyCost: numberValue(event.target.value) } } } }))} /></label>
                <label><span>Power / Unit</span><input type="number" step="0.01" value={activeOverhead.powerCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], powerCostPerUnit: numberValue(event.target.value) } } } }))} /></label>
              </div>
            </SectionCard>

            <SectionCard title="Space Strategy" description="Manual, matrix, and 40/60 split settings are available from the same project state.">
              <div className="form-grid cols-4">
                <label><span>Mode</span><select value={project.plant.spaceSettings.mode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, mode: event.target.value as ProjectState['plant']['spaceSettings']['mode'] } } }))}><option value="manual">manual</option><option value="matrix">matrix</option></select></label>
                <label><span>Space Rate / Sqft</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceRatePerSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceRatePerSqft: numberValue(event.target.value) } } }))} /></label>
                <label><span>Line Length Ft</span><input type="number" value={project.plant.spaceSettings.lineLengthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineLengthFt: numberValue(event.target.value) } } }))} /></label>
                <label><span>Line Width Ft</span><input type="number" value={project.plant.spaceSettings.lineWidthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineWidthFt: numberValue(event.target.value) } } }))} /></label>
                <label><span>Area Multiplier</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceAreaMultiplier} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: numberValue(event.target.value) } } }))} /></label>
                <label><span>Building Override Sqft</span><input type="number" value={project.plant.spaceSettings.buildingAreaOverrideSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, buildingAreaOverrideSqft: numberValue(event.target.value) } } }))} /></label>
                <label><span>40/60 Split</span><select value={project.plant.spaceSettings.split4060Enabled ? 'enabled' : 'disabled'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060Enabled: event.target.value === 'enabled' } } }))}><option value="disabled">disabled</option><option value="enabled">enabled</option></select></label>
                <label><span>40/60 Total Floor</span><input type="number" value={project.plant.spaceSettings.split4060TotalFloorSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060TotalFloorSqft: numberValue(event.target.value) } } }))} /></label>
              </div>
            </SectionCard>

            <SectionCard title="Labor Tables" description="Direct and indirect labor are maintained separately.">
              <div className="two-column-grid">
                <div>
                  <h3>Direct labor</h3>
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
                </div>
                <div>
                  <h3>Indirect labor</h3>
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
              </div>
            </SectionCard>

            <SectionCard title="Equipment Delta" description="Compares the selected line standard against baseline plus extra equipment.">
              <div className="data-table-wrapper compact">
                <table className="data-table">
                  <thead>
                    <tr><th>Process</th><th>Item</th><th>Template Cost / Mo</th><th>Total Cost / Mo</th><th>Delta / Mo</th></tr>
                  </thead>
                  <tbody>
                    {equipmentDelta.rows.map((row) => (
                      <tr key={`${row.process}-${row.item}`}>
                        <td>{row.process}</td>
                        <td>{row.item}</td>
                        <td>{toMoney(row.templateCostPerMonth)}</td>
                        <td>{toMoney(row.totalCostPerMonth)}</td>
                        <td>{toMoney(row.deltaCostPerMonth)}</td>
                      </tr>
                    ))}
                    <tr className="summary-row"><td colSpan={2}>Total</td><td>{toMoney(equipmentDelta.totals.templateCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.totalCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.deltaCostPerMonth)}</td></tr>
                  </tbody>
                </table>
              </div>
            </SectionCard>

            <SectionCard title="Labor Estimation Snapshots" description="Imported labor-time estimation sheets are visible here for parity checks.">
              <div className="two-column-grid">
                <div className="data-table-wrapper compact">
                  <table className="data-table">
                    <thead><tr><th colSpan={4}>L10 Stations</th></tr><tr><th>Name</th><th>HC</th><th>UPH</th><th>Monthly Capa</th></tr></thead>
                    <tbody>
                      {project.laborTimeL10.stations.map((station) => (
                        <tr key={station.id}><td>{station.name}</td><td>{station.laborHc}</td><td>{station.uph ?? 0}</td><td>{station.monthlyCapa ?? 0}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="data-table-wrapper compact">
                  <table className="data-table">
                    <thead><tr><th colSpan={4}>L6 Stations</th></tr><tr><th>Name</th><th>HC</th><th>UPH</th><th>Cycle Time</th></tr></thead>
                    <tbody>
                      {project.laborTimeL6.stations.map((station) => (
                        <tr key={station.id}><td>{station.name}</td><td>{station.laborHc}</td><td>{station.uph ?? 0}</td><td>{station.cycleTimeSec ?? 0}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Equipment and Space" description="Baseline, extra equipment, and process space are explicit project assets.">
              <div className="three-column-grid">
                <div>
                  <div className="inline-actions"><h3>Baseline equipment</h3><button type="button" className="button ghost" onClick={() => updateProject((current) => ({ ...current, plant: { ...current.plant, equipmentList: [...current.plant.equipmentList, { id: `eq-${Date.now()}`, process: '', item: '', qty: 0, unitPrice: 0, depreciationYears: 1 }] } }))}>Add</button></div>
                  <div className="editable-list">
                    {project.plant.equipmentList.map((item) => (
                      <div className="list-row" key={item.id}>
                        <input value={item.item} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, equipmentList: current.plant.equipmentList.map((entry) => entry.id === item.id ? { ...entry, item: event.target.value } : entry) } }))} placeholder="Item" />
                        <input type="number" value={item.qty} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, equipmentList: current.plant.equipmentList.map((entry) => entry.id === item.id ? { ...entry, qty: numberValue(event.target.value) } : entry) } }))} placeholder="Qty" />
                        <input type="number" value={item.unitPrice} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, equipmentList: current.plant.equipmentList.map((entry) => entry.id === item.id ? { ...entry, unitPrice: numberValue(event.target.value) } : entry) } }))} placeholder="Unit price" />
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="inline-actions"><h3>Extra equipment</h3><button type="button" className="button ghost" onClick={() => updateProject((current) => ({ ...current, plant: { ...current.plant, extraEquipmentList: [...current.plant.extraEquipmentList, { id: `extra-${Date.now()}`, process: '', item: '', qty: 0, unitPrice: 0, depreciationYears: 1 }] } }))}>Add</button></div>
                  <div className="editable-list">
                    {project.plant.extraEquipmentList.map((item) => (
                      <div className="list-row" key={item.id}>
                        <input value={item.item} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, extraEquipmentList: current.plant.extraEquipmentList.map((entry) => entry.id === item.id ? { ...entry, item: event.target.value } : entry) } }))} placeholder="Item" />
                        <input type="number" value={item.qty} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, extraEquipmentList: current.plant.extraEquipmentList.map((entry) => entry.id === item.id ? { ...entry, qty: numberValue(event.target.value) } : entry) } }))} placeholder="Qty" />
                        <input type="number" value={item.unitPrice} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, extraEquipmentList: current.plant.extraEquipmentList.map((entry) => entry.id === item.id ? { ...entry, unitPrice: numberValue(event.target.value) } : entry) } }))} placeholder="Unit price" />
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="inline-actions"><h3>Space allocation</h3><button type="button" className="button ghost" onClick={() => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceAllocation: [...current.plant.spaceAllocation, { id: `space-${Date.now()}`, floor: '', process: '', areaSqft: 0, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft }] } }))}>Add</button></div>
                  <div className="editable-list">
                    {project.plant.spaceAllocation.map((row) => (
                      <div className="list-row" key={row.id}>
                        <input value={row.process} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceAllocation: current.plant.spaceAllocation.map((entry) => entry.id === row.id ? { ...entry, process: event.target.value } : entry) } }))} placeholder="Process" />
                        <input type="number" value={row.areaSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceAllocation: current.plant.spaceAllocation.map((entry) => entry.id === row.id ? { ...entry, areaSqft: numberValue(event.target.value) } : entry) } }))} placeholder="Area" />
                        <input type="number" value={row.ratePerSqft ?? 0} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceAllocation: current.plant.spaceAllocation.map((entry) => entry.id === row.id ? { ...entry, ratePerSqft: numberValue(event.target.value) } : entry) } }))} placeholder="Rate" />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'product' ? (
          <div className="stack-xl">
            <SectionCard title="L10 and L6 Product Setup" description="New product metadata is kept separate from plant assumptions.">
              <div className="two-column-grid">
                <div className="form-grid cols-2">
                  <h3>L10</h3>
                  <label><span>BU</span><input value={project.productL10.bu} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, bu: event.target.value } }))} /></label>
                  <label><span>Project Name</span><input value={project.productL10.projectName} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, projectName: event.target.value } }))} /></label>
                  <label><span>Customer</span><input value={project.productL10.customer} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, customer: event.target.value } }))} /></label>
                  <label><span>Model Name</span><input value={project.productL10.modelName} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, modelName: event.target.value } }))} /></label>
                  <label><span>Size mm</span><input value={project.productL10.sizeMm} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, sizeMm: event.target.value } }))} /></label>
                  <label><span>Handling Sec</span><input type="number" value={project.productL10.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { handlingSec: numberValue(event.target.value) }) } }))} /></label>
                  <label><span>Function Sec</span><input type="number" value={project.productL10.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { functionSec: numberValue(event.target.value) }) } }))} /></label>
                  <label><span>Yield</span><input type="number" step="0.01" value={project.productL10.yield ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, yield: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  <label><span>RFQ / Month</span><input type="number" value={project.productL10.rfqQtyPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, rfqQtyPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  <label><span>Shifts / Day</span><input type="number" value={project.productL10.shiftsPerDay ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, shiftsPerDay: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  <label><span>Hours / Shift</span><input type="number" value={project.productL10.hoursPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, hoursPerShift: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                </div>
                <div className="form-grid cols-2">
                  <h3>L6</h3>
                  <label><span>BU</span><input value={project.productL6.bu} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, bu: event.target.value } }))} /></label>
                  <label><span>PN</span><input value={project.productL6.pn} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pn: event.target.value } }))} /></label>
                  <label><span>SKU</span><input value={project.productL6.sku ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, sku: event.target.value } }))} /></label>
                  <label><span>Customer</span><input value={project.productL6.customer} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, customer: event.target.value } }))} /></label>
                  <label><span>Boards / Panel</span><input type="number" value={project.productL6.boardsPerPanel} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, boardsPerPanel: numberValue(event.target.value) } }))} /></label>
                  <label><span>PCB Size</span><input value={project.productL6.pcbSize} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pcbSize: event.target.value } }))} /></label>
                  <label><span>Side 1 Parts</span><input type="number" value={project.productL6.side1PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side1PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  <label><span>Side 2 Parts</span><input type="number" value={project.productL6.side2PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side2PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
                  <label><span>Handling Sec</span><input type="number" value={project.productL6.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, handlingSec: numberValue(event.target.value) } } }))} /></label>
                  <label><span>Function Sec</span><input type="number" value={project.productL6.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, functionSec: numberValue(event.target.value) } } }))} /></label>
                  <label className="full-span"><span>Station Path</span><input value={project.productL6.stationPath} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, stationPath: event.target.value } }))} /></label>
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Structured Setup Imports" description="Legacy MPM and labor-estimation sheets now map directly into typed project state.">
              <div className="action-grid">
                <label className="upload-card"><span>Import Month/Year Update CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importMonthYearUpdate(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L10 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10MpmSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L6 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6MpmSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L10 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10LaborEstimation(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import L6 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6LaborEstimation(event.target.files?.[0] ?? null)} /></label>
              </div>
            </SectionCard>

            <SectionCard title="Line Standard Library" description="Templates replace the legacy localStorage-only equipment helper with a typed model." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={saveCurrentAsLineStandard}>Save Current as Template</button><button type="button" className="button secondary" onClick={applySelectedLineStandard}>Apply Selected</button></div>}>
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
            </SectionCard>
          </div>
        ) : null}

        {activeTab === 'reports' ? (
          <div className="stack-xl">
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

            <SectionCard title="Imports and Exports" description="Structured file exchange replaces the legacy missing helper modules.">
              <div className="action-grid">
                <label className="upload-card"><span>Import Project JSON</span><input type="file" accept="application/json" onChange={(event) => void importJsonProject(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Month/Year Update CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importMonthYearUpdate(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Equipment CSV</span><input type="file" accept=".csv,text/csv" onChange={async (event) => {
                  const file = event.target.files?.[0] ?? null;
                  if (!file) return;
                  try {
                    assertFileSize(file);
                    const rows = parseEquipmentRows(await file.text());
                    updateProject((current) => ({ ...current, plant: { ...current.plant, equipmentList: rows } }));
                    setStatusMessage(`Imported equipment CSV: ${file.name}`);
                  } catch (error) {
                    const message = error instanceof Error ? error.message : 'Failed to import equipment CSV.';
                    setStatusMessage(message);
                    console.error(error);
                  }
                }} /></label>
                <label className="upload-card"><span>Import Equipment Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importEquipmentSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Space CSV</span><input type="file" accept=".csv,text/csv" onChange={async (event) => {
                  const file = event.target.files?.[0] ?? null;
                  if (!file) return;
                  try {
                    assertFileSize(file);
                    const rows = parseSpaceRows(await file.text());
                    updateProject((current) => ({ ...current, plant: { ...current.plant, spaceAllocation: rows } }));
                    setStatusMessage(`Imported space CSV: ${file.name}`);
                  } catch (error) {
                    const message = error instanceof Error ? error.message : 'Failed to import space CSV.';
                    setStatusMessage(message);
                    console.error(error);
                  }
                }} /></label>
                <label className="upload-card"><span>Import Space Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importSpaceSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Labor CSV</span><input type="file" accept=".csv,text/csv" onChange={async (event) => {
                  const file = event.target.files?.[0] ?? null;
                  if (!file) return;
                  try {
                    assertFileSize(file);
                    const parsed = parseLaborRows(await file.text());
                    updateProject((current) => ({ ...current, plant: { ...current.plant, directLaborRows: parsed.direct, indirectLaborRows: parsed.indirect } }));
                    setStatusMessage(`Imported labor CSV: ${file.name}`);
                  } catch (error) {
                    const message = error instanceof Error ? error.message : 'Failed to import labor CSV.';
                    setStatusMessage(message);
                    console.error(error);
                  }
                }} /></label>
                <label className="upload-card"><span>Import DLOH/IDL Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importDlohIdlSetup(event.target.files?.[0] ?? null)} /></label>
                <label className="upload-card"><span>Import Line Standards JSON</span><input type="file" accept="application/json" onChange={(event) => void importLineStandards(event.target.files?.[0] ?? null)} /></label>
                <button type="button" className="button secondary tall" onClick={exportL10Stations}>Export L10 Stations CSV</button>
                <button type="button" className="button secondary tall" onClick={exportLineStandards}>Export Line Standards JSON</button>
              </div>
            </SectionCard>

            <SectionCard title="Summary Preview" description="CSV preview for regression testing and audit verification.">
              <textarea data-testid="summary-preview" readOnly value={summaryCsv} rows={14} />
            </SectionCard>

            <SectionCard title="Sample CSV Formats" description="Minimal import formats documented directly in the app for engineers and QA.">
              <div className="two-column-grid">
                <textarea readOnly rows={10} value={toCsv([
                  ['id', 'process', 'item', 'qty', 'unitPrice', 'depreciationYears', 'costPerMonth'],
                  ['eq-1', 'SMT', 'Placement line', 1, 540000, 5, ''],
                ])} />
                <textarea readOnly rows={10} value={toCsv([
                  ['kind', 'id', 'name', 'process', 'department', 'role', 'headcount', 'allocationPercent', 'uphSource', 'overrideUph'],
                  ['direct', 'dl-1', 'Assembly', 'Assembly', '', '', 15, '', 'line', ''],
                  ['indirect', 'idl-1', 'TE', '', 'Engineering', 'TE', 8.36, 1, 'line', ''],
                ])} />
              </div>
            </SectionCard>
          </div>
        ) : null}
      </main>
    </div>
  );
}
