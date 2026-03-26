import { memo, useCallback, useMemo, useRef, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { SectionCard } from '../components/SectionCard';
import { parseL6LaborTimeEstimationCsv } from '../domain/importers';
import type { L6Station, LaborRow, ProjectState } from '../domain/models';
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

// ── Column-virtualised segment matrix ──────────────────────────────────────

const STATION_COL_W = 130; // estimated px width per station column
const LABEL_COL_W = 120;   // fixed px width for the row-label column

type StationSnapshot = L6Station & { avgCycleTimeSec: number; uph: number };
type SegmentSnapshot = { id: string; name: string; stations: StationSnapshot[] };

interface SegmentMatrixProps {
  segment: SegmentSnapshot;
  totalSegments: number;
  updateL6SegmentName: (segmentId: string, name: string) => void;
  addL6Station: (segmentId: string) => void;
  deleteL6Segment: (segmentId: string) => void;
  updateL6Station: (segmentId: string, stationId: string, patch: Partial<Omit<L6Station, 'id'>>) => void;
  deleteL6Station: (segmentId: string, stationId: string) => void;
}

/** Deep-compare two segment snapshots; skip re-render unless station data changed. */
function areSegmentMatrixPropsEqual(
  prev: SegmentMatrixProps,
  next: SegmentMatrixProps,
): boolean {
  if (prev.totalSegments !== next.totalSegments) return false;
  const a = prev.segment;
  const b = next.segment;
  if (a === b) return true;
  if (a.id !== b.id || a.name !== b.name || a.stations.length !== b.stations.length) return false;
  return a.stations.every((s, i) => {
    const t = b.stations[i];
    return (
      s.id === t.id && s.name === t.name && s.laborHc === t.laborHc &&
      s.stationNo === t.stationNo && s.parallelStations === t.parallelStations &&
      s.cycleTimeSec === t.cycleTimeSec && s.allowanceRate === t.allowanceRate &&
      s.stdManHours === t.stdManHours && s.vpy === t.vpy && s.shiftRate === t.shiftRate &&
      s.dlOnline === t.dlOnline && s.isTotal === t.isTotal &&
      s.avgCycleTimeSec === t.avgCycleTimeSec && s.uph === t.uph
    );
  });
}

/**
 * Transposed station matrix for a single L6 segment with column virtualisation.
 *
 * Rows = fixed station attributes (HC, CT, UPH …), columns = N stations.
 * With 200+ stations only the ~5–10 currently visible columns are in the DOM,
 * cutting layout work from O(stations × 13) to O(visible × 13).
 *
 * Memoised with a deep segment comparison so typing in one segment does not
 * trigger re-renders in sibling segments.
 */
const SegmentMatrix = memo(function SegmentMatrix({
  segment,
  totalSegments,
  updateL6SegmentName,
  addL6Station,
  deleteL6Segment,
  updateL6Station,
  deleteL6Station,
}: SegmentMatrixProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const colVirtualizer = useVirtualizer({
    count: segment.stations.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => STATION_COL_W,
    horizontal: true,
    overscan: 3,
  });

  const virtualCols = colVirtualizer.getVirtualItems();
  const totalColSize = colVirtualizer.getTotalSize();
  const padLeft = virtualCols.length > 0 ? virtualCols[0].start : 0;
  const padRight = totalColSize - (virtualCols.length > 0 ? virtualCols[virtualCols.length - 1].end : 0);
  const tableWidth = LABEL_COL_W + totalColSize;

  const activeStations = segment.stations.filter((s) => !s.isTotal);
  const segmentTotalHc = activeStations.reduce((sum, s) => sum + (s.dlOnline ?? s.laborHc ?? 0), 0);
  const segmentBottleneck = activeStations.reduce((lowest, s) => {
    const next = s.uph ?? 0;
    if (lowest === 0) return next;
    if (next === 0) return lowest;
    return Math.min(lowest, next);
  }, 0);

  const sid = segment.id;

  /** Returns a <tr> with the fixed label column + only the visible virtual station cells. */
  const renderRow = (label: string, cellContent: (s: StationSnapshot) => ReactNode, tdClassName?: string) => (
    <tr key={label}>
      <th style={{ width: LABEL_COL_W, minWidth: LABEL_COL_W }}>{label}</th>
      {padLeft > 0 && <td style={{ padding: 0, width: padLeft, minWidth: padLeft }} aria-hidden />}
      {virtualCols.map((vc) => {
        const s = segment.stations[vc.index];
        return <td key={`${s.id}-${label}`} className={tdClassName}>{cellContent(s)}</td>;
      })}
      {padRight > 0 && <td style={{ padding: 0, width: padRight, minWidth: padRight }} aria-hidden />}
    </tr>
  );

  return (
    <section className="segment-matrix-card">
      <div className="segment-matrix-header">
        <label>
          <span>Segment Name</span>
          <input value={segment.name} onChange={(e) => updateL6SegmentName(sid, e.target.value)} />
        </label>
        <div className="inline-actions">
          <button type="button" className="button ghost" onClick={() => addL6Station(sid)}>Add Station</button>
          <button type="button" className="button ghost" onClick={() => deleteL6Segment(sid)} disabled={totalSegments === 1}>Delete Segment</button>
        </div>
      </div>
      <div ref={scrollRef} className="data-table-wrapper sticky-header tall-table mt-md" style={{ overflowX: 'auto' }}>
        <table className="data-table matrix-table" style={{ tableLayout: 'fixed', width: tableWidth }}>
          <thead>
            <tr>
              <th style={{ width: LABEL_COL_W, minWidth: LABEL_COL_W }}>Item</th>
              {padLeft > 0 && <th style={{ padding: 0, width: padLeft }} aria-hidden />}
              {virtualCols.map((vc) => {
                const s = segment.stations[vc.index];
                return <th key={s.id} style={{ width: STATION_COL_W, minWidth: STATION_COL_W }}>{s.name || `Station ${s.stationNo ?? ''}`}</th>;
              })}
              {padRight > 0 && <th style={{ padding: 0, width: padRight }} aria-hidden />}
            </tr>
          </thead>
          <tbody>
            {renderRow('Station', (s) => <input value={s.name} onChange={(e) => updateL6Station(sid, s.id, { name: e.target.value })} />)}
            {renderRow('No', (s) => <input type="number" value={s.stationNo ?? ''} onChange={(e) => updateL6Station(sid, s.id, { stationNo: e.target.value === '' ? undefined : numberValue(e.target.value) })} />)}
            {renderRow('HC', (s) => <input type="number" value={s.laborHc} onChange={(e) => updateL6Station(sid, s.id, { laborHc: numberValue(e.target.value) })} />)}
            {renderRow('Fixture', (s) => <input type="number" value={s.parallelStations} onChange={(e) => updateL6Station(sid, s.id, { parallelStations: numberValue(e.target.value) })} />)}
            {renderRow('Cycle Time', (s) => <input type="number" value={s.cycleTimeSec ?? 0} onChange={(e) => updateL6Station(sid, s.id, { cycleTimeSec: numberValue(e.target.value) })} />)}
            {renderRow('Allowance Rate', (s) => <input type="number" step="0.01" value={s.allowanceRate ?? ''} onChange={(e) => updateL6Station(sid, s.id, { allowanceRate: e.target.value === '' ? null : numberValue(e.target.value) })} />)}
            {renderRow('Std Man Hour', (s) => <input type="number" step="0.01" value={s.stdManHours ?? ''} onChange={(e) => updateL6Station(sid, s.id, { stdManHours: e.target.value === '' ? null : numberValue(e.target.value) })} />)}
            {renderRow('VPY', (s) => <input type="number" step="0.01" value={s.vpy ?? ''} onChange={(e) => updateL6Station(sid, s.id, { vpy: e.target.value === '' ? null : numberValue(e.target.value) })} />)}
            {renderRow('Shift Rate', (s) => <input type="number" step="0.01" value={s.shiftRate ?? ''} onChange={(e) => updateL6Station(sid, s.id, { shiftRate: e.target.value === '' ? null : numberValue(e.target.value) })} />)}
            {renderRow('DL Online', (s) => <input type="number" value={s.dlOnline ?? ''} onChange={(e) => updateL6Station(sid, s.id, { dlOnline: e.target.value === '' ? null : numberValue(e.target.value) })} />)}
            {renderRow('Avg CT', (s) => (s.avgCycleTimeSec ?? 0).toFixed(2), 'matrix-readonly-cell')}
            {renderRow('UPH', (s) => (s.uph ?? 0).toFixed(2), 'matrix-readonly-cell')}
            {renderRow('Action', (s) => <button type="button" className="button ghost" onClick={() => deleteL6Station(sid, s.id)}>Delete</button>)}
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
}, areSegmentMatrixPropsEqual);

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

  // Stable mutation handlers — useCallback ensures SegmentMatrix's memo
  // comparator never sees callback reference churn as "changed" props.
  const updateL6SegmentName = useCallback((segmentId: string, name: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, name } : segment)),
    ));
  }, [updateProject]);
  const addL6Segment = useCallback(() => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      return syncL6Segments(current, [
        ...baseSegments,
        { id: `seg-${Date.now()}`, name: `Segment ${baseSegments.length + 1}`, stations: [{ id: `l6-${Date.now()}`, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }] },
      ]);
    });
  }, [updateProject]);
  const deleteL6Segment = useCallback((segmentId: string) => {
    updateProject((current) => {
      const baseSegments = buildFallbackL6Segments(current);
      const nextSegments = baseSegments.filter((segment) => segment.id !== segmentId);
      if (nextSegments.length === 0) {
        return syncL6Segments(current, [{ id: 'seg-1', name: current.productL6.sku || current.productL6.pn || 'Segment 1', stations: [] }]);
      }
      return syncL6Segments(current, nextSegments);
    });
  }, [updateProject]);
  const updateL6Station = useCallback((segmentId: string, id: string, patch: Partial<Omit<L6Station, 'id'>>) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId
        ? { ...segment, stations: segment.stations.map((station) => (station.id === id ? { ...station, ...patch } : station)) }
        : segment)),
    ));
  }, [updateProject]);
  const addL6Station = useCallback((segmentId: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => {
        if (segment.id !== segmentId) return segment;
        const maxStationNo = segment.stations.reduce((max, station) => Math.max(max, station.stationNo ?? 0), 0);
        return { ...segment, stations: [...segment.stations, { id: `l6-${Date.now()}`, stationNo: maxStationNo + 1, name: 'New Process', laborHc: 0, parallelStations: 1, cycleTimeSec: 0, allowanceRate: 0 }] };
      }),
    ));
  }, [updateProject]);
  const deleteL6Station = useCallback((segmentId: string, id: string) => {
    updateProject((current) => syncL6Segments(
      current,
      buildFallbackL6Segments(current).map((segment) => (segment.id === segmentId ? { ...segment, stations: segment.stations.filter((station) => station.id !== id) } : segment)),
    ));
  }, [updateProject]);
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
          {l6SegmentSnapshots.map((segment) => (
            <SegmentMatrix
              key={segment.id}
              segment={segment}
              totalSegments={l6SegmentSnapshots.length}
              updateL6SegmentName={updateL6SegmentName}
              addL6Station={addL6Station}
              deleteL6Segment={deleteL6Segment}
              updateL6Station={updateL6Station}
              deleteL6Station={deleteL6Station}
            />
          ))}
        </div>
      </SectionCard>
    </div>
  );
});
