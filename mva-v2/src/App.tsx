import { lazy, startTransition, Suspense, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SectionCard } from './components/SectionCard';
import { Sidebar } from './components/Sidebar';
import { SummaryPage } from './components/SummaryPage';
import { buildL10StationsCsv, buildProjectExport, buildSummaryCsv, downloadText } from './domain/exporters';
import type { ProjectState, TabId } from './domain/models';
import { loadSelectedLineStandardId, saveSelectedLineStandardId } from './domain/persistence';
import { usePortfolioState } from './state/useProjectState';
import { useProjectImports } from './hooks/useProjectImports';
import { useProcessSummary } from './hooks/useProcessSummary';
import { useCalcWorker } from './hooks/useCalcWorker';
import { formatTimestamp } from './utils/formatters';
import { defaultProject } from './domain/defaults';
import { LoginPage } from './features/LoginPage';

/** Classifies an import/export feedback string into a banner severity level. */
function classifyStatusType(msg: string): 'success' | 'error' | 'info' {
  if (/^(Failed|Invalid|Error|Cannot|Missing|not\s+found)/i.test(msg)) return 'error';
  if (/^(Imported|Applied|Saved)/i.test(msg)) return 'success';
  return 'info';
}

// Code-split each feature page — only the active tab is ever parsed/executed.
const ProjectDashboardPage = lazy(() => import('./features/ProjectDashboardPage').then((m) => ({ default: m.ProjectDashboardPage })));
const BasicInfoPage = lazy(() => import('./features/BasicInfoPage').then((m) => ({ default: m.BasicInfoPage })));
const BomMapPage = lazy(() => import('./features/BomMapPage').then((m) => ({ default: m.BomMapPage })));
const DlohIdlPage = lazy(() => import('./features/DlohIdlPage').then((m) => ({ default: m.DlohIdlPage })));
const LaborTimeL10Page = lazy(() => import('./features/LaborTimeL10Page').then((m) => ({ default: m.LaborTimeL10Page })));
const LaborTimeL6Page = lazy(() => import('./features/LaborTimeL6Page').then((m) => ({ default: m.LaborTimeL6Page })));
const MachineRatesPage = lazy(() => import('./features/MachineRatesPage').then((m) => ({ default: m.MachineRatesPage })));
const ModelProcessPage = lazy(() => import('./features/ModelProcessPage').then((m) => ({ default: m.ModelProcessPage })));
const MpmL10Page = lazy(() => import('./features/MpmL10Page').then((m) => ({ default: m.MpmL10Page })));
const MpmL6Page = lazy(() => import('./features/MpmL6Page').then((m) => ({ default: m.MpmL6Page })));
const PlantEnvPage = lazy(() => import('./features/PlantEnvPage').then((m) => ({ default: m.PlantEnvPage })));
const PlantEquipmentPage = lazy(() => import('./features/PlantEquipmentPage').then((m) => ({ default: m.PlantEquipmentPage })));
const PlantRatesPage = lazy(() => import('./features/PlantRatesPage').then((m) => ({ default: m.PlantRatesPage })));
const PlantSpacePage = lazy(() => import('./features/PlantSpacePage').then((m) => ({ default: m.PlantSpacePage })));
const SimulationPage = lazy(() => import('./features/SimulationPage').then((m) => ({ default: m.SimulationPage })));

// Re-export domain helpers consumed by tests that previously imported from App
export { validateProjectPayload, validateLineStandardsPayload } from './domain/importers';
export { parseEquipmentRows, parseSpaceRows, parseLaborRows } from './domain/importers';

function AppOrchestrator() {
  const {
    portfolio,
    project,
    setProject,
    activeProjectId,
    createProject,
    deleteProject,
    openProject,
    goToDashboard,
  } = usePortfolioState();
  const [activeTab, setActiveTab] = useState<TabId>('basic');

  // When project is null (dashboard view), fall back to defaultProject so that
  // all hooks below are called with a valid ProjectState on every render pass.
  // Values computed from safeProject are only used in the orchestrator branch
  // (rendered after the null-guard early return).
  const safeProject = project ?? defaultProject;

  const [selectedLineStandardId, setSelectedLineStandardId] = useState<string>(
    () => loadSelectedLineStandardId() || safeProject.lineStandards[0]?.id || '',
  );
  const [statusMessage, setStatusMessage] = useState<string>('');

  const { simulation, mva, isComputing } = useCalcWorker(safeProject);
  // L10 and L6 process-scoped derivations are deferred so they never block
  // the main thread during rapid data entry — their values are consumed only
  // by their respective summary / MPM tabs and tolerate a render-cycle lag.
  const deferredProject = useDeferredValue(safeProject);
  const { mva: l10Mva, simulation: l10Simulation, summaryCsv: l10SummaryCsv } = useProcessSummary(deferredProject, 'L10');
  const { mva: l6Mva, simulation: l6Simulation, summaryCsv: l6SummaryCsv } = useProcessSummary(deferredProject, 'L6');
  const summaryCsv = useMemo(() => buildSummaryCsv(deferredProject), [deferredProject]);

  // Stable reference — feature pages wrapped in memo won't re-render just because
  // App re-renders for unrelated state changes (e.g. activeTab, statusMessage).
  const updateProject = useCallback((updater: (current: ProjectState) => ProjectState) => {
    startTransition(() => { setProject((current) => updater(current)); });
  }, [setProject]);

  // Dirty-state: true whenever the project has changed since the last export.
  const [isDirty, setIsDirty] = useState(false);
  const isFirstProjectMount = useRef(true);
  useEffect(() => {
    if (isFirstProjectMount.current) { isFirstProjectMount.current = false; return; }
    setIsDirty(true);
  }, [safeProject]);

  useEffect(() => { saveSelectedLineStandardId(selectedLineStandardId); }, [selectedLineStandardId]);
  useEffect(() => {
    if (!safeProject.lineStandards.some((item) => item.id === selectedLineStandardId)) {
      setSelectedLineStandardId(safeProject.lineStandards[0]?.id ?? '');
    }
  }, [safeProject.lineStandards, selectedLineStandardId]);
  useEffect(() => {
    if (!statusMessage) return;
    const timer = setTimeout(() => setStatusMessage(''), 6000);
    return () => clearTimeout(timer);
  }, [statusMessage]);

  const {
    importJsonProject,
    importLineStandards,
    importMonthYearUpdate,
    importEquipmentSetup,
    importSpaceSetup,
    importDlohIdlSetup,
  } = useProjectImports({ updateProject, setSelectedLineStandardId, setStatusMessage });

  // ── Portfolio dashboard (no active project) ─────────────────────────────────
  // Render the project dashboard when no active project is selected.
  if (activeProjectId === null || project === null) {
    return (
      <Suspense fallback={<div className="page-loading" aria-busy="true"><span className="page-loading-spinner" />Loading…</div>}>
        <ProjectDashboardPage
          portfolio={portfolio}
          onOpen={openProject}
          onCreateNew={createProject}
          onDelete={deleteProject}
        />
      </Suspense>
    );
  }

  // ── Export helpers ──────────────────────────────────────────────────────────
  const markClean = () => setIsDirty(false);
  const exportProject = () => {
    downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_project.json`, buildProjectExport(project), 'application/json');
    markClean();
  };
  const exportSummary = () => {
    downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_summary.csv`, summaryCsv, 'text/csv');
    markClean();
  };
  const exportL10Stations = () =>
    downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_l10-stations.csv`, buildL10StationsCsv(project), 'text/csv');
  const exportLineStandards = () =>
    downloadText(`line-standards_${formatTimestamp()}.json`, JSON.stringify(project.lineStandards, null, 2), 'application/json');

  // ── Imports/exports slot rendered into SummaryPage ──────────────────────────
  const renderImportsExports = (summaryText: string, processLabel: 'L10' | 'L6') => (
    <SectionCard
      title="Imports and Exports"
      description={`Structured exchange for ${processLabel} summary verification and release handoff.`}
    >
      <div className="action-grid">
        <label className="upload-card">
          <span>Import Project JSON</span>
          <input type="file" accept="application/json" onChange={(e) => void importJsonProject(e.target.files?.[0] ?? null)} />
        </label>
        <label className="upload-card">
          <span>Import Month/Year Update CSV</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => void importMonthYearUpdate(e.target.files?.[0] ?? null)} />
        </label>
        <label className="upload-card">
          <span>Import Equipment Setup CSV</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => void importEquipmentSetup(e.target.files?.[0] ?? null)} />
        </label>
        <label className="upload-card">
          <span>Import Space Setup CSV</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => void importSpaceSetup(e.target.files?.[0] ?? null)} />
        </label>
        <label className="upload-card">
          <span>Import DLOH/IDL Setup CSV</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => void importDlohIdlSetup(e.target.files?.[0] ?? null)} />
        </label>
        <label className="upload-card">
          <span>Import Line Standards JSON</span>
          <input type="file" accept="application/json" onChange={(e) => void importLineStandards(e.target.files?.[0] ?? null)} />
        </label>
        <button type="button" className="button secondary tall"
          onClick={() => downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_${processLabel.toLowerCase()}_summary.csv`, summaryText, 'text/csv')}>
          Export {processLabel} Summary CSV
        </button>
        <button type="button" className="button secondary tall" onClick={exportProject}>Export Project JSON</button>
        <button type="button" className="button secondary tall" onClick={exportL10Stations}>Export L10 Stations CSV</button>
        <button type="button" className="button secondary tall" onClick={exportLineStandards}>Export Line Standards JSON</button>
      </div>
    </SectionCard>
  );

  return (
    <div className="app-shell">
      <Sidebar activeTab={activeTab} onSelect={setActiveTab} />
      <ErrorBoundary>
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
              <button type="button" className="button secondary" onClick={goToDashboard} title="Return to the project portfolio">← Portfolio</button>
              <button type="button" className="button secondary" onClick={exportProject}>Export Project JSON</button>
              <button type="button" className="button primary" onClick={exportSummary}>Export Summary CSV</button>
              {isDirty && <span className="dirty-badge" title="You have unsaved changes. Export Project JSON to persist them.">● Unsaved Changes</span>}
            </div>
          </header>

          {(isComputing || deferredProject !== safeProject) && (
            <div className="status-banner info computing-banner" role="status" aria-live="polite">
              <span className="page-loading-spinner" aria-hidden="true" />
              Computing…
            </div>
          )}

          {statusMessage && (
            <div className={`status-banner ${classifyStatusType(statusMessage)}`} role="status" aria-live="polite">
              <span aria-hidden="true">{classifyStatusType(statusMessage) === 'success' ? '✓' : classifyStatusType(statusMessage) === 'error' ? '✗' : 'ℹ'}</span>
              {statusMessage}
            </div>
          )}

          <Suspense fallback={<div className="page-loading" aria-busy="true"><span className="page-loading-spinner" />Loading…</div>}>
          {activeTab === 'basic' && <BasicInfoPage project={project} updateProject={updateProject} />}
          {activeTab === 'machine_rates' && <MachineRatesPage project={project} updateProject={updateProject} />}
          {activeTab === 'bom_map' && <BomMapPage project={project} updateProject={updateProject} />}
          {activeTab === 'model_process' && <ModelProcessPage project={project} updateProject={updateProject} />}
          {activeTab === 'simulation_results' && <SimulationPage demandWeekly={project.basicInfo.demandWeekly} simulation={simulation} />}
          {activeTab === 'mva_plant_rates' && <PlantRatesPage project={project} updateProject={updateProject} mva={mva} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mva_plant_env' && <PlantEnvPage project={project} updateProject={updateProject} mva={mva} />}
          {activeTab === 'mva_plant_equipment' && (
            <PlantEquipmentPage
              project={project}
              updateProject={updateProject}
              selectedLineStandardId={selectedLineStandardId}
              onUpdateLineStandardSelection={setSelectedLineStandardId}
              onStatusMessage={setStatusMessage}
            />
          )}
          {activeTab === 'mva_plant_space' && <PlantSpacePage project={project} updateProject={updateProject} mva={mva} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mva_plant_dloh_idl' && <DlohIdlPage project={project} updateProject={updateProject} mva={mva} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mpm_l10' && (
            <MpmL10Page project={project} updateProject={updateProject} l10Mva={l10Mva} l10Simulation={l10Simulation} onStatusMessage={setStatusMessage} />
          )}
          {activeTab === 'mpm_l6' && <MpmL6Page project={project} updateProject={updateProject} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mva_labor_l10' && <LaborTimeL10Page project={project} updateProject={updateProject} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mva_labor_l6' && <LaborTimeL6Page project={project} updateProject={updateProject} onStatusMessage={setStatusMessage} />}
          {activeTab === 'mva_summary_l10' && (
            <SummaryPage
              processLabel="L10"
              project={project}
              summaryMva={l10Mva}
              summarySimulation={l10Simulation}
              summaryText={l10SummaryCsv}
              updateProject={updateProject}
              importsExportsSlot={renderImportsExports(l10SummaryCsv, 'L10')}
            />
          )}
          {activeTab === 'mva_summary_l6' && (
            <SummaryPage
              processLabel="L6"
              project={project}
              summaryMva={l6Mva}
              summarySimulation={l6Simulation}
              summaryText={l6SummaryCsv}
              updateProject={updateProject}
              importsExportsSlot={renderImportsExports(l6SummaryCsv, 'L6')}
            />
          )}
          </Suspense>
        </main>
      </ErrorBoundary>
    </div>
  );
}

/** Auth-guarded entry point rendered by main.tsx and used directly by tests. */
export default function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => sessionStorage.getItem('mva_auth') === '1',
  );

  if (!isAuthenticated) {
    return <LoginPage onLogin={() => setIsAuthenticated(true)} />;
  }

  return <AppOrchestrator />;
}
