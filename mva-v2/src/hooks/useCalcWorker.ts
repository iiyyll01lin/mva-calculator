import { useEffect, useRef, useState } from 'react';
import { calculateMva, calculateSimulation } from '../domain/calculations';
import type { MvaResults, ProjectState, SimulationResults } from '../domain/models';
import type { CalcWorkerOutput } from '../workers/calcWorker';

interface CalcResults {
  simulation: SimulationResults;
  mva: MvaResults;
}

interface UseCalcWorkerResult extends CalcResults {
  isComputing: boolean;
}

/**
 * Off-main-thread computation hook.
 *
 * Strategy:
 *  1. Initial state is computed synchronously in the useState initializer so
 *     the first render has real data immediately (no loading flash).
 *  2. After that, every project change is debounced (150 ms) and offloaded to
 *     a Web Worker, keeping the UI thread free for input events.
 *  3. In environments where Worker is unavailable (Vitest / jsdom, SSR) the
 *     hook falls back to synchronous computation transparently.
 *
 * The 150 ms debounce batches rapid keystroke edits so the worker is not
 * spammed with intermediate values.
 */
export function useCalcWorker(project: ProjectState): UseCalcWorkerResult {
  const [results, setResults] = useState<CalcResults>(() => {
    const simulation = calculateSimulation(project);
    return { simulation, mva: calculateMva(project, simulation) };
  });
  const [isComputing, setIsComputing] = useState(false);

  const workerRef = useRef<Worker | null>(null);
  // Monotonically increasing request counter — stale worker responses are discarded.
  const reqIdRef = useRef(0);
  // Skip the effect on the very first run: initial state is already correct.
  const isInitialRef = useRef(true);

  // Spin up the worker once on mount; clean up on unmount.
  useEffect(() => {
    if (typeof Worker === 'undefined') return; // jsdom / test env — no Worker support
    let worker: Worker;
    try {
      worker = new Worker(
        new URL('../workers/calcWorker.ts', import.meta.url),
        { type: 'module' },
      );
    } catch {
      // Some environments define Worker but fail to instantiate — stay synchronous.
      return;
    }
    worker.onmessage = (e: MessageEvent<CalcWorkerOutput>) => {
      if (e.data.id !== reqIdRef.current) return; // discard stale response
      setResults({ simulation: e.data.simulation, mva: e.data.mva });
      setIsComputing(false);
    };
    worker.onerror = () => {
      setIsComputing(false);
    };
    workerRef.current = worker;
    return () => {
      worker.terminate();
      workerRef.current = null;
    };
  }, []);

  // Recompute whenever the project changes.
  useEffect(() => {
    if (isInitialRef.current) {
      isInitialRef.current = false;
      return; // Initial result already set synchronously above.
    }

    if (!workerRef.current) {
      // Synchronous fallback: test env, Worker unavailable, or not yet initialised.
      const sim = calculateSimulation(project);
      setResults({ simulation: sim, mva: calculateMva(project, sim) });
      return;
    }

    setIsComputing(true);
    const id = ++reqIdRef.current;
    const timer = setTimeout(() => {
      workerRef.current?.postMessage({ id, project });
    }, 150); // 150 ms debounce batches rapid keystrokes into a single worker call
    return () => clearTimeout(timer);
  }, [project]);

  return { ...results, isComputing };
}
