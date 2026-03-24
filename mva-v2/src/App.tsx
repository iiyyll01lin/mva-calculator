import { startTransition, useEffect, useMemo, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SectionCard } from './components/SectionCard';
import { Sidebar } from './components/Sidebar';
import { SummaryPage } from './components/SummaryPage';
import { calculateMva, calculateSimulation, projectForProcess } from './domain/calculations';
import { buildL10StationsCsv, buildProjectExport, buildSummaryCsv, downloadText } from './domain/exporters';
import type { ProjectState, TabId } from './domain/models';
import { loadSelectedLineStandardId, saveSelectedLineStandardId } from './domain/persistence';
import { useProjectState } from './state/useProjectState';
import { BasicInfoPage } from './features/BasicInfoPage';
import { BomMapPage } from './features/BomMapPage';
import { DlohIdlPage } from './features/DlohIdlPage';
import { LaborTimeL10Page } from './features/LaborTimeL10Page';
import { LaborTimeL6Page } from './features/LaborTimeL6Page';
import { MachineRatesPage } from './features/MachineRatesPage';
import { ModelProcessPage } from './features/ModelProcessPage';
import { MpmL10Page } from './features/MpmL10Page';
import { MpmL6Page } from './features/MpmL6Page';
import { PlantEnvPage } from './features/PlantEnvPage';
import { PlantEquipmentPage } from './features/PlantEquipmentPage';
import { PlantRatesPage } from './features/PlantRatesPage';
import { PlantSpacePage } from './features/PlantSpacePage';
import { SimulationPage } from './features/SimulationPage';
import { useProjectImports } from './hooks/useProjectImports';
import { formatTimestamp } from './utils/formatters';

// Re-export domain helpers consumed by tests that previously imported from App
export { validateProjectPayload, validateLineStandardsPayload } from './domain/importers';
export { parseEquipmentRows, parseSpaceRows, parseLaborRows } from './domain/importers';

export default function App() {
  const { project, setProject } = useProjectState();
  const [activeTab, setActiveTab] = useState<TabId>('basic');
  const [selectedLineStandardId, setSelectedLineStandardId] = useState<string>(
    () => loadSelectedLineStandardId() || project.lineStandards[0]?.id || '',
  );
  const [statusMessage, setStatusMessage] = useState<string>('');

  const simulation = useMemo(() => calculateSimulation(project), [project]);
  const mva = useMemo(() => calculateMva(project, simulation), [project, simulation]);
  const l10Project = useMemo(() => projectForProcess(project, 'L10'), [project]);
  const l6Project = useMemo(() => projectForProcess(project, 'L6'), [project]);
  const l10Simulation = useMemo(() => calculateSimulation(l10Project), [l10Project]);
  const l6Simulation = useMemo(() => calculateSimulation(l6Project), [l6Project]);
  const l10Mva = useMemo(() => calculateMva(l10Project, l10Simulation), [l10Project, l10Simulation]);
  const l6Mva = useMemo(() => calculateMva(l6Project, l6Simulation), [l6Project, l6Simulation]);
  const l10SummaryCsv = useMemo(() => buildSummaryCsv(l10Project), [l10Project]);
  const l6SummaryCsv = useMemo(() => buildSummaryCsv(l6Project), [l6Project]);
  const summaryCsv = useMemo(() => buildSummaryCsv(project), [project]);

  const updateProject = (updater: (current: ProjectState) => ProjectState) => {
    startTransition(() => { setProject((current) => updater(current)); });
  };

  useEffect(() => { saveSelectedLineStandardId(selectedLineStandardId); }, [selectedLineStandardId]);
  useEffect(() => {
    if (!project.lineStandards.some((item) => item.id === selectedLineStandardId)) {
      setSelectedLineStandardId(project.lineStandards[0]?.id ?? '');
    }
  }, [project.lineStandards, selectedLineStandardId]);
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

  // ── Export helpers ──────────────────────────────────────────────────────────
  const exportProject = () =>
    downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_project.json`, buildProjectExport(project), 'application/json');
  const exportSummary = () =>
    downloadText(`${project.basicInfo.modelName}_${formatTimestamp()}_summary.csv`, summaryCsv, 'text/csv');
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
              <button type="button" className="button secondary" onClick={exportProject}>Export Project JSON</button>
              <button type="button" className="button primary" onClick={exportSummary}>Export Summary CSV</button>
            </div>
          </header>

          {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

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
        </main>
      </ErrorBoundary>
    </div>
  );
}
