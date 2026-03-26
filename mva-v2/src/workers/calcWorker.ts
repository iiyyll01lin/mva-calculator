/// <reference lib="webworker" />
/**
 * Off-main-thread calculation worker.
 *
 * Receives a full ProjectState via postMessage, runs the CPU-bound
 * calculateSimulation + calculateMva pipeline, and posts results back.
 * This file has zero DOM dependencies — it only imports pure-function
 * domain modules that are safe in any Worker context.
 */
import { calculateMva, calculateSimulation } from '../domain/calculations';
import type { MvaResults, ProjectState, SimulationResults } from '../domain/models';

export interface CalcWorkerInput {
  id: number;
  project: ProjectState;
}

export interface CalcWorkerOutput {
  id: number;
  simulation: SimulationResults;
  mva: MvaResults;
}

self.onmessage = (event: MessageEvent<CalcWorkerInput>) => {
  const { id, project } = event.data;
  const simulation = calculateSimulation(project);
  const mva = calculateMva(project, simulation);
  (self as unknown as Worker).postMessage({ id, simulation, mva } satisfies CalcWorkerOutput);
};
