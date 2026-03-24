import { startTransition, useEffect, useMemo, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SectionCard } from './components/SectionCard';
import { Sidebar } from './components/Sidebar';
import { SummaryPage } from './components/SummaryPage';
import { calculateMva, calculateSimulation, projectForProcess, resolveIndirectLaborRows } from './domain/calculations';
import { defaultProject } from './domain/defaults';
import { buildL10StationsCsv, buildProjectExport, buildSummaryCsv, downloadText } from './domain/exporters';
import {
  parseDlohIdlSetupCsv,
  parseEquipmentListSetupCsv,
  parseMonthYearUpdateCsv,
  parseSpaceSetupCsv,
  validateLineStandardsPayload,
  validateProjectPayload,
} from './domain/importers';
import type { ProjectState, TabId } from './domain/models';
import { loadSelectedLineStandardId, saveSelectedLineStandardId } from './domain/persistence';
import { useProjectState } from './state/useProjectState';
import { BasicInfoPage } from './features/BasicInfoPage';
import { DlohIdlPage } from './features/DlohIdlPage';
import { LaborTimeL10Page } from './features/LaborTimeL10Page';
import { LaborTimeL6Page } from './features/LaborTimeL6Page';
import { MpmL10Page } from './features/MpmL10Page';
import { MpmL6Page } from './features/MpmL6Page';
import { PlantEnvPage } from './features/PlantEnvPage';
import { PlantEquipmentPage } from './features/PlantEquipmentPage';
import { PlantRatesPage } from './features/PlantRatesPage';
import { PlantSpacePage } from './features/PlantSpacePage';
import { SimulationPage } from './features/SimulationPage';
import { assertFileSize, readJsonRecord } from './utils/fileImport';
import { formatTimestamp, numberValue } from './utils/formatters';

// Re-export for tests that previously imported from App
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

  // ── Export helpers ──────────────────────────────────────────────────────────
  const exportProject = () =>
    downloadText(
      `${project.basicInfo.modelName}_${formatTimestamp()}_project.json`,
      buildProjectExport(project),
      'application/json',
    );
  const exportSummary = () =>
    downloadText(
      `${project.basicInfo.modelName}_${formatTimestamp()}_summary.csv`,
      summaryCsv,
      'text/csv',
    );
  const exportL10Stations = () =>
    downloadText(
      `${project.basicInfo.modelName}_${formatTimestamp()}_l10-stations.csv`,
      buildL10StationsCsv(project),
      'text/csv',
    );
  const exportLineStandards = () =>
    downloadText(
      `line-standards_${formatTimestamp()}.json`,
      JSON.stringify(project.lineStandards, null, 2),
      'application/json',
    );

  // ── Shared import handlers (used by renderImportsExports / SummaryPage slot) ─
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
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import project JSON.');
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
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import line standards JSON.');
      console.error(error);
    }
  };

  const importMonthYearUpdateForSummary = async (file: File | null) => {
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
              equipmentAverageUsefulLifeYears:
                parsed.equipmentAverageUsefulLifeYears ??
                current.plant.overheadByProcess[current.plant.processType].equipmentAverageUsefulLifeYears,
              equipmentMaintenanceRatio:
                parsed.equipmentMaintenanceRatio ??
                current.plant.overheadByProcess[current.plant.processType].equipmentMaintenanceRatio,
              powerCostPerUnit:
                parsed.powerCostPerUnit ??
                current.plant.overheadByProcess[current.plant.processType].powerCostPerUnit,
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
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import month/year update CSV.');
      console.error(error);
    }
  };

  const importEquipmentSetupForSummary = async (file: File | null) => {
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
              equipmentDepreciationPerMonth:
                parsed.equipmentCostPerMonth ??
                current.plant.overheadByProcess[current.plant.processType].equipmentDepreciationPerMonth,
            },
          },
        },
      }));
      setStatusMessage(`Imported equipment setup CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import equipment setup CSV.');
      console.error(error);
    }
  };

  const importSpaceSetupForSummary = async (file: File | null) => {
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
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import space setup CSV.');
      console.error(error);
    }
  };

  const importDlohIdlSetupForSummary = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseDlohIdlSetupCsv(await file.text());
      updateProject((current) => {
        const nextPlant: ProjectState['plant'] = {
          ...current.plant,
          idlRowsByMode: { ...current.plant.idlRowsByMode, L10: parsed.idlL10, L6: parsed.idlL6 },
        };
        nextPlant.indirectLaborRows = resolveIndirectLaborRows(nextPlant);
        return { ...current, plant: nextPlant };
      });
      setStatusMessage(`Imported DLOH/IDL setup CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import DLOH/IDL setup CSV.');
      console.error(error);
    }
  };

  // ── Review gate ─────────────────────────────────────────────────────────────
  const renderReviewGate = () => (
    <SectionCard title="Review Gate" description="Exports are only meaningful when reviewer metadata is populated.">
      <div className="form-grid cols-4">
        <label>
          <span>Decision</span>
          <select
            value={project.confirmation.decision ?? ''}
            onChange={(event) => {
              updateProject((current) => ({
                ...current,
                confirmation: {
                  ...current.confirmation,
                  decision:
                    event.target.value === 'OK' || event.target.value === 'NG' ? event.target.value : null,
                  decidedAt: new Date().toISOString(),
                },
              }));
              setStatusMessage(`Review gate updated: ${event.target.value || 'Unreviewed'}`);
            }}
          >
            <option value="">Unreviewed</option>
            <option value="OK">OK</option>
            <option value="NG">NG</option>
          </select>
        </label>
        <label>
          <span>Reviewer</span>
          <input
            value={project.confirmation.reviewer}
            onChange={(event) =>
              updateProject((current) => ({
                ...current,
                confirmation: { ...current.confirmation, reviewer: event.target.value },
              }))
            }
          />
        </label>
        <label className="full-span">
          <span>Comment</span>
          <input
            value={project.confirmation.comment}
            onChange={(event) =>
              updateProject((current) => ({
                ...current,
                confirmation: { ...current.confirmation, comment: event.target.value },
              }))
            }
          />
        </label>
      </div>
      <p className="muted">Recorded at: {project.confirmation.decidedAt ?? 'Not recorded yet'}</p>
    </SectionCard>
  );

  // ── Imports/exports slot for SummaryPage ────────────────────────────────────
  const renderImportsExports = (summaryText: string, processLabel: 'L10' | 'L6') => (
    <SectionCard
      title="Imports and Exports"
      description={`Structured exchange for ${processLabel} summary verification and release handoff.`}
    >
      <div className="action-grid">
        <label className="upload-card">
          <span>Import Project JSON</span>
          <input
            type="file"
            accept="application/json"
            onChange={(event) => void importJsonProject(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="upload-card">
          <span>Import Month/Year Update CSV</span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void importMonthYearUpdateForSummary(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="upload-card">
          <span>Import Equipment Setup CSV</span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void importEquipmentSetupForSummary(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="upload-card">
          <span>Import Space Setup CSV</span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void importSpaceSetupForSummary(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="upload-card">
          <span>Import DLOH/IDL Setup CSV</span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void importDlohIdlSetupForSummary(event.target.files?.[0] ?? null)}
          />
        </label>
        <label className="upload-card">
          <span>Import Line Standards JSON</span>
          <input
            type="file"
            accept="application/json"
            onChange={(event) => void importLineStandards(event.target.files?.[0] ?? null)}
          />
        </label>
        <button
          type="button"
          className="button secondary tall"
          onClick={() =>
            downloadText(
              `${project.basicInfo.modelName}_${formatTimestamp()}_${processLabel.toLowerCase()}_summary.csv`,
              summaryText,
              'text/csv',
            )
          }
        >
          Export {processLabel} Summary CSV
        </button>
        <button type="button" className="button secondary tall" onClick={exportProject}>
          Export Project JSON
        </button>
        <button type="button" className="button secondary tall" onClick={exportL10Stations}>
          Export L10 Stations CSV
        </button>
        <button type="button" className="button secondary tall" onClick={exportLineStandards}>
          Export Line Standards JSON
        </button>
      </div>
    </SectionCard>
  );

  // ── Inline small tabs (machine_rates, bom_map, model_process) ───────────────
  const sideBadgeClass = (side: string) => {
    if (side === 'Top') return 'side-badge top';
    if (side === 'Bottom') return 'side-badge bottom';
    return 'side-badge neutral';
  };

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
              <p className="muted">
                Legacy-aligned MVA workflow with typed state, import pipelines, and
                verification-friendly summaries.
              </p>
            </div>
            <div className="hero-actions">
              <button type="button" className="button secondary" onClick={exportProject}>
                Export Project JSON
              </button>
              <button type="button" className="button primary" onClick={exportSummary}>
                Export Summary CSV
              </button>
            </div>
          </header>

          {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

          {activeTab === 'basic' ? (
            <BasicInfoPage project={project} updateProject={updateProject} />
          ) : null}

          {activeTab === 'machine_rates' ? (
            <div className="stack-xl">
              <SectionCard
                title="Machine Rates"
                description="Rate edits feed the simulation and downstream MVA calculations immediately."
              >
                <div className="data-table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Group</th>
                        <th>Description</th>
                        <th>Rate / Hr</th>
                      </tr>
                    </thead>
                    <tbody>
                      {project.machines.map((machine) => (
                        <tr key={machine.id}>
                          <td>{machine.group}</td>
                          <td>{machine.description}</td>
                          <td>
                            <input
                              aria-label={`Rate ${machine.group}`}
                              type="number"
                              value={machine.rate}
                              onChange={(event) =>
                                updateProject((current) => ({
                                  ...current,
                                  machines: current.machines.map((item) =>
                                    item.id === machine.id
                                      ? { ...item, rate: numberValue(event.target.value) }
                                      : item,
                                  ),
                                }))
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            </div>
          ) : null}

          {activeTab === 'bom_map' ? (
            <div className="stack-xl">
              <SectionCard
                title="BOM Mapping"
                description="Package counts by side and assigned machine group."
              >
                <div className="data-table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Side</th>
                        <th>Bucket</th>
                        <th>Machine Group</th>
                        <th>Count</th>
                      </tr>
                    </thead>
                    <tbody>
                      {project.bomMap.map((row) => (
                        <tr key={row.id}>
                          <td>{row.side}</td>
                          <td>{row.bucket}</td>
                          <td>{row.machineGroup}</td>
                          <td>
                            <input
                              type="number"
                              value={row.count}
                              onChange={(event) =>
                                updateProject((current) => ({
                                  ...current,
                                  bomMap: current.bomMap.map((item) =>
                                    item.id === row.id
                                      ? { ...item, count: numberValue(event.target.value) }
                                      : item,
                                  ),
                                }))
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            </div>
          ) : null}

          {activeTab === 'model_process' ? (
            <div className="stack-xl">
              <SectionCard
                title="Model Process"
                description="Editable process routing matching the legacy model-process worksheet."
                actions={
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() =>
                      updateProject((current) => ({
                        ...current,
                        processSteps: [
                          ...current.processSteps,
                          { step: current.processSteps.length + 1, process: '', side: 'N/A' },
                        ],
                      }))
                    }
                  >
                    Add Step
                  </button>
                }
              >
                <div className="data-table-wrapper sticky-header tall-table">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Seq</th>
                        <th>Process / Machine Group</th>
                        <th>Side</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {project.processSteps.map((row, index) => (
                        <tr key={`${row.step}-${index}`}>
                          <td>
                            <input
                              type="number"
                              value={row.step}
                              onChange={(event) =>
                                updateProject((current) => ({
                                  ...current,
                                  processSteps: current.processSteps.map((r, i) =>
                                    i === index
                                      ? { ...r, step: Math.max(1, numberValue(event.target.value)) }
                                      : r,
                                  ),
                                }))
                              }
                              placeholder="Seq"
                            />
                          </td>
                          <td>
                            <input
                              value={row.process}
                              onChange={(event) =>
                                updateProject((current) => ({
                                  ...current,
                                  processSteps: current.processSteps.map((r, i) =>
                                    i === index ? { ...r, process: event.target.value } : r,
                                  ),
                                }))
                              }
                              placeholder="Process / Machine Group"
                            />
                          </td>
                          <td>
                            <div className="process-side-cell">
                              <select
                                value={row.side}
                                onChange={(event) =>
                                  updateProject((current) => ({
                                    ...current,
                                    processSteps: current.processSteps.map((r, i) =>
                                      i === index ? { ...r, side: event.target.value } : r,
                                    ),
                                  }))
                                }
                              >
                                <option value="Top">Top</option>
                                <option value="Bottom">Bottom</option>
                                <option value="N/A">N/A</option>
                              </select>
                              <span className={sideBadgeClass(row.side)}>{row.side}</span>
                            </div>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="button ghost"
                              onClick={() =>
                                updateProject((current) => ({
                                  ...current,
                                  processSteps: current.processSteps
                                    .filter((_, i) => i !== index)
                                    .map((r, i) => ({ ...r, step: i + 1 })),
                                }))
                              }
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            </div>
          ) : null}

          {activeTab === 'simulation_results' ? (
            <SimulationPage demandWeekly={project.basicInfo.demandWeekly} simulation={simulation} />
          ) : null}

          {activeTab === 'mva_plant_rates' ? (
            <PlantRatesPage
              project={project}
              updateProject={updateProject}
              mva={mva}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mva_plant_env' ? (
            <PlantEnvPage project={project} updateProject={updateProject} mva={mva} />
          ) : null}

          {activeTab === 'mva_plant_equipment' ? (
            <PlantEquipmentPage
              project={project}
              updateProject={updateProject}
              selectedLineStandardId={selectedLineStandardId}
              onUpdateLineStandardSelection={setSelectedLineStandardId}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mva_plant_space' ? (
            <PlantSpacePage
              project={project}
              updateProject={updateProject}
              mva={mva}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mva_plant_dloh_idl' ? (
            <DlohIdlPage
              project={project}
              updateProject={updateProject}
              mva={mva}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mpm_l10' ? (
            <MpmL10Page
              project={project}
              updateProject={updateProject}
              l10Mva={l10Mva}
              l10Simulation={l10Simulation}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mpm_l6' ? (
            <MpmL6Page project={project} updateProject={updateProject} onStatusMessage={setStatusMessage} />
          ) : null}

          {activeTab === 'mva_labor_l10' ? (
            <LaborTimeL10Page
              project={project}
              updateProject={updateProject}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mva_labor_l6' ? (
            <LaborTimeL6Page
              project={project}
              updateProject={updateProject}
              onStatusMessage={setStatusMessage}
            />
          ) : null}

          {activeTab === 'mva_summary_l10' ? (
            <SummaryPage
              processLabel="L10"
              project={project}
              summaryMva={l10Mva}
              summarySimulation={l10Simulation}
              summaryText={l10SummaryCsv}
              updateProject={updateProject}
              importsExportsSlot={renderImportsExports(l10SummaryCsv, 'L10')}
            />
          ) : null}

          {activeTab === 'mva_summary_l6' ? (
            <SummaryPage
              processLabel="L6"
              project={project}
              summaryMva={l6Mva}
              summarySimulation={l6Simulation}
              summaryText={l6SummaryCsv}
              updateProject={updateProject}
              importsExportsSlot={renderImportsExports(l6SummaryCsv, 'L6')}
            />
          ) : null}
        </main>
      </ErrorBoundary>
    </div>
  );
}
