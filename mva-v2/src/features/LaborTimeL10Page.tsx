import { memo, useCallback, useMemo, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { SectionCard } from '../components/SectionCard';
import { parseL10LaborTimeEstimationCsv } from '../domain/importers';
import type { LaborRow, ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue } from '../utils/formatters';

// Pixel height of a single L10 station row (matches table-cell padding in data-table).
const L10_ROW_HEIGHT = 44;
// Cap the visible window; beyond this height virtualization kicks in.
const MAX_TABLE_HEIGHT = 540;

interface LaborTimeL10PageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  onStatusMessage: (msg: string) => void;
}

export const LaborTimeL10Page = memo(function LaborTimeL10Page({ project, updateProject, onStatusMessage }: LaborTimeL10PageProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const l10StationSnapshots = useMemo(() => project.laborTimeL10.stations.map((station) => {
    const parallelStations = Math.max(1, station.parallelStations || 1);
    const cycleTimeSec = station.cycleTimeSec ?? 0;
    const avgCycleTimeSec = cycleTimeSec > 0 ? (cycleTimeSec * station.allowanceFactor) / parallelStations : 0;
    const uph = avgCycleTimeSec > 0 ? 3600 / avgCycleTimeSec : 0;
    const hoursPerShift = project.laborTimeL10.meta.hoursPerShift ?? 0;
    const shiftsPerDay = project.laborTimeL10.meta.shiftsPerDay ?? 0;
    const workDaysPerWeek = project.laborTimeL10.meta.workDaysPerWeek ?? 0;
    const workDaysPerMonth = project.laborTimeL10.meta.workDaysPerMonth ?? 0;
    return {
      ...station,
      avgCycleTimeSec,
      uph,
      perShiftCapa: uph * hoursPerShift,
      dailyCapa: uph * hoursPerShift * shiftsPerDay,
      weeklyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerWeek,
      monthlyCapa: uph * hoursPerShift * shiftsPerDay * workDaysPerMonth,
    };
  }), [project.laborTimeL10.meta, project.laborTimeL10.stations]);

  const updateL10Station = useCallback((id: string, patch: Partial<ProjectState['laborTimeL10']['stations'][number]>) => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: { ...current.laborTimeL10, stations: current.laborTimeL10.stations.map((station) => (station.id === id ? { ...station, ...patch } : station)) },
    }));
  }, [updateProject]);
  const addL10Station = useCallback(() => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: { ...current.laborTimeL10, stations: [...current.laborTimeL10.stations, { id: `l10-${Date.now()}`, name: '', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceFactor: 1.15 }] },
    }));
  }, [updateProject]);
  const deleteL10Station = useCallback((id: string) => {
    updateProject((current) => ({
      ...current,
      laborTimeL10: { ...current.laborTimeL10, stations: current.laborTimeL10.stations.filter((station) => station.id !== id) },
    }));
  }, [updateProject]);
  const applyL10StationsToDirectLabor = useCallback(() => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: current.laborTimeL10.stations.map((station, index): LaborRow => ({
          id: `l10-dl-${index}`,
          name: station.name,
          process: station.name,
          headcount: station.laborHc,
          uphSource: 'line',
        })),
      },
    }));
  }, [updateProject]);
  const importL10LaborEstimation = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL10LaborTimeEstimationCsv(await file.text());
      updateProject((current) => ({ ...current, laborTimeL10: parsed }));
      onStatusMessage(`Imported L10 labor estimation CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import L10 labor estimation CSV.');
      console.error(error);
    }
  };

  // Row virtualizer — only visible rows are mounted in the DOM.
  // For small datasets the container grows to fit; for large datasets (500+)
  // only the ~12 visible rows are rendered, cutting layout work from O(N) to O(1).
  const rowVirtualizer = useVirtualizer({
    count: l10StationSnapshots.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => L10_ROW_HEIGHT,
    overscan: 5,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();
  const totalHeight = rowVirtualizer.getTotalSize();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom = totalHeight - (virtualRows.length > 0 ? virtualRows[virtualRows.length - 1].end : 0);

  // Container height: grow with content for small datasets, cap for large ones.
  const tableHeight = Math.min(
    l10StationSnapshots.length * L10_ROW_HEIGHT + 48,
    MAX_TABLE_HEIGHT,
  );

  return (
    <div className="stack-xl">
      <SectionCard title="Labor Time (L10)" description="Import, meta, and station matrix for L10 labor time estimation." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={addL10Station}>Add Station</button><button type="button" className="button secondary" onClick={applyL10StationsToDirectLabor}>Apply to Direct Labor</button></div>}>
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Import Mini-MOST Time Estimation Data</h3></div>
            <div className="form-grid cols-2">
              <label className="full-span"><span>Import CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10LaborEstimation(event.target.files?.[0] ?? null)} /></label>
              <label><span>Source</span><input readOnly value={project.laborTimeL10.source} /></label>
              <label><span>Parsed Stations</span><input readOnly value={project.laborTimeL10.stations.length} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Meta</h3></div>
            <div className="form-grid cols-2">
              <label><span>Hours / Shift</span><input type="number" value={project.laborTimeL10.meta.hoursPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, hoursPerShift: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
              <label><span>Target Time</span><input type="number" value={project.laborTimeL10.meta.targetTime ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, targetTime: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
              <label><span>Shifts / Day</span><input type="number" value={project.laborTimeL10.meta.shiftsPerDay ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, shiftsPerDay: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
              <label><span>Work Days / Week</span><input type="number" value={project.laborTimeL10.meta.workDaysPerWeek ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, workDaysPerWeek: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
              <label><span>Work Days / Month</span><input type="number" value={project.laborTimeL10.meta.workDaysPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, workDaysPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
              <label><span>FPY</span><input type="number" step="0.01" value={project.laborTimeL10.meta.fpy ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL10: { ...current.laborTimeL10, meta: { ...current.laborTimeL10.meta, fpy: event.target.value === '' ? null : numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>
        </div>
        <div
          ref={parentRef}
          className="data-table-wrapper sticky-header mt-md"
          style={{ height: tableHeight, overflowY: 'auto' }}
        >
          <table className="data-table matrix-table">
            <thead><tr><th>Station</th><th>HC</th><th>Mach/Para</th><th>CT(s)</th><th>Allow</th><th>Avg CT</th><th>UPH</th><th>Shift Capa</th><th>Day Capa</th><th>Wk Capa</th><th>Mo Capa</th><th>Touch(min)</th><th>Handling(s)</th><th>1-Man-N-MC</th><th>Time(min)/cyc</th><th>Run/Day</th><th>Action</th></tr></thead>
            <tbody>
              {paddingTop > 0 && (
                <tr style={{ height: paddingTop }}>
                  <td colSpan={17} />
                </tr>
              )}
              {virtualRows.map((vRow) => {
                const station = l10StationSnapshots[vRow.index];
                return (
                  <tr key={station.id} style={{ height: L10_ROW_HEIGHT }}>
                    <td><input value={station.name} onChange={(event) => updateL10Station(station.id, { name: event.target.value })} /></td>
                    <td><input type="number" value={station.laborHc} onChange={(event) => updateL10Station(station.id, { laborHc: numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.parallelStations} onChange={(event) => updateL10Station(station.id, { parallelStations: numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.cycleTimeSec ?? 0} onChange={(event) => updateL10Station(station.id, { cycleTimeSec: numberValue(event.target.value) })} /></td>
                    <td><input type="number" step="0.01" value={station.allowanceFactor} onChange={(event) => updateL10Station(station.id, { allowanceFactor: numberValue(event.target.value) })} /></td>
                    <td>{station.avgCycleTimeSec.toFixed(2)}</td><td>{station.uph.toFixed(2)}</td><td>{station.perShiftCapa.toFixed(2)}</td><td>{station.dailyCapa.toFixed(2)}</td><td>{station.weeklyCapa.toFixed(2)}</td><td>{station.monthlyCapa.toFixed(2)}</td>
                    <td><input type="number" value={station.touchTimeMin ?? ''} onChange={(event) => updateL10Station(station.id, { touchTimeMin: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.handlingTimeSec ?? ''} onChange={(event) => updateL10Station(station.id, { handlingTimeSec: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.oneManMultiMachineCount ?? ''} onChange={(event) => updateL10Station(station.id, { oneManMultiMachineCount: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.timeMinPerCycle ?? ''} onChange={(event) => updateL10Station(station.id, { timeMinPerCycle: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                    <td><input type="number" value={station.runPerDay ?? ''} onChange={(event) => updateL10Station(station.id, { runPerDay: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                    <td><button type="button" className="button ghost" onClick={() => deleteL10Station(station.id)}>Delete</button></td>
                  </tr>
                );
              })}
              {paddingBottom > 0 && (
                <tr style={{ height: paddingBottom }}>
                  <td colSpan={17} />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
});
