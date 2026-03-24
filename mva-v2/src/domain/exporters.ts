import * as XLSX from 'xlsx';
import { buildL10SummaryStationCostTable, calculateMva, calculateSimulation, summarizeForCsv } from './calculations';
import { toCsv } from './csv';
import type { MvaResults, ProjectState, SimulationResults } from './models';

export function buildProjectExport(project: ProjectState): string {
  return JSON.stringify(project, null, 2);
}

export function buildLineStandardsExport(project: ProjectState): string {
  return JSON.stringify(project.lineStandards, null, 2);
}

export function buildSummaryCsv(project: ProjectState): string {
  return toCsv(summarizeForCsv(project));
}

export function buildL10StationsCsv(project: ProjectState): string {
  const simulation = calculateSimulation(project);
  const mva = calculateMva(project, simulation);
  const rows = buildL10SummaryStationCostTable({
    directLaborBreakdown: mva.dlBreakdown,
    equipmentDepPerUnit: mva.overhead.equipmentDepPerUnit,
    equipmentMaintPerUnit: mva.overhead.equipmentMaintPerUnit,
  });

  return toCsv([
    ['Station Name', 'Machine Cost', 'Labor Cost', 'Total Cost'],
    ...rows.map((row) => [row.stationName, row.machineCost, row.laborCost, row.totalCost]),
  ]);
}

// ─── XLSX export ────────────────────────────────────────────────────────────

type XlsxRow = (string | number | null)[];

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(value);
}

function pct(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

/**
 * Build the structured row array for the MVA Summary Excel worksheet.
 * This replicates the legacy Excel template column/row layout exactly:
 *   Row 1:   Header (model name, process label, date)
 *   Row 2:   Blank
 *   Rows 3-6: Confirm section
 *   Row 7:   Blank
 *   Rows 8-15: Capacity Assumptions
 *   Row 16:  Blank
 *   Row 17:  A. LABOR Cost header
 *   Rows 18+: DL rows → Total DL → IDL header → IDL rows → Total IDL
 *   …       : D. Overhead, E. SGA, MVA Total
 */
export function buildMvaSummaryXlsxRows(
  processLabel: 'L10' | 'L6',
  project: ProjectState,
  mva: MvaResults,
  simulation: SimulationResults,
): XlsxRow[] {
  const now = new Date().toISOString().slice(0, 10);
  const { confirmation, basicInfo, plant, productL6 } = project;

  const rows: XlsxRow[] = [];

  // ── Header ──────────────────────────────────────────────────────────────
  rows.push([`MVA Summary — ${processLabel}`, basicInfo.modelName, '', `Generated: ${now}`]);
  rows.push([]);

  // ── Confirm ─────────────────────────────────────────────────────────────
  rows.push(['Section', 'Item', 'Value', 'Note']);
  rows.push(['Confirm', 'Decision', confirmation.decision ?? 'Unreviewed', '']);
  rows.push(['Confirm', 'Reviewer', confirmation.reviewer || '', '']);
  rows.push(['Confirm', 'Comment', confirmation.comment || '', '']);
  rows.push(['Confirm', 'Decided At', confirmation.decidedAt ?? '', '']);
  rows.push([]);

  // ── Capacity Assumptions ─────────────────────────────────────────────────
  rows.push(['Capacity Assumptions', 'Item', 'Value', 'Unit']);
  rows.push(['Capacity', 'Monthly FCST', mva.monthlyVolume, 'units']);
  rows.push(['Capacity', 'Days / Month', plant.mvaVolume.workDaysPerMonth, 'days']);
  rows.push(['Capacity', 'Shifts / Day', basicInfo.shiftsPerDay, 'shifts']);
  rows.push(['Capacity', 'Hours / Shift', basicInfo.hoursPerShift, 'hrs']);
  rows.push(['Capacity', 'Line UPH Raw', mva.lineUphRaw, 'units/hr']);
  rows.push(['Capacity', 'Line UPH Used', mva.lineUphUsedForCapacity, 'units/hr']);
  if (processLabel === 'L6') {
    rows.push(['Capacity', 'Boards / Panel', productL6.boardsPerPanel, 'pcs']);
    rows.push(['Capacity', 'PCB Size', productL6.pcbSize || '', '']);
    rows.push(['Capacity', 'Parts (S1)', productL6.side1PartsCount ?? 0, 'pcs']);
    rows.push(['Capacity', 'Parts (S2)', productL6.side2PartsCount ?? 0, 'pcs']);
  }
  rows.push([]);

  // ── A. LABOR Cost ─────────────────────────────────────────────────────────
  rows.push(['A. LABOR Cost', '', '', '']);
  rows.push([
    'Rate',
    `DL: ${money(plant.mvaRates.directLaborHourlyRate)}/hr`,
    `IDL: ${money(plant.mvaRates.indirectLaborHourlyRate)}/hr`,
    `Efficiency: ${pct(plant.mvaRates.efficiency)}`,
  ]);
  rows.push(['Direct Labor', 'Name', 'HC', 'UPH Used', 'Cost / Unit']);
  for (const row of mva.directLabor) {
    rows.push(['DL', row.name, row.effectiveHeadcount, row.uphUsed, row.costPerUnit]);
  }
  const totalDlHc = mva.directLabor.reduce((sum, r) => sum + r.effectiveHeadcount, 0);
  const totalDlCost = mva.directLabor.reduce((sum, r) => sum + r.costPerUnit, 0);
  rows.push(['DL Total', '', totalDlHc, '', totalDlCost]);
  rows.push([]);
  rows.push(['Indirect Labor', 'Name', 'HC', 'UPH Used', 'Cost / Unit']);
  for (const row of mva.indirectLabor) {
    rows.push(['IDL', row.name, row.effectiveHeadcount, row.uphUsed, row.costPerUnit]);
  }
  const totalIdlHc = mva.indirectLabor.reduce((sum, r) => sum + r.effectiveHeadcount, 0);
  const totalIdlCost = mva.indirectLabor.reduce((sum, r) => sum + r.costPerUnit, 0);
  rows.push(['IDL Total', '', totalIdlHc, '', totalIdlCost]);
  rows.push([]);

  // ── D. Overhead ───────────────────────────────────────────────────────────
  const overheadTotal =
    mva.overhead.equipmentDepPerUnit +
    mva.overhead.equipmentMaintPerUnit +
    mva.overhead.spacePerUnit +
    mva.overhead.powerPerUnit;
  rows.push(['D. Overhead', 'Item', 'Cost / Unit', '']);
  rows.push(['Overhead', 'Equip Depreciation', mva.overhead.equipmentDepPerUnit, '']);
  rows.push(['Overhead', 'Equip Maintenance', mva.overhead.equipmentMaintPerUnit, '']);
  rows.push(['Overhead', 'Space / Utility', mva.overhead.spacePerUnit, '']);
  rows.push(['Overhead', 'Power', mva.overhead.powerPerUnit, '']);
  rows.push(['Overhead', 'Total Overhead', overheadTotal, '']);
  rows.push([]);

  // ── E. SG&A ───────────────────────────────────────────────────────────────
  rows.push(['E. SG&A', 'Item', 'Cost / Unit', '']);
  rows.push(['SGA', 'Corporate Burden', mva.sga.corporateBurdenPerUnit, '']);
  rows.push(['SGA', 'Site Maintenance', mva.sga.siteMaintenancePerUnit, '']);
  rows.push(['SGA', 'IT Security', mva.sga.itSecurityPerUnit, '']);
  rows.push(['SGA', 'Total SGA', mva.sga.totalSgaPerUnit, '']);
  rows.push([]);

  // ── MVA Total ─────────────────────────────────────────────────────────────
  rows.push(['MVA Total Cost / Unit', '', mva.totalPerUnit, '']);
  rows.push([]);

  // ── Simulation KPIs ───────────────────────────────────────────────────────
  rows.push(['Simulation', 'UPH', simulation.uph, 'units/hr']);
  rows.push(['Simulation', 'Takt Time', simulation.taktTime, 'sec']);
  rows.push(['Simulation', 'Weekly Output', simulation.weeklyOutput, 'units']);
  rows.push(['Simulation', 'Bottleneck', simulation.bottleneckProcess, '']);

  return rows;
}

export function buildMvaXlsx(
  processLabel: 'L10' | 'L6',
  project: ProjectState,
  mva: MvaResults,
  simulation: SimulationResults,
): Uint8Array {
  const wb = XLSX.utils.book_new();

  // Primary summary sheet
  const summaryRows = buildMvaSummaryXlsxRows(processLabel, project, mva, simulation);
  const wsSummary = XLSX.utils.aoa_to_sheet(summaryRows);

  // Set column widths
  wsSummary['!cols'] = [
    { wch: 22 }, // A – Section
    { wch: 28 }, // B – Item
    { wch: 20 }, // C – Value
    { wch: 22 }, // D – Note / Unit
    { wch: 16 }, // E – extra (labor cost col)
  ];

  XLSX.utils.book_append_sheet(wb, wsSummary, `Summary ${processLabel}`);

  // Cost lines summary sheet
  const costSheet = XLSX.utils.aoa_to_sheet([
    ['Cost Category', 'Value / Unit'],
    ...mva.costLines.map((line) => [line.label, line.value]),
    ['Total / Unit', mva.totalPerUnit],
  ]);
  costSheet['!cols'] = [{ wch: 30 }, { wch: 16 }];
  XLSX.utils.book_append_sheet(wb, costSheet, 'Cost Rollup');

  // Simulation steps sheet
  const simSheet = XLSX.utils.aoa_to_sheet([
    ['Step', 'Process', 'Side', 'Raw CT (s)', 'Final CT (s)', 'Utilization %', 'Bottleneck'],
    ...simulation.steps.map((step) => [
      step.step,
      step.process,
      step.side,
      step.rawCycleTime,
      step.cycleTime,
      Number(step.utilization.toFixed(2)),
      step.isBottleneck ? 'YES' : '',
    ]),
  ]);
  simSheet['!cols'] = [{ wch: 6 }, { wch: 20 }, { wch: 10 }, { wch: 12 }, { wch: 12 }, { wch: 14 }, { wch: 10 }];
  XLSX.utils.book_append_sheet(wb, simSheet, 'Simulation');

  return new Uint8Array(XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as number[]);
}

// ─── Download helpers ────────────────────────────────────────────────────────

export function downloadText(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function downloadBytes(filename: string, bytes: Uint8Array, mimeType: string): void {
  const blob = new Blob([bytes], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
