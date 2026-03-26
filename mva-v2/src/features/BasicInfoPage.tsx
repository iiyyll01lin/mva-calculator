import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { ProjectState } from '../domain/models';
import { boundedNumber, numberValue } from '../utils/formatters';

interface BasicInfoPageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
}

export const BasicInfoPage = memo(function BasicInfoPage({ project, updateProject }: BasicInfoPageProps) {
  return (
    <div className="stack-xl">
      <SectionCard title="Basic Information" description="Legacy planning inputs: model, demand, shifts, lot size, utilization, and OEE assumptions.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header">
              <h3>Production Planning</h3>
              <p className="muted">Model, planning days, and weekly demand.</p>
            </div>
            <div className="form-grid cols-2">
              <label className="full-span"><span>Model Name</span><input aria-label="Model Name" value={project.basicInfo.modelName} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, modelName: event.target.value } }))} /></label>
              <label><span>Work Days (Planning)</span><input type="number" min="0" value={project.basicInfo.workDays} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, workDays: Math.max(0, numberValue(event.target.value)) } }))} /></label>
              <label><span>Weekly Demand</span><input aria-label="Weekly Demand" type="number" min="0" value={project.basicInfo.demandWeekly} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, demandWeekly: Math.max(0, numberValue(event.target.value)) } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header">
              <h3>Shift Configuration</h3>
              <p className="muted">Shift count, working hours, and break assumptions.</p>
            </div>
            <div className="form-grid cols-2">
              <label><span>Shifts per Day</span><input aria-label="Shifts Per Day" type="number" value={project.basicInfo.shiftsPerDay} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, shiftsPerDay: numberValue(event.target.value) } }))} /></label>
              <label><span>Hours per Shift</span><input aria-label="Hours Per Shift" type="number" value={project.basicInfo.hoursPerShift} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hoursPerShift: numberValue(event.target.value) } }))} /></label>
              <label className="full-span"><span>Breaks per Shift</span><input type="number" value={project.basicInfo.breaksPerShiftMin} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, breaksPerShiftMin: numberValue(event.target.value) } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header">
              <h3>Efficiency and Quality</h3>
              <p className="muted">Utilization, first pass yield, and side-specific OEE assumptions.</p>
            </div>
            <div className="form-grid cols-2">
              <label><span>Target Utilization</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.targetUtilization} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, targetUtilization: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
              <label><span>First Pass Yield (FPY)</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.fpy} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, fpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
              <label><span>OEE - Placement</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeePlacement} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeePlacement: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
              <label><span>OEE - Others</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.oeeOthers} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, oeeOthers: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header">
              <h3>Batch Settings</h3>
              <p className="muted">Lot size, setup time, panelization, and build-side toggles.</p>
            </div>
            <div className="form-grid cols-2">
              <label><span>Lot Size</span><input type="number" min="0" value={project.basicInfo.lotSize} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, lotSize: Math.max(0, numberValue(event.target.value)) } }))} /></label>
              <label><span>Setup Time / Lot</span><input type="number" min="0" value={project.basicInfo.setupTimeMin} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, setupTimeMin: Math.max(0, numberValue(event.target.value)) } }))} /></label>
              <label><span>Boards per Panel</span><input type="number" min="0" value={project.basicInfo.boardsPerPanel} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, boardsPerPanel: Math.max(0, numberValue(event.target.value)) } }))} /></label>
              <label><span>Value Pass Yield (VPY)</span><input type="number" min="0" max="1" step="0.01" value={project.basicInfo.vpy} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, vpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } }))} /></label>
              <label><span>Top Side</span><select value={project.basicInfo.hasTopSide ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hasTopSide: event.target.value === 'yes' } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
              <label><span>Bottom Side</span><select value={project.basicInfo.hasBottomSide ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, basicInfo: { ...current.basicInfo, hasBottomSide: event.target.value === 'yes' } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
            </div>
          </section>
        </div>
      </SectionCard>
    </div>
  );
});
