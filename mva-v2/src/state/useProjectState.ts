import { useCallback, useEffect, useState } from 'react';
import { DEFAULT_PROJECT_ID, defaultProject } from '../domain/defaults';
import {
  generateProjectId,
  loadActiveProjectId,
  loadLineStandards,
  loadPortfolio,
  saveActiveProjectId,
  saveLineStandards,
  savePortfolio,
} from '../domain/persistence';
import type { ProductLine, ProjectState, SkuType } from '../domain/models';

// ── Portfolio state hook ────────────────────────────────────────────────────────

/**
 * Full portfolio state hook — manages the project list and the active project.
 *
 * Initialization logic (both SSR-safe and test-safe):
 *  - `loadPortfolio()` bootstraps from localStorage, migrates legacy single-project
 *    data if found, or returns `[defaultProject]` on a cold start.
 *  - `activeProjectId` defaults to the persisted ID, or the first project's ID if
 *    nothing is saved, so the app renders the orchestrator immediately (no dashboard)
 *    on first install — preserving pre-Phase-8 UX.
 */
export function usePortfolioState() {
  const [portfolio, setPortfolioRaw] = useState<ProjectState[]>(() => {
    if (typeof window === 'undefined') return [defaultProject];
    return loadPortfolio();
  });

  const [activeProjectId, setActiveProjectIdRaw] = useState<string | null>(() => {
    if (typeof window === 'undefined') return DEFAULT_PROJECT_ID;
    const savedId = loadActiveProjectId();
    if (savedId) return savedId;
    // No persisted ID → auto-select the first project so the orchestrator renders
    const initial = loadPortfolio();
    return initial[0]?.projectId ?? null;
  });

  const project = portfolio.find((p) => p.projectId === activeProjectId) ?? null;

  // Persist portfolio whenever it changes (including line standards for compat)
  useEffect(() => {
    savePortfolio(portfolio);
    if (project) saveLineStandards(project.lineStandards);
  }, [portfolio, project]);

  // Persist active project ID whenever it changes
  useEffect(() => {
    saveActiveProjectId(activeProjectId);
  }, [activeProjectId]);

  /** Update the currently-active project via an updater function. */
  const setProject = useCallback(
    (updater: (current: ProjectState) => ProjectState) => {
      setPortfolioRaw((prev) =>
        prev.map((p) =>
          p.projectId === activeProjectId
            ? { ...updater(p), lastModified: new Date().toISOString() }
            : p,
        ),
      );
    },
    [activeProjectId],
  );

  /** Create a new project and immediately open it. */
  const createProject = useCallback(
    (meta: { modelName: string; productLine: ProductLine; sku: SkuType | string }) => {
      const newId = generateProjectId();
      // Inherit line standards from the first portfolio project for continuity
      const inheritedStandards = portfolio[0]?.lineStandards ?? defaultProject.lineStandards;
      const newProject: ProjectState = {
        ...defaultProject,
        projectId: newId,
        productLine: meta.productLine,
        sku: meta.sku,
        lastModified: new Date().toISOString(),
        basicInfo: { ...defaultProject.basicInfo, modelName: meta.modelName },
        lineStandards: inheritedStandards,
      };
      setPortfolioRaw((prev) => [...prev, newProject]);
      setActiveProjectIdRaw(newId);
    },
    [portfolio],
  );

  /** Remove a project from the portfolio. If it is active, switch to the next. */
  const deleteProject = useCallback(
    (projectId: string) => {
      setPortfolioRaw((prev) => {
        const updated = prev.filter((p) => p.projectId !== projectId);
        if (activeProjectId === projectId) {
          setActiveProjectIdRaw(updated[0]?.projectId ?? null);
        }
        return updated;
      });
    },
    [activeProjectId],
  );

  /** Switch the active project. */
  const openProject = useCallback((projectId: string) => {
    setActiveProjectIdRaw(projectId);
  }, []);

  /** Navigate back to the portfolio dashboard (sets active project to null). */
  const goToDashboard = useCallback(() => {
    setActiveProjectIdRaw(null);
  }, []);

  return {
    portfolio,
    activeProjectId,
    project,
    setProject,
    createProject,
    deleteProject,
    openProject,
    goToDashboard,
  };
}

// ── Backward-compatible single-project hook ─────────────────────────────────────

/**
 * Thin backward-compatible wrapper around `usePortfolioState`.
 * Pre-Phase-8 code that imported `useProjectState` continues to work unchanged.
 * Line standards are loaded from the legacy separate key and merged in, matching
 * the original two-key storage behaviour.
 */
export function useProjectState() {
  const { project, setProject } = usePortfolioState();
  const [resolvedProject, setResolvedProject] = useState<ProjectState>(() => {
    const base = project ?? defaultProject;
    const standards = typeof window === 'undefined' ? base.lineStandards : loadLineStandards();
    return { ...base, lineStandards: standards };
  });

  // Keep resolvedProject in sync with portfolio changes
  useEffect(() => {
    const base = project ?? defaultProject;
    setResolvedProject((prev) => {
      // Only replace lineStandards if they have not been overridden by portfolio yet
      const standards = prev.lineStandards.length > 0 ? prev.lineStandards : base.lineStandards;
      return { ...base, lineStandards: standards };
    });
  }, [project]);

  return { project: resolvedProject, setProject };
}
