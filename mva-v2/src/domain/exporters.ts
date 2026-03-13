import { summarizeForCsv } from './calculations';
import { toCsv } from './csv';
import type { ProjectState } from './models';

export function buildProjectExport(project: ProjectState): string {
  return JSON.stringify(project, null, 2);
}

export function buildSummaryCsv(project: ProjectState): string {
  return toCsv(summarizeForCsv(project));
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
