import { buildL10SummaryStationCostTable, calculateMva, calculateSimulation, summarizeForCsv } from './calculations';
import { toCsv } from './csv';
import type { ProjectState } from './models';

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
