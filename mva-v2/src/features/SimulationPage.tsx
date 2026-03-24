import { memo, useMemo } from 'react';
import { KpiCard } from '../components/KpiCard';
import { SectionCard } from '../components/SectionCard';
import type { SimulationResults } from '../domain/models';

function sideBadgeClass(side: string) {
  if (side === 'Top') return 'side-badge top';
  if (side === 'Bottom') return 'side-badge bottom';
  return 'side-badge neutral';
}

interface SimulationPageProps {
  demandWeekly: number;
  simulation: SimulationResults;
}

export const SimulationPage = memo(function SimulationPage({ demandWeekly, simulation }: SimulationPageProps) {
  const bottleneckStep = useMemo(
    () => simulation.steps.find((step) => step.isBottleneck) ?? null,
    [simulation.steps],
  );
  const chartCeiling = useMemo(
    () => Math.max(...simulation.steps.map((step) => step.cycleTime), simulation.taktTime, 1),
    [simulation.steps, simulation.taktTime],
  );

  return (
    <div className="stack-xl">
      <section className="kpi-grid" data-testid="kpi-grid">
        <KpiCard label="System UPH" value={simulation.uph.toFixed(2)} />
        <KpiCard label="Takt Time" value={`${simulation.taktTime.toFixed(2)} s`} />
        <KpiCard label="Weekly Capacity" value={`${simulation.weeklyOutput.toFixed(2)} / ${demandWeekly}`} tone={simulation.weeklyOutput >= demandWeekly ? 'good' : 'warn'} />
        <KpiCard label="Line Balance" value={`${simulation.lineBalanceEfficiency.toFixed(2)}%`} />
      </section>

      {bottleneckStep ? (
        <section className="alert-card warn">
          <div>
            <p className="eyebrow">Bottleneck Detected</p>
            <strong>{bottleneckStep.process} ({bottleneckStep.side})</strong>
          </div>
          <p>Cycle time {bottleneckStep.cycleTime.toFixed(2)} s, {((bottleneckStep.cycleTime / Math.max(simulation.taktTime, 0.01)) * 100).toFixed(1)}% of takt.</p>
        </section>
      ) : null}

      <SectionCard title="Cycle Time Analysis" description="CT and bottleneck mountain view with takt reference aligned to the legacy simulation page.">
        <div className="simulation-chart-card">
          <div className="chart-legend">
            <span><i className="legend-dot bar-normal" />Cycle time</span>
            <span><i className="legend-dot bar-bottleneck" />Bottleneck</span>
            <span><i className="legend-line" />Takt time</span>
          </div>
          <div className="mountain-chart" aria-label="CT and bottleneck mountain chart">
            <div className="takt-reference" style={{ bottom: `${(simulation.taktTime / chartCeiling) * 100}%` }}>
              <span>Takt {simulation.taktTime.toFixed(2)} s</span>
            </div>
            {simulation.steps.map((step) => (
              <div className="mountain-bar-group" key={`${step.step}-${step.process}`} title={`Step ${step.step} ${step.process} ${step.side} CT ${step.cycleTime.toFixed(2)} s Util ${step.utilization.toFixed(2)}% Components ${step.componentCount}`}>
                <div className={`mountain-bar ${step.isBottleneck ? 'is-bottleneck' : ''}`} style={{ height: `${(step.cycleTime / chartCeiling) * 100}%` }} />
                <span className="mountain-bar-value">{step.cycleTime.toFixed(1)}</span>
                <span className="mountain-bar-label">{step.step}</span>
              </div>
            ))}
          </div>
          <div className="mountain-axis-labels">
            {simulation.steps.map((step) => (
              <div key={`${step.step}-${step.process}-label`} className="mountain-axis-item">
                <span>{step.process}</span>
                <span className={sideBadgeClass(step.side)}>{step.side}</span>
              </div>
            ))}
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Simulation Results" description="Raw CT, losses, final CT, and utilization by process step.">
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr><th>Step</th><th>Process</th><th>Side</th><th>Raw CT (s)</th><th>OEE Loss + Setup</th><th>Final CT (s)</th><th>Utilization</th></tr>
            </thead>
            <tbody>
              {simulation.steps.map((step) => (
                <tr key={`${step.step}-${step.process}`} className={step.isBottleneck ? 'highlight-row' : ''}>
                  <td>{step.step}</td>
                  <td>{step.process}</td>
                  <td><span className={sideBadgeClass(step.side)}>{step.side}</span></td>
                  <td>{step.rawCycleTime.toFixed(2)}</td>
                  <td>{(step.cycleTime - step.rawCycleTime).toFixed(2)}</td>
                  <td>{step.cycleTime.toFixed(2)}</td>
                  <td>
                    <div className="utilization-cell">
                      <span>{step.utilization.toFixed(2)}%</span>
                      <div className="progress-track"><div className={`progress-fill ${step.isBottleneck ? 'warn' : ''}`} style={{ width: `${Math.min(step.utilization, 100)}%` }} /></div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
});
