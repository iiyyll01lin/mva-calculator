import { memo, useCallback, useState } from 'react';
import type { AiInsight } from '../domain/ai-agent';
import { runMockAiAnalysis } from '../domain/ai-agent';
import type { ProjectState, SimulationResults } from '../domain/models';

interface AiInsightsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  project: ProjectState;
  simulation: SimulationResults;
}

// ── Minimal inline Markdown renderer ──────────────────────────────────────
// Supports **bold** and \n\n paragraph breaks without dangerouslySetInnerHTML.
function BoldText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function MarkdownParagraphs({ text }: { text: string }) {
  const paragraphs = text.split(/\n\n+/);
  return (
    <>
      {paragraphs.map((para, i) => (
        <p key={i} className="ai-md-para">
          <BoldText text={para} />
        </p>
      ))}
    </>
  );
}

// ── Main panel component ───────────────────────────────────────────────────

export const AiInsightsPanel = memo(function AiInsightsPanel({
  isOpen,
  onClose,
  project,
  simulation,
}: AiInsightsPanelProps) {
  const [insight, setInsight] = useState<AiInsight | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleAnalyze = useCallback(() => {
    setIsLoading(true);
    // Brief delay to signal async work (mirrors a real LLM round-trip)
    setTimeout(() => {
      const result = runMockAiAnalysis(project, simulation);
      setInsight(result);
      setIsLoading(false);
    }, 700);
  }, [project, simulation]);

  const handleClose = useCallback(() => {
    onClose();
  }, [onClose]);

  if (!isOpen) return null;

  const displayName = insight?.generatedFor ?? 'Avery, Yeh';

  return (
    <div
      className="ai-panel-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="AI Insights Panel"
      onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
    >
      <aside className="ai-panel-drawer">
        {/* Header */}
        <div className="ai-panel-header">
          <div className="ai-panel-header-copy">
            <span className="eyebrow">AI Optimization Agent</span>
            <h2 className="ai-panel-title">✨ Insights for {displayName}</h2>
          </div>
          <button
            type="button"
            className="ai-panel-close"
            onClick={handleClose}
            aria-label="Close AI Insights panel"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="ai-panel-body">
          {/* Empty state */}
          {!insight && !isLoading && (
            <div className="ai-panel-empty">
              <div className="ai-panel-empty-icon" aria-hidden="true">🤖</div>
              <p className="ai-panel-empty-title">Ready to Analyze</p>
              <p className="muted">
                Click <strong>Analyze Now</strong> to run the optimization engine on your current
                simulation data. Results are cached — re-opening is instant unless data changes.
              </p>
              <button type="button" className="button primary" onClick={handleAnalyze}>
                ✨ Analyze Now
              </button>
            </div>
          )}

          {/* Loading */}
          {isLoading && (
            <div className="ai-panel-loading" aria-busy="true" aria-label="Analyzing…">
              <span className="page-loading-spinner" aria-hidden="true" />
              <p>Analyzing manufacturing parameters…</p>
            </div>
          )}

          {/* Results */}
          {insight && !isLoading && (
            <div className="ai-panel-results">

              {/* Executive Summary */}
              <section className="ai-section ai-section-summary">
                <h3 className="ai-section-heading">Executive Summary</h3>
                <div className="ai-summary-card">
                  <MarkdownParagraphs text={insight.executiveSummary} />
                </div>
              </section>

              {/* Top Bottlenecks */}
              <section className="ai-section">
                <h3 className="ai-section-heading">🎯 Top 2 Bottleneck Stations</h3>
                <ul className="ai-list">
                  {insight.topBottlenecks.map((item, i) => (
                    <li key={i} className="ai-list-item">
                      <span className="ai-list-rank">#{i + 1}</span>
                      <div className="ai-list-content">
                        <MarkdownParagraphs text={item} />
                      </div>
                    </li>
                  ))}
                  {insight.topBottlenecks.length === 0 && (
                    <li className="ai-list-item muted">No bottleneck data — line may have no active steps.</li>
                  )}
                </ul>
              </section>

              {/* Headcount recommendations */}
              <section className="ai-section">
                <h3 className="ai-section-heading">👥 Headcount Re-Allocation</h3>
                <ul className="ai-list">
                  {insight.headcountActions.map((action, i) => (
                    <li key={i} className="ai-list-item">
                      <span className="ai-list-rank ai-list-rank-action">→</span>
                      <div className="ai-list-content"><BoldText text={action} /></div>
                    </li>
                  ))}
                </ul>
              </section>

              {/* UPH prediction */}
              <section className="ai-section">
                <h3 className="ai-section-heading">📈 Projected UPH Improvement</h3>
                <div className="ai-prediction-card">
                  {insight.magicWand && (
                    <div className="ai-uph-kpi-row">
                      <div className="ai-uph-kpi">
                        <span className="ai-uph-label">Current UPH</span>
                        <span className="ai-uph-value">{insight.magicWand.currentUph.toFixed(2)}</span>
                      </div>
                      <span className="ai-uph-arrow" aria-hidden="true">→</span>
                      <div className="ai-uph-kpi ai-uph-kpi-projected">
                        <span className="ai-uph-label">Projected UPH</span>
                        <span className="ai-uph-value">
                          {insight.magicWand.projectedUph.toFixed(2)}
                          <span className="ai-gain-badge">+{insight.magicWand.uphGainPercent}%</span>
                        </span>
                      </div>
                    </div>
                  )}
                  <MarkdownParagraphs text={insight.uphPrediction} />
                </div>
              </section>

              {/* Footer meta */}
              <div className="ai-panel-meta-footer">
                <p className="muted">
                  Generated {new Date(insight.timestamp).toLocaleString()} &middot; Context hash:{' '}
                  <code className="ai-hash">{insight.contextHash}</code>
                </p>
                <button type="button" className="button secondary" onClick={handleAnalyze}>
                  ↻ Re-Analyze
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
});
