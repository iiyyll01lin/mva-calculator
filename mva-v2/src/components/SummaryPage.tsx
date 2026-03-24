import { memo } from 'react';
import { buildMvaXlsx, downloadBytes } from '../domain/exporters';
import type { MvaResults, ProjectState, SimulationResults } from '../domain/models';
import { SectionCard } from './SectionCard';

function toMoney(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value);
}

function formatTimestamp(): string {
  const now = new Date();
  const pad = (v: number) => String(v).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

interface SummaryPageProps {
  processLabel: 'L10' | 'L6';
  project: ProjectState;
  summaryMva: MvaResults;
  summarySimulation: SimulationResults;
  summaryText: string;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  /** The full Imports and Exports card rendered by the parent (App) */
  importsExportsSlot: React.ReactNode;
}

export const SummaryPage = memo(function SummaryPage({
  processLabel,
  project,
  summaryMva,
  summarySimulation,
  summaryText,
  updateProject,
  importsExportsSlot,
}: SummaryPageProps) {
  const overheadPerUnit =
    summaryMva.overhead.equipmentDepPerUnit +
    summaryMva.overhead.equipmentMaintPerUnit +
    summaryMva.overhead.spacePerUnit +
    summaryMva.overhead.powerPerUnit;

  return (
    <div className="stack-xl">
      <div className="summary-page-header">
        <div>
          <h2>Summary — {processLabel}</h2>
          <p className="muted">{project.basicInfo.modelName}</p>
        </div>
        <button
          type="button"
          className="button primary"
          onClick={() => {
            const bytes = buildMvaXlsx(processLabel, project, summaryMva, summarySimulation);
            downloadBytes(
              `${project.basicInfo.modelName}_${formatTimestamp()}_${processLabel.toLowerCase()}_mva.xlsx`,
              bytes,
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            );
          }}
        >
          Export MVA
        </button>
      </div>

      <SectionCard title={`Confirm (${processLabel})`} description="Decision, reviewer, and comment recorded before release.">
        <div className="form-grid cols-4">
          <div className="decision-group">
            <button
              type="button"
              className={project.confirmation.decision === 'OK' ? 'button decision-ok active' : 'button decision-ok'}
              onClick={() =>
                updateProject((current) => ({
                  ...current,
                  confirmation: { ...current.confirmation, decision: 'OK', decidedAt: new Date().toISOString() },
                }))
              }
            >
              OK
            </button>
            <button
              type="button"
              className={project.confirmation.decision === 'NG' ? 'button decision-ng active' : 'button decision-ng'}
              onClick={() =>
                updateProject((current) => ({
                  ...current,
                  confirmation: { ...current.confirmation, decision: 'NG', decidedAt: new Date().toISOString() },
                }))
              }
            >
              NG
            </button>
          </div>
          <label>
            <span>Current Status</span>
            <input readOnly value={project.confirmation.decision ?? 'Unreviewed'} />
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
          <label>
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

      <SectionCard title="Capacity Assumptions" description="Legacy summary assumptions for demand, schedule, and UPH used in costing.">
        <div className="form-grid cols-4 capacity-grid">
          <div className="summary-metric">
            <span>Monthly FCST</span>
            <strong>{summaryMva.monthlyVolume.toFixed(2)}</strong>
          </div>
          <div className="summary-metric">
            <span>Days / Month</span>
            <strong>{project.plant.mvaVolume.workDaysPerMonth}</strong>
          </div>
          <div className="summary-metric">
            <span>Shifts / Day</span>
            <strong>{project.basicInfo.shiftsPerDay}</strong>
          </div>
          <div className="summary-metric">
            <span>Hours / Shift</span>
            <strong>{project.basicInfo.hoursPerShift}</strong>
          </div>
          <div className="summary-metric">
            <span>Line UPH Raw</span>
            <strong>{summaryMva.lineUphRaw.toFixed(2)}</strong>
          </div>
          <div className="summary-metric">
            <span>Line UPH Used</span>
            <strong>{summaryMva.lineUphUsedForCapacity.toFixed(2)}</strong>
          </div>
          {processLabel === 'L6' ? (
            <div className="summary-metric summary-highlight">
              <span>Boards / Panel</span>
              <strong>{project.productL6.boardsPerPanel}</strong>
            </div>
          ) : null}
          {processLabel === 'L6' ? (
            <div className="summary-metric summary-highlight">
              <span>PCB Size</span>
              <strong>{project.productL6.pcbSize || '-'}</strong>
            </div>
          ) : null}
          {processLabel === 'L6' ? (
            <div className="summary-metric">
              <span>Parts (S1 / S2)</span>
              <strong>{`${project.productL6.side1PartsCount ?? 0} / ${project.productL6.side2PartsCount ?? 0}`}</strong>
            </div>
          ) : null}
        </div>
      </SectionCard>

      <SectionCard
        title="A. LABOR Cost"
        description={`DL Rate: ${toMoney(project.plant.mvaRates.directLaborHourlyRate)}/hr  ·  IDL Rate: ${toMoney(project.plant.mvaRates.indirectLaborHourlyRate)}/hr  ·  Efficiency: ${(project.plant.mvaRates.efficiency * 100).toFixed(1)}%`}
      >
        <div className="data-table-wrapper">
          <table className="data-table" data-testid={`labor-table-${processLabel.toLowerCase()}`}>
            <thead>
              <tr>
                <th colSpan={4} className="section-header">
                  Direct Labor
                </th>
              </tr>
              <tr>
                <th>Name</th>
                <th>HC</th>
                <th>Line UPH</th>
                <th>Cost / Unit</th>
              </tr>
            </thead>
            <tbody>
              {summaryMva.directLabor.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>{row.effectiveHeadcount}</td>
                  <td>{row.uphUsed}</td>
                  <td>{toMoney(row.costPerUnit)}</td>
                </tr>
              ))}
              <tr className="summary-row">
                <td>Total DL</td>
                <td>{summaryMva.directLabor.reduce((sum, row) => sum + row.effectiveHeadcount, 0).toFixed(2)}</td>
                <td>-</td>
                <td>{toMoney(summaryMva.directLabor.reduce((sum, row) => sum + row.costPerUnit, 0))}</td>
              </tr>
              <tr>
                <th colSpan={4} className="section-header">
                  Indirect Labor
                </th>
              </tr>
              <tr>
                <th>Name</th>
                <th>HC</th>
                <th>Line UPH</th>
                <th>Cost / Unit</th>
              </tr>
              {summaryMva.indirectLabor.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>{row.effectiveHeadcount}</td>
                  <td>{row.uphUsed}</td>
                  <td>{toMoney(row.costPerUnit)}</td>
                </tr>
              ))}
              <tr className="summary-row">
                <td>Total IDL</td>
                <td>{summaryMva.indirectLabor.reduce((sum, row) => sum + row.effectiveHeadcount, 0).toFixed(2)}</td>
                <td>-</td>
                <td>{toMoney(summaryMva.indirectLabor.reduce((sum, row) => sum + row.costPerUnit, 0))}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <div className="two-column-grid">
        <SectionCard title="D. Overhead" description="Equipment, space, and power costs per unit.">
          <ul className="plain-list">
            <li>Equip Dep / Unit: {toMoney(summaryMva.overhead.equipmentDepPerUnit)}</li>
            <li>Equip Maint / Unit: {toMoney(summaryMva.overhead.equipmentMaintPerUnit)}</li>
            <li>Space / Utility / Unit: {toMoney(summaryMva.overhead.spacePerUnit)}</li>
            <li>Power / Unit: {toMoney(summaryMva.overhead.powerPerUnit)}</li>
            <li>
              <strong>Total Overhead / Unit: {toMoney(overheadPerUnit)}</strong>
            </li>
          </ul>
        </SectionCard>
        <div className="stack-md">
          <SectionCard title="E. SG&A" description="Corporate, site, and IT security burden per unit.">
            <p>
              Total SG&amp;A / Unit: <strong>{toMoney(summaryMva.sga.totalSgaPerUnit)}</strong>
            </p>
          </SectionCard>
          <SectionCard title="MVA Total Cost / Unit" description="">
            <div className="cost-total-display" data-testid={`total-cost-${processLabel.toLowerCase()}`}>
              {toMoney(summaryMva.totalPerUnit)}
            </div>
          </SectionCard>
        </div>
      </div>

      <SectionCard title="Warnings" description="Release blockers are surfaced here before export.">
        <ul className="plain-list" data-testid={`warning-list-${processLabel.toLowerCase()}`}>
          {summaryMva.warnings.length === 0 ? (
            <li>No blocking warnings.</li>
          ) : (
            summaryMva.warnings.map((warning) => <li key={warning}>{warning}</li>)
          )}
        </ul>
      </SectionCard>

      {importsExportsSlot}

      <SectionCard title="Summary Preview" description="CSV preview for regression and manual audit.">
        <textarea
          data-testid={`summary-preview-${processLabel.toLowerCase()}`}
          readOnly
          value={summaryText}
          rows={14}
        />
      </SectionCard>
    </div>
  );
});
