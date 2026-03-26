import { memo, useMemo, useState } from 'react';
import { KpiCard } from '../components/KpiCard';
import { SectionCard } from '../components/SectionCard';
import { applyMagicWandPreview, computeMagicWand } from '../domain/ai-agent';
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
  const [whatIfActive, setWhatIfActive] = useState(false);

  const bottleneckStep = useMemo(
    () => simulation.steps.find((step) => step.isBottleneck) ?? null,
    [simulation.steps],
  );
  const chartCeiling = useMemo(
    () => Math.max(...simulation.steps.map((step) => step.cycleTime), simulation.taktTime, 1),
    [simulation.steps, simulation.taktTime],
  );
  const magicWand = useMemo(() => computeMagicWand(simulation), [simulation]);
  const whatIfSimulation = useMemo(
    () => (whatIfActive && magicWand ? applyMagicWandPreview(simulation, magicWand) : null),
    [whatIfActive, magicWand, simulation],
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

      {/* AI Recommended Adjustments ─────────────────────────────────── */}
      {magicWand && (
        <SectionCard
          title="✨ AI Recommended Adjustments"
          description="Automatically identified optimisation opportunity based on current bottleneck analysis."
        >
          <div className="ai-magic-wand-section">
            <div className="alert-card warn ai-bottleneck-highlight">
              <div>
                <p className="eyebrow">Bottleneck Identified</p>
                <strong>{magicWand.targetProcess} — Step {magicWand.targetStep}</strong>
              </div>
              <p>
                CT <strong>{magicWand.currentCycleTime.toFixed(2)} s</strong> is the line constraint.
                Adding 1 parallel station is projected to raise UPH by{' '}
                <strong>+{magicWand.uphGainPercent}%</strong>{' '}
                ({magicWand.currentUph.toFixed(2)} → {magicWand.projectedUph.toFixed(2)}).
              </p>
            </div>

            <div className="ai-magic-wand-actions">
              <button
                type="button"
                className={`button ${whatIfActive ? 'secondary' : 'primary'} ai-magic-wand-btn`}
                onClick={() => setWhatIfActive((v) => !v)}
                aria-pressed={whatIfActive}
              >
                {whatIfActive ? '↩ Reset Preview' : '✨ Magic Wand — Preview Impact'}
              </button>
              {whatIfActive && (
                <span className="ai-magic-wand-hint">
                  Showing projected results with 1× parallel station at{' '}
                  <strong>{magicWand.targetProcess}</strong>.
                </span>
              )}
            </div>

            {whatIfActive && whatIfSimulation && (
              <div className="ai-whatif-preview">
                <p className="eyebrow" style={{ marginBottom: '0.75rem' }}>What-If Preview</p>
                <div className="kpi-grid">
                  <div className="ai-whatif-kpi">
                    <span className="ai-whatif-label">UPH</span>
                    <span className="ai-whatif-before">{simulation.uph.toFixed(2)}</span>
                    <span className="ai-whatif-arrow" aria-hidden="true">→</span>
                    <span className="ai-whatif-after">{whatIfSimulation.uph.toFixed(2)}</span>
                    <span className="ai-gain-badge">+{magicWand.uphGainPercent}%</span>
                  </div>
                  <div className="ai-whatif-kpi">
                    <span className="ai-whatif-label">Line Balance</span>
                    <span className="ai-whatif-before">{simulation.lineBalanceEfficiency.toFixed(1)}%</span>
                    <span className="ai-whatif-arrow" aria-hidden="true">→</span>
                    <span className="ai-whatif-after">{whatIfSimulation.lineBalanceEfficiency.toFixed(1)}%</span>
                  </div>
                  <div className="ai-whatif-kpi">
                    <span className="ai-whatif-label">New Bottleneck</span>
                    <span className="ai-whatif-after">{whatIfSimulation.bottleneckProcess}</span>
                  </div>
                  <div className="ai-whatif-kpi">
                    <span className="ai-whatif-label">Weekly Output</span>
                    <span className="ai-whatif-before">{simulation.weeklyOutput.toFixed(0)}</span>
                    <span className="ai-whatif-arrow" aria-hidden="true">→</span>
                    <span className="ai-whatif-after">{whatIfSimulation.weeklyOutput.toFixed(0)}</span>
                  </div>
                </div>
                <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.8rem' }}>
                  To apply this change permanently, navigate to <strong>Machine Rates</strong> and
                  double the rate for <strong>{magicWand.targetProcess}</strong>.
                </p>
              </div>
            )}
          </div>
        </SectionCard>
      )}
    </div>
  );
});
