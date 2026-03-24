import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import { parseL10MpmSetupCsv, parseL10LaborTimeEstimationCsv } from '../domain/importers';
import type { MvaResults, ProjectState, SimulationResults } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue } from '../utils/formatters';

function syncL10TestTime(
  current: ProjectState['productL10']['testTime'],
  patch: Partial<ProjectState['productL10']['testTime']>,
) {
  const handlingSec = patch.handlingSec ?? current.handlingSec;
  const functionSec = patch.functionSec ?? current.functionSec;
  return { ...current, ...patch, handlingSec, functionSec, totalSec: handlingSec + functionSec };
}

interface MpmL10PageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  l10Mva: MvaResults;
  l10Simulation: SimulationResults;
  onStatusMessage: (msg: string) => void;
}

export const MpmL10Page = memo(function MpmL10Page({ project, updateProject, l10Mva, l10Simulation, onStatusMessage }: MpmL10PageProps) {
  const importL10MpmSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseL10MpmSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        productL10: {
          ...current.productL10,
          ...parsed,
          testTime: syncL10TestTime(current.productL10.testTime, parsed.testTime ?? {}),
        },
      }));
      onStatusMessage(`Imported L10 MPM setup CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import L10 MPM setup CSV.');
      console.error(error);
    }
  };
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

  return (
    <div className="stack-xl">
      <SectionCard title="MPM Report Setup (L10)" description="Legacy-aligned L10 product setup, schedule, test time, and volume assumptions.">
        <div className="action-grid compact-actions">
          <label className="upload-card"><span>Import L10 MPM Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10MpmSetup(event.target.files?.[0] ?? null)} /></label>
          <label className="upload-card"><span>Import L10 Labor Estimation CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importL10LaborEstimation(event.target.files?.[0] ?? null)} /></label>
        </div>
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Product Information</h3></div>
            <div className="form-grid cols-2">
              <label><span>BU</span><input value={project.productL10.bu} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, bu: event.target.value } }))} /></label>
              <label><span>Customer</span><input value={project.productL10.customer} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, customer: event.target.value } }))} /></label>
              <label><span>Project Name</span><input value={project.productL10.projectName} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, projectName: event.target.value } }))} /></label>
              <label><span>Yield (FPY)</span><input type="number" step="0.01" value={project.productL10.yield ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, yield: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Product Dimension</h3></div>
            <div className="form-grid cols-2">
              <label><span>Size</span><input value={project.productL10.sizeMm} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, sizeMm: event.target.value } }))} /></label>
              <label><span>Weight (Kg/Tool)</span><input type="number" value={project.productL10.weightKgPerTool ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, weightKgPerTool: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Test Time Setup</h3></div>
            <div className="form-grid cols-2">
              <label><span>Handling (sec)</span><input type="number" value={project.productL10.testTime.handlingSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { handlingSec: numberValue(event.target.value) }) } }))} /></label>
              <label><span>Function (sec)</span><input type="number" value={project.productL10.testTime.functionSec} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, testTime: syncL10TestTime(current.productL10.testTime, { functionSec: numberValue(event.target.value) }) } }))} /></label>
              <label className="full-span"><span>Total (sec)</span><input readOnly value={project.productL10.testTime.totalSec} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Volume and Lifecycle</h3></div>
            <div className="form-grid cols-2">
              <label><span>RFQ Qty / Month</span><input type="number" value={project.productL10.rfqQtyPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, rfqQtyPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Probe Life Cycle</span><input type="number" value={project.productL10.probeLifeCycle ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, probeLifeCycle: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label className="full-span"><span>RFQ Qty / Life Cycle</span><input type="number" value={project.productL10.rfqQtyLifeCycle ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, rfqQtyLifeCycle: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard full-span-card">
            <div className="legacy-subcard-header"><h3>Working Schedule</h3></div>
            <div className="form-grid cols-4">
              <label><span>Working Day (Year)</span><input type="number" value={project.productL10.workDaysPerYear ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerYear: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Working Day (Month)</span><input type="number" value={project.productL10.workDaysPerMonth ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerMonth: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Working Day (Week)</span><input type="number" value={project.productL10.workDaysPerWeek ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, workDaysPerWeek: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label><span>Shift</span><input type="number" value={project.productL10.shiftsPerDay ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, shiftsPerDay: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
              <label className="full-span"><span>Working Hour / Shift</span><input type="number" value={project.productL10.hoursPerShift ?? ''} onChange={(event) => updateProject((current) => ({ ...current, productL10: { ...current.productL10, hoursPerShift: event.target.value === '' ? null : numberValue(event.target.value) } }))} /></label>
            </div>
          </section>
        </div>
      </SectionCard>

      <SectionCard title="Volume and Yield Strategy" description="Legacy L10 volume mode and yield usage controls.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Volume</h3></div>
            <div className="form-grid cols-2">
              <label><span>Demand Mode</span><select value={project.plant.mvaVolume.demandMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, demandMode: event.target.value as 'monthly' | 'weekly' } } }))}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></label>
              <label><span>Work Days / Month</span><input type="number" value={project.plant.mvaVolume.workDaysPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>RFQ Qty / Month</span><input type="number" value={project.plant.mvaVolume.rfqQtyPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, rfqQtyPerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>Weekly Demand</span><input type="number" value={project.plant.mvaVolume.weeklyDemand} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, weeklyDemand: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Yield / FPY / VPY</h3></div>
            <div className="form-grid cols-2">
              <label><span>Strategy</span><select value={project.plant.yield.strategy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, strategy: event.target.value as 'ignore' | 'apply_to_capacity' } } }))}><option value="ignore">Ignore</option><option value="apply_to_capacity">Apply to capacity</option></select></label>
              <label><span>Use L10 FPY</span><select value={project.plant.yield.useL10Fpy ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, useL10Fpy: event.target.value === 'yes' } } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
              <label><span>FPY</span><input type="number" step="0.01" value={project.plant.yield.fpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, fpy: numberValue(event.target.value) } } }))} /></label>
              <label><span>VPY</span><input type="number" step="0.01" value={project.plant.yield.vpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, vpy: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>
          <section className="legacy-subcard full-span-card">
            <div className="legacy-subcard-header"><h3>Derived Output</h3></div>
            <div className="form-grid cols-4">
              <label><span>UPH</span><input readOnly value={project.productL10.uph ?? l10Mva.lineUphUsedForCapacity.toFixed(2)} /></label>
              <label><span>CT Sec</span><input readOnly value={project.productL10.ctSec ?? l10Simulation.taktTime.toFixed(2)} /></label>
              <label><span>Monthly Volume Used</span><input readOnly value={l10Mva.monthlyVolume.toFixed(2)} /></label>
              <label><span>Yield Factor</span><input readOnly value={l10Mva.yield.yieldFactor.toFixed(4)} /></label>
            </div>
          </section>
        </div>
      </SectionCard>
    </div>
  );
});
