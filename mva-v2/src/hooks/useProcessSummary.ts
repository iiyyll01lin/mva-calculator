import { useMemo } from 'react';
import { calculateMva, calculateSimulation, projectForProcess } from '../domain/calculations';
import { buildSummaryCsv } from '../domain/exporters';
import type { MvaResults, ProjectState, SimulationResults } from '../domain/models';

interface ProcessSummaryResult {
  processProject: ProjectState;
  simulation: SimulationResults;
  mva: MvaResults;
  summaryCsv: string;
}

/**
 * Derives a process-scoped project snapshot and computes its full MVA summary.
 *
 * Encapsulates the three-step derivation pipeline:
 *   `projectForProcess` → `calculateSimulation` → `calculateMva` → `buildSummaryCsv`
 *
 * Using this hook in `App.tsx` avoids exposing intermediate `l10Project` /
 * `l6Project` variables as explicit local state, keeping the root component's
 * hook list lean and communicating intent clearly.
 *
 * Each memo inside this hook reacts only to its own upstream dependency, so a
 * change that only affects the simulation (e.g. machine rates) does not
 * invalidate the project snapshot memo — React's reference equality handles
 * the chain correctly.
 *
 * @param project - The full project state from `useProjectState`
 * @param processType - Process filter to scope the summary ('L10' or 'L6')
 * @returns Stable memoized `{ processProject, simulation, mva, summaryCsv }`
 */
export function useProcessSummary(
  project: ProjectState,
  processType: 'L10' | 'L6',
): ProcessSummaryResult {
  const processProject = useMemo(
    () => projectForProcess(project, processType),
    [project, processType],
  );
  const simulation = useMemo(() => calculateSimulation(processProject), [processProject]);
  const mva = useMemo(() => calculateMva(processProject, simulation), [processProject, simulation]);
  const summaryCsv = useMemo(() => buildSummaryCsv(processProject), [processProject]);
  return { processProject, simulation, mva, summaryCsv };
}
