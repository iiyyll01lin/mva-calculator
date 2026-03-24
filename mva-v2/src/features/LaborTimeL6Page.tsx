import { memo, useMemo } from 'react';
import { SectionCard } from '../components/SectionCard';
import { parseL6LaborTimeEstimationCsv } from '../domain/importers';
import type { LaborRow, ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue } from '../utils/formatters';

function buildFallbackL6Segments(project: ProjectState): ProjectState['laborTimeL6']['segments'] {
  if (project.laborTimeL6.segments.length > 0) {
    return project.laborTimeL6.segments.map((segment, segmentIndex) => ({
      ...segment,
      id: segment.id || `seg-${segmentIndex + 1}`,
      name: segment.name || project.productL6.sku || project.productL6.pn || `Segment ${segmentIndex + 1}`,
      stations: segment.stations.map((station, stationIndex) => ({
        ...station,
        id: station.id || `${segment.id || `seg-${segmentIndex + 1}`}-station-${stationIndex + 1}`,
      })),
    }));
  }
  return [{
    id: 'seg-1',
    name: project.productL6.sku || project.productL6.pn || 'Segment 1',
    stations: project.laborTimeL6.stations.map((station, stationIndex) => ({
      ...station,
      id: station.id || `seg-1-station-${stationIndex + 1}`,
    })),
  }];
}

function syncL6Segments(
  current: ProjectState,
  segments: ProjectState['laborTimeL6']['segments'],
  source: string = current.laborTimeL6.source,
): ProjectState {
  return {
    ...current,
    laborTimeL6: { ...current.laborTimeL6, segments, stations: segments.flatMap((seg) => seg.stations), source },
  };
}

interface LaborTimeL6PageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  onStatusMessage: (msg: string) => void;
}

export const LaborTimeL6Page = memo(function LaborTimeL6Page({ project, updateProject, onStatusMessage }: LaborTimeL6PageProps) {
  const l6SegmentSnapshots = useMemo(() => buildFallbackL6Segments(project).map((segment) => ({
    ...segment,
    stations: segment.stations.map((station) => {
      const parallelStations = Math.max(1, station.parallelStations || 1);
      const allowanceRate = station.allowanceRate ?? 0;
      const allowanceFactor = allowanceRate > 1 ? 1 + allowanceRate / 100 : 1 + allowanceRate;
      const cycleTimeSec = station.cycleTimeSec ?? 0;
      const avgCycleTimeSec = cycleTimeSec > 0 ? (cycleTimeSec * allowanceFactor) / parallelStations : 0;
      const uph = avgCycleTimeSec > 0 ? 3600 / avgCycleTimeSec : 0;
      return { ...station, avgCycleTimeSec, uph };
    }),
  })), [project]);

  const l6StationSnapshots = useMemo(() => l6SegmentSnapshots.flatMap((seg) => seg.stations), [l6SegmentSnapshots]);
  const l6LaborSummary = useMemo(() => {
    const activeStations = l6StationSnapshots.filter((station) => !station.isTotal);
    return {
      segments: l6SegmentSnapshots.length,
      stations: activeStations.length,
      totalHc: activeStations.reduce((sum, station) => sum + (station.dlOnline ?? station.laborHc ?? 0), 0),
      bottleneckUph: activeStations.reduce((lowest, station) => {
        const next = station.uph ?? 0;
        if (lowest === 0) return next;
        if (next === 0) return lowest;
        return Math.min(lowest, next);
      }, 0),
    };
  }, [l6SegmentSnapshots, l6StationSnapshots]);

  const updateL6SegmentName = (segmentId: string, name: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, name } : segment)),
    ));
  };
  const addL6Segment = () => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      return syncL6Segments(current, [
        ...baseSegments,
        { id: `seg-${Date.now()}`, name: `Segment ${baseSegments.length + 1}`, stations: [{ id: `l6-${Date.now()}`, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }] },
      ]);
    });
  };
  const deleteL6Segment = (segmentId: string) => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      const nextSegments = baseSegments.filter((segment) => segment.id !== segmentId);
      if (nextSegments.length === 0) {
        return syncL6Segments(current, [{ id: 'seg-1', name: current.productL6.sku || current.productL6.pn || 'Segment 1', stations: [] }]);
      }
      return syncL6Segments(current, nextSegments);
    });
  };
  const updateL6Station = (segmentId: string, id: string, patch: Partial<ProjectState['laborTimeL6']['stations'][number]>) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId
        ? { ...segment, stations: segment.stations.map((station) => (station.id === id ? { ...station, ...patch } : station)) }
        : segment)),
    ));
  };
  const addL6Station = (segmentId: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => {
        if (segment.id !== segmentId) return segment;
        const maxStationNo = segment.stations.reduce((max, station) => Math.max(max, station.stationNo ?? 0), 0);
        return { ...segment, stations: [...segment.stations, { id: `l6-${Date.now()}`, stationNo: maxStationNo + 1, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }] };
      }),
    ));
  };
  const deleteL6Station = (segmentId: string, id: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, stations: segment.stations.filter((station) => station.id !== id) } : segment)),
    ));
  };
  const applyL6StationsToDirectLabor = () => {
    updateProject((current) => ({
      ...current,
      plant: {
        ...current.plant,
        directLaborRows: buildFallbackL6Segments(current).flatMap((segment) => segment.stations).filter((station) => !station.isTotal).map((station, index): LaborRow => ({
          id: `l6-dl-${index}`,
          name: station.name,
          process: station.name,
          headcount: station.laborHc,
          uphSource: 'line',
        })),
      },
    }));
  };
  const importL6LaborEstimation = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL6LaborTimeEstimationCsv(await file.text());
      updateProject((current) => ({ ...current, laborTimeL6: parsed }));
      onStatusMessage(`Imported L6 labor estimation CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import L6 labor estimation CSV.');
      console.error(error);
    }
  };

  return (
    <div className="stack-xl">
      <SectionCard title="Labor Time Estimation (L6)" description="Legacy-style L6 segment matrix with per-segment station columns and derived CT / UPH outputs." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={addL6Segment}>Add Segment</button><button type="button" className="button secondary" onClick={applyL6StationsToDirectLabor}>Apply to Direct Labor</button></div>}>
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Import Mini-MOST Time Estimation Data</h3></div>
            <div className="form-grid cols-2">
              <label className="full-span"><span>Import L6 labor time estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6LaborEstimation(event.target.files?.[0] ?? null)} /></label>
              <label><span>Source</span><input readOnly value={project.laborTimeL6.source} /></label>
              <label><span>Parsed Segments</span><input readOnly value={l6LaborSummary.segments} /></label>
              <label><span>Parsed Stations</span><input readOnly value={l6LaborSummary.stations} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Detailed Statistics Breakdown</h3></div>
            <div className="form-grid cols-2">
              <label><span>Mfg Time / Unit</span><input type="number" value={project.laborTimeL6.header.manufacturingTimePerUnit ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, manufacturingTimePerUnit: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Output / Line / Shift</span><input type="number" value={project.laborTimeL6.header.outputPerLinePerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, outputPerLinePerShift: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Total DL Online</span><input readOnly value={l6LaborSummary.totalHc.toFixed(2)} /></label>
              <label><span>Bottleneck UPH</span><input readOnly value={l6LaborSummary.bottleneckUph.toFixed(2)} /></label>
            </div>
          </section>
        </div>
        <div className="stack-xl mt-md">
          {l6SegmentSnapshots.map((segment) => {
            const activeStations = segment.stations.filter((station) => !station.isTotal);
            const segmentTotalHc = activeStations.reduce((sum, station) => sum + (station.dlOnline ?? station.laborHc ?? 0), 0);
            const segmentBottleneck = activeStations.reduce((lowest, station) => {
              const next = station.uph ?? 0;
              if (lowest === 0) return next;
              if (next === 0) return lowest;
              return Math.min(lowest, next);
            }, 0);
            return (
              <section className="segment-matrix-card" key={segment.id}>
                <div className="segment-matrix-header">
                  <label>
                    <span>Segment Name</span>
                    <input value={segment.name} onChange={(event) => updateL6SegmentName(segment.id, event.target.value)} />
                  </label>
                  <div className="inline-actions">
                    <button type="button" className="button ghost" onClick={() => addL6Station(segment.id)}>Add Station</button>
                    <button type="button" className="button ghost" onClick={() => deleteL6Segment(segment.id)} disabled={l6SegmentSnapshots.length === 1}>Delete Segment</button>
                  </div>
                </div>
                <div className="data-table-wrapper sticky-header tall-table mt-md">
                  <table className="data-table matrix-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        {segment.stations.map((station) => (
                          <th key={station.id}>{station.name || `Station ${station.stationNo ?? ''}`}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr><th>Station</th>{segment.stations.map((station) => <td key={`${station.id}-name`}><input value={station.name} onChange={(event) => updateL6Station(segment.id, station.id, { name: event.target.value })} /></td>)}</tr>
                      <tr><th>No</th>{segment.stations.map((station) => <td key={`${station.id}-no`}><input type="number" value={station.stationNo ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { stationNo: event.target.value === '' ? undefined : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>HC</th>{segment.stations.map((station) => <td key={`${station.id}-hc`}><input type="number" value={station.laborHc} onChange={(event) => updateL6Station(segment.id, station.id, { laborHc: numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Fixture</th>{segment.stations.map((station) => <td key={`${station.id}-fixture`}><input type="number" value={station.parallelStations} onChange={(event) => updateL6Station(segment.id, station.id, { parallelStations: numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Cycle Time</th>{segment.stations.map((station) => <td key={`${station.id}-ct`}><input type="number" value={station.cycleTimeSec ?? 0} onChange={(event) => updateL6Station(segment.id, station.id, { cycleTimeSec: numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Allowance Rate</th>{segment.stations.map((station) => <td key={`${station.id}-allow`}><input type="number" step="0.01" value={station.allowanceRate ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { allowanceRate: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Std Man Hour</th>{segment.stations.map((station) => <td key={`${station.id}-std`}><input type="number" step="0.01" value={station.stdManHours ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { stdManHours: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>VPY</th>{segment.stations.map((station) => <td key={`${station.id}-vpy`}><input type="number" step="0.01" value={station.vpy ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { vpy: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Shift Rate</th>{segment.stations.map((station) => <td key={`${station.id}-shift`}><input type="number" step="0.01" value={station.shiftRate ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { shiftRate: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>DL Online</th>{segment.stations.map((station) => <td key={`${station.id}-dl`}><input type="number" value={station.dlOnline ?? ''} onChange={(event) => updateL6Station(segment.id, station.id, { dlOnline: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>)}</tr>
                      <tr><th>Avg CT</th>{segment.stations.map((station) => <td key={`${station.id}-avg`} className="matrix-readonly-cell">{(station.avgCycleTimeSec ?? 0).toFixed(2)}</td>)}</tr>
                      <tr><th>UPH</th>{segment.stations.map((station) => <td key={`${station.id}-uph`} className="matrix-readonly-cell">{(station.uph ?? 0).toFixed(2)}</td>)}</tr>
                      <tr><th>Action</th>{segment.stations.map((station) => <td key={`${station.id}-action`}><button type="button" className="button ghost" onClick={() => deleteL6Station(segment.id, station.id)}>Delete</button></td>)}</tr>
                    </tbody>
                  </table>
                </div>
                <div className="matrix-summary-strip">
                  <div><span>Segment Stations</span><strong>{activeStations.length}</strong></div>
                  <div><span>Segment DL Online</span><strong>{segmentTotalHc.toFixed(2)}</strong></div>
                  <div><span>Segment Bottleneck UPH</span><strong>{segmentBottleneck.toFixed(2)}</strong></div>
                </div>
              </section>
            );
          })}
        </div>
      </SectionCard>
    </div>
  );
});
