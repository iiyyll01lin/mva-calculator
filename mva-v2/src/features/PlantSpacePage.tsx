import { memo, useMemo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { MvaResults, ProjectState } from '../domain/models';
import { parseSpaceSetupCsv } from '../domain/importers';
import { assertFileSize } from '../utils/fileImport';
import { numberValue, toMoney } from '../utils/formatters';

interface PlantSpacePageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  mva: MvaResults;
  onStatusMessage: (msg: string) => void;
}

export const PlantSpacePage = memo(function PlantSpacePage({ project, updateProject, mva, onStatusMessage }: PlantSpacePageProps) {
  const matrixLineArea = useMemo(
    () => Math.max(0, project.plant.spaceSettings.lineLengthFt * project.plant.spaceSettings.lineWidthFt * project.plant.spaceSettings.spaceAreaMultiplier),
    [project.plant.spaceSettings.lineLengthFt, project.plant.spaceSettings.lineWidthFt, project.plant.spaceSettings.spaceAreaMultiplier],
  );

  const updateSpaceAllocationRow = (id: string, patch: Partial<ProjectState['plant']['spaceAllocation'][number]>) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, spaceAllocation: current.plant.spaceAllocation.map((row) => (row.id === id ? { ...row, ...patch } : row)) },
    }));
  };
  const addSpaceAllocationRow = () => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, spaceAllocation: [...current.plant.spaceAllocation, { id: `space-${Date.now()}`, floor: 'F1', process: '', areaSqft: 0, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: null }] },
    }));
  };
  const deleteSpaceAllocationRow = (id: string) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, spaceAllocation: current.plant.spaceAllocation.filter((row) => row.id !== id) },
    }));
  };
  const applyMatrixSpaceAllocation = () => {
    updateProject((current) => {
      const floors = Object.entries(current.plant.spaceSettings.floorAreas)
        .filter(([, area]) => area > 0)
        .map(([floor]) => floor);
      const floorOrder = floors.length > 0 ? floors : ['F1'];
      const processRows = Object.entries(current.plant.spaceSettings.processDistribution)
        .filter(([, percent]) => percent > 0)
        .map(([process, percent], index) => ({
          id: `matrix-${process}-${index}`,
          floor: floorOrder[index % floorOrder.length],
          process,
          areaSqft: matrixLineArea * (percent / 100),
          ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft,
          monthlyCost: matrixLineArea * (percent / 100) * current.plant.spaceSettings.spaceRatePerSqft,
        }));
      return { ...current, plant: { ...current.plant, spaceAllocation: processRows } };
    });
  };
  const applySplit4060SpaceAllocation = () => {
    updateProject((current) => {
      const totalFloorSqft = current.plant.spaceSettings.split4060TotalFloorSqft;
      const smtSqft = totalFloorSqft * 0.4;
      const hiSqft = totalFloorSqft * 0.6;
      const rows = current.plant.spaceSettings.split4060HiMode === 'FA'
        ? [
            { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
            { id: 'split-fa', floor: 'F1', process: 'F/A', areaSqft: hiSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: hiSqft * current.plant.spaceSettings.spaceRatePerSqft },
          ]
        : current.plant.spaceSettings.split4060HiMode === 'FBT'
          ? [
              { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fbt', floor: 'F1', process: 'FBT', areaSqft: hiSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: hiSqft * current.plant.spaceSettings.spaceRatePerSqft },
            ]
          : [
              { id: 'split-smt', floor: 'F1', process: 'SMT', areaSqft: smtSqft, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: smtSqft * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fa', floor: 'F1', process: 'F/A', areaSqft: hiSqft / 2, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: (hiSqft / 2) * current.plant.spaceSettings.spaceRatePerSqft },
              { id: 'split-fbt', floor: 'F1', process: 'FBT', areaSqft: hiSqft / 2, ratePerSqft: current.plant.spaceSettings.spaceRatePerSqft, monthlyCost: (hiSqft / 2) * current.plant.spaceSettings.spaceRatePerSqft },
            ];
      return { ...current, plant: { ...current.plant, spaceAllocation: rows } };
    });
  };
  const importSpaceSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseSpaceSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          spaceAllocation: parsed.allocations,
          spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: parsed.areaMultiplier ?? current.plant.spaceSettings.spaceAreaMultiplier },
        },
      }));
      onStatusMessage(`Imported space setup CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import space setup CSV.');
      console.error(error);
    }
  };

  return (
    <div className="stack-xl">
      <SectionCard title="Space Setup" description="Matrix mode, manual allocation, and 40/60 split controls aligned to the legacy worksheet.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Mode Selection</h3><p className="muted">Matrix: configure building / distribution and generate allocation. Manual: edit rows directly.</p></div>
            <div className="form-grid cols-2">
              <label><span>Mode</span><select value={project.plant.spaceSettings.mode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, mode: event.target.value as ProjectState['plant']['spaceSettings']['mode'] } } }))}><option value="manual">manual</option><option value="matrix">matrix</option></select></label>
              <label><span>Space Rate / Sqft</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceRatePerSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceRatePerSqft: numberValue(event.target.value) } } }))} /></label>
              <label><span>Area Multiplier</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceAreaMultiplier} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: numberValue(event.target.value) } } }))} /></label>
              <label><span>Total Line Area</span><input readOnly value={matrixLineArea.toFixed(2)} /></label>
            </div>
          </section>

          {project.plant.spaceSettings.mode === 'matrix' ? (
            <section className="legacy-subcard">
              <div className="legacy-subcard-header"><h3>2. Line Specification</h3><p className="muted">Line length and width used by matrix allocation.</p></div>
              <div className="form-grid cols-2">
                <label><span>Line Length Ft</span><input type="number" value={project.plant.spaceSettings.lineLengthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineLengthFt: numberValue(event.target.value) } } }))} /></label>
                <label><span>Line Width Ft</span><input type="number" value={project.plant.spaceSettings.lineWidthFt} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, lineWidthFt: numberValue(event.target.value) } } }))} /></label>
              </div>
            </section>
          ) : null}

          {project.plant.spaceSettings.mode === 'matrix' ? (
            <section className="legacy-subcard">
              <div className="legacy-subcard-header"><h3>1. Building Area by Floor</h3><p className="muted">BF / F1 / F2 space pool used by matrix allocation.</p></div>
              <div className="form-grid cols-2">
                {Object.entries(project.plant.spaceSettings.floorAreas).map(([floor, area]) => (
                  <label key={floor}><span>{floor}</span><input type="number" value={area} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, floorAreas: { ...current.plant.spaceSettings.floorAreas, [floor]: numberValue(event.target.value) } } } }))} /></label>
                ))}
              </div>
            </section>
          ) : null}

          {project.plant.spaceSettings.mode === 'matrix' ? (
            <section className="legacy-subcard full-span-card">
              <div className="legacy-subcard-header"><h3>3. Process Distribution (%)</h3><p className="muted">Define SMT / HI / Test distribution and apply matrix calculation.</p></div>
              <div className="form-grid cols-4">
                {Object.entries(project.plant.spaceSettings.processDistribution).map(([process, percent]) => (
                  <label key={process}><span>{process}</span><input type="number" step="0.1" value={percent} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, processDistribution: { ...current.plant.spaceSettings.processDistribution, [process]: numberValue(event.target.value) } } } }))} /></label>
                ))}
              </div>
              <div className="inline-actions mt-md"><button type="button" className="button primary" onClick={applyMatrixSpaceAllocation}>Apply Matrix Calculation</button></div>
            </section>
          ) : null}

          <section className="legacy-subcard full-span-card">
            <div className="legacy-subcard-header"><h3>40 / 60 Split</h3><p className="muted">Quick helper for SMT 40% and HI 60% allocation.</p></div>
            <div className="form-grid cols-4">
              <label><span>40/60 Split</span><select value={project.plant.spaceSettings.split4060Enabled ? 'enabled' : 'disabled'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060Enabled: event.target.value === 'enabled' } } }))}><option value="disabled">disabled</option><option value="enabled">enabled</option></select></label>
              <label><span>40/60 Total Floor</span><input type="number" value={project.plant.spaceSettings.split4060TotalFloorSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060TotalFloorSqft: numberValue(event.target.value) } } }))} /></label>
              <label><span>40/60 Hi Mode</span><select value={project.plant.spaceSettings.split4060HiMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, split4060HiMode: event.target.value as ProjectState['plant']['spaceSettings']['split4060HiMode'] } } }))}><option value="FA">FA</option><option value="FBT">FBT</option><option value="FA_FBT">FA_FBT</option></select></label>
              <div className="button-grid-align"><button type="button" className="button secondary" onClick={applySplit4060SpaceAllocation} disabled={!project.plant.spaceSettings.split4060Enabled}>Apply 40/60 Split</button></div>
            </div>
          </section>
        </div>
      </SectionCard>

      <SectionCard title={`Space Allocation${project.plant.spaceSettings.mode === 'matrix' ? ' (Generated)' : ' (Manual)'}`} description="Editable space rows with process, area, and rate.">
        {project.plant.spaceSettings.mode === 'manual' ? (
          <div className="inline-actions">
            <button type="button" className="button ghost" onClick={addSpaceAllocationRow} data-testid="mva-space-add">Add Row</button>
            <label className="upload-inline"><span>Import Space Setup CSV</span><input type="file" accept=".csv,text/csv" data-testid="mva-import-space-setup" onChange={(event) => void importSpaceSetup(event.target.files?.[0] ?? null)} /></label>
          </div>
        ) : null}
        <div className="data-table-wrapper compact mt-md">
          <table className="data-table">
            <thead><tr><th>Floor</th><th>Process</th><th>Area (sq.ft.)</th><th>Rate</th><th>Monthly Cost</th><th>Action</th></tr></thead>
            <tbody>
              {project.plant.spaceAllocation.map((row) => (
                <tr key={row.id}>
                  <td><input value={row.floor} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { floor: event.target.value })} /></td>
                  <td><input value={row.process} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { process: event.target.value })} /></td>
                  <td><input type="number" value={row.areaSqft} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { areaSqft: numberValue(event.target.value) })} /></td>
                  <td><input type="number" value={row.ratePerSqft ?? 0} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { ratePerSqft: numberValue(event.target.value) })} /></td>
                  <td><input type="number" value={row.monthlyCost ?? ''} disabled={project.plant.spaceSettings.mode === 'matrix'} onChange={(event) => updateSpaceAllocationRow(row.id, { monthlyCost: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                  <td><button type="button" className="button ghost" disabled={project.plant.spaceSettings.mode === 'matrix'} onClick={() => deleteSpaceAllocationRow(row.id)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <label className="checkbox-field mt-md"><input type="checkbox" checked={project.plant.spaceSettings.useSpaceAllocationTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, useSpaceAllocationTotal: event.target.checked } } }))} /><span>Use space allocation total to override Space / Rental / Cleaning (Monthly)</span></label>
      </SectionCard>

      <SectionCard title="Space Results" description="Resolved per-row space cost and aggregate building impact.">
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead><tr><th>Floor</th><th>Process</th><th>Area Sqft</th><th>Monthly Cost Used</th></tr></thead>
            <tbody>
              {mva.spaceAllocation.map((row) => (
                <tr key={row.id}><td>{row.floor}</td><td>{row.process}</td><td>{row.areaSqft}</td><td>{toMoney(row.monthlyCostUsed)}</td></tr>
              ))}
              <tr className="summary-row"><td colSpan={2}>Total</td><td>{mva.overhead.spaceFloorTotalSqft.toFixed(2)}</td><td>{toMoney(mva.overhead.spaceMonthlyCostUsed)}</td></tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
});
