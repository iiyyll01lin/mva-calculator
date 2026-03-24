import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import { parseL6MpmSetupCsv, parseL6LaborTimeEstimationCsv } from '../domain/importers';
import type { ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue } from '../utils/formatters';

interface MpmL6PageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  onStatusMessage: (msg: string) => void;
}

export const MpmL6Page = memo(function MpmL6Page({ project, updateProject, onStatusMessage }: MpmL6PageProps) {
  const importL6MpmSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL6MpmSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        productL6: {
          ...current.productL6,
          ...parsed,
          routingTimesSec: parsed.routingTimesSec ?? current.productL6.routingTimesSec,
          testTime: { ...current.productL6.testTime, ...parsed.testTime },
        },
      }));
      onStatusMessage(`Imported L6 MPM setup CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import L6 MPM setup CSV.');
      console.error(error);
    }
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

  const addL6RoutingTime = () => {
    updateProject((current) => {
      const nextKey = `Process ${Object.keys(current.productL6.routingTimesSec).length + 1}`;
      return { ...current, productL6: { ...current.productL6, routingTimesSec: { ...current.productL6.routingTimesSec, [nextKey]: 0 } } };
    });
  };
  const updateL6RoutingTime = (key: string, patch: { nextKey?: string; value?: number }) => {
    updateProject((current) => {
      const entries = Object.entries(current.productL6.routingTimesSec);
      const nextEntries = entries.map(([entryKey, entryValue]) => {
        if (entryKey !== key) return [entryKey, entryValue] as const;
        return [patch.nextKey ?? entryKey, patch.value ?? entryValue] as const;
      });
      return { ...current, productL6: { ...current.productL6, routingTimesSec: Object.fromEntries(nextEntries.filter(([entryKey]) => entryKey.trim().length > 0)) } };
    });
  };
  const deleteL6RoutingTime = (key: string) => {
    updateProject((current) => ({
      ...current,
      productL6: { ...current.productL6, routingTimesSec: Object.fromEntries(Object.entries(current.productL6.routingTimesSec).filter(([entryKey]) => entryKey !== key)) },
    }));
  };

  return (
    <div className="stack-xl">
      <SectionCard title="MPM Report Setup (L6)" description="Legacy-aligned L6 by-product, header, routing, and test-time inputs.">
        <div className="action-grid compact-actions">
          <label className="upload-card"><span>Import L6 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6MpmSetup(event.target.files?.[0] ?? null)} /></label>
          <label className="upload-card"><span>Import L6 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL6LaborEstimation(event.target.files?.[0] ?? null)} /></label>
        </div>
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>By Product</h3></div>
            <div className="form-grid cols-2">
              <label><span>BU</span><input value={project.productL6.bu} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, bu: event.target.value } }))} /></label>
              <label><span>Customer</span><input value={project.productL6.customer} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, customer: event.target.value } }))} /></label>
              <label><span>Model Name</span><input value={project.productL6.modelName ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, modelName: event.target.value } }))} /></label>
              <label><span>PN</span><input value={project.productL6.pn} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pn: event.target.value } }))} /></label>
              <label><span>SKU</span><input value={project.productL6.sku ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, sku: event.target.value } }))} /></label>
              <label><span>PCB Params / Size</span><input value={project.productL6.pcbSize} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, pcbSize: event.target.value } }))} /></label>
              <label><span>Boards / Panel</span><input type="number" value={project.productL6.boardsPerPanel} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, boardsPerPanel: numberValue(event.target.value) } }))} /></label>
              <label><span>Printing</span><input value={project.laborTimeL6.header.printing ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, printing: event.target.value } } }))} /></label>
              <label><span>Line Change</span><input value={project.laborTimeL6.header.lineChange ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, lineChange: event.target.value } } }))} /></label>
              <label className="full-span"><span>ICT to FBT</span><input value={project.laborTimeL6.header.ictToFbt ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, ictToFbt: event.target.value } } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Header Parameters</h3></div>
            <div className="form-grid cols-2">
              <label><span>Category</span><input value={project.laborTimeL6.header.category ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, category: event.target.value } } }))} /></label>
              <label><span>Production Line</span><input value={project.laborTimeL6.header.productionLine ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, productionLine: event.target.value } } }))} /></label>
              <label><span>Annual Demand</span><input type="number" value={project.laborTimeL6.header.annualDemand ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, annualDemand: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Monthly Demand</span><input type="number" value={project.laborTimeL6.header.monthlyDemand ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, monthlyDemand: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Multi-Panel Qty</span><input type="number" value={project.laborTimeL6.header.multiPanelQty ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, multiPanelQty: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Line Capability / Shift</span><input type="number" value={project.laborTimeL6.header.lineCapabilityPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, lineCapabilityPerShift: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Ex-Rate RMB/USD</span><input type="number" step="0.01" value={project.laborTimeL6.header.exRateRmbUsd ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, exRateRmbUsd: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
              <label><span>Service Hourly Wage</span><input type="number" step="0.01" value={project.laborTimeL6.header.serviceHourlyWage ?? ''} onChange={(event) => updateProject((current) => ({ ...current, laborTimeL6: { ...current.laborTimeL6, header: { ...current.laborTimeL6.header, serviceHourlyWage: event.target.value === '' ? undefined : numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard full-span-card">
            <div className="legacy-subcard-header"><h3>Product Setup</h3></div>
            <div className="form-grid cols-4">
              <label><span>Side 1 Parts</span><input type="number" value={project.productL6.side1PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side1PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Side 2 Parts</span><input type="number" value={project.productL6.side2PartsCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, side2PartsCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Offline Blasting Part</span><input type="number" value={project.productL6.offLineBlastingPartCount ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, offLineBlastingPartCount: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>DIP Parts Type</span><input value={project.productL6.dipPartsType} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, dipPartsType: event.target.value } }))} /></label>
              <label><span>Selective WS</span><input value={project.productL6.selectiveWs} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, selectiveWs: event.target.value } }))} /></label>
              <label><span>Assembly Part</span><input value={project.productL6.assemblyPart} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, assemblyPart: event.target.value } }))} /></label>
              <label><span>RFQ Qty / Month</span><input type="number" value={project.productL6.rfqQtyPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, rfqQtyPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Handling Sec</span><input type="number" value={project.productL6.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, handlingSec: numberValue(event.target.value) } } }))} /></label>
              <label><span>Function Sec</span><input type="number" value={project.productL6.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, testTime: { ...current.productL6.testTime, functionSec: numberValue(event.target.value) } } }))} /></label>
              <label className="full-span"><span>Station Path</span><input value={project.productL6.stationPath} onChange={(event) => updateProject((current) => ({ ...current, productL6: { ...current.productL6, stationPath: event.target.value } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard full-span-card">
            <div className="legacy-subcard-header"><h3>Routing Time (sec)</h3><p className="muted">Per-station routing inputs shown explicitly like the legacy template.</p></div>
            <div className="editable-list">
              {Object.entries(project.productL6.routingTimesSec).map(([key, value]) => (
                <div className="list-row wide-4" key={key}>
                  <input value={key} onChange={(event) => updateL6RoutingTime(key, { nextKey: event.target.value, value })} placeholder="Routing Station" />
                  <input type="number" value={value} onChange={(event) => updateL6RoutingTime(key, { value: numberValue(event.target.value) })} placeholder="Seconds" />
                  <div className="matrix-readonly-cell">{value > 0 ? `${(value / 60).toFixed(2)} min` : '0.00 min'}</div>
                  <button type="button" className="button ghost" onClick={() => deleteL6RoutingTime(key)}>Delete</button>
                </div>
              ))}
              <button type="button" className="button secondary" onClick={addL6RoutingTime}>Add Routing Time Row</button>
            </div>
          </section>
        </div>
      </SectionCard>
    </div>
  );
});
