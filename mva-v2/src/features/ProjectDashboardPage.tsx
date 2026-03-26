import React, { memo, useId, useState } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { ProductLine, ProjectState, SkuType } from '../domain/models';

const PRODUCT_LINES: ProductLine[] = ['Server', 'Switch', 'Storage', 'Other'];
const SKU_OPTIONS: (SkuType | string)[] = [
  '1U-Standard',
  '1U-HighEnd',
  '2U-Standard',
  '2U-HighEnd',
  '4U-HighEnd',
  'Custom',
];

interface Props {
  portfolio: ProjectState[];
  onOpen: (projectId: string) => void;
  onCreateNew: (meta: { modelName: string; productLine: ProductLine; sku: SkuType | string }) => void;
  onDelete: (projectId: string) => void;
}

function formatDate(iso: string | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export const ProjectDashboardPage = memo(function ProjectDashboardPage({
  portfolio,
  onOpen,
  onCreateNew,
  onDelete,
}: Props) {
  const formId = useId();
  const [modelName, setModelName] = useState('New Project');
  const [productLine, setProductLine] = useState<ProductLine>('Server');
  const [sku, setSku] = useState<SkuType | string>('1U-Standard');
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const sortedPortfolio = [...portfolio].sort((a, b) => {
    const ta = a.lastModified ? new Date(a.lastModified).getTime() : 0;
    const tb = b.lastModified ? new Date(b.lastModified).getTime() : 0;
    return tb - ta; // most-recent first
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = modelName.trim();
    if (!trimmed) return;
    onCreateNew({ modelName: trimmed, productLine, sku });
  };

  return (
    <div className="app-shell" style={{ flexDirection: 'column' }}>
      <main
        className="content-shell"
        style={{ maxWidth: 960, margin: '0 auto', padding: '2rem 1.5rem' }}
      >
        {/* ── Dashboard header ───────────────────────────────────────────── */}
        <header className="hero-panel" style={{ marginBottom: '2rem' }}>
          <div className="hero-copy">
            <p className="eyebrow">Enterprise Portfolio</p>
            <h1>MVA Projects</h1>
            <p className="muted">
              Manage multiple MVA cost-model projects across product lines and SKUs.
            </p>
          </div>
        </header>

        {/* ── Create new project ─────────────────────────────────────────── */}
        <SectionCard
          title="Create New Project"
          description="Start a fresh MVA cost model for a specific product and SKU."
        >
          <form
            id={formId}
            onSubmit={handleCreate}
            className="action-grid"
            style={{ alignItems: 'flex-end' }}
          >
            <div className="field-group">
              <label htmlFor={`${formId}-name`}>Model / Project Name</label>
              <input
                id={`${formId}-name`}
                type="text"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder="e.g. 1395A3368201"
                required
                aria-label="Project Name"
              />
            </div>

            <div className="field-group">
              <label htmlFor={`${formId}-pl`}>Product Line</label>
              <select
                id={`${formId}-pl`}
                value={productLine}
                onChange={(e) => setProductLine(e.target.value as ProductLine)}
                aria-label="Product Line"
              >
                {PRODUCT_LINES.map((pl) => (
                  <option key={pl} value={pl}>
                    {pl}
                  </option>
                ))}
              </select>
            </div>

            <div className="field-group">
              <label htmlFor={`${formId}-sku`}>SKU</label>
              <select
                id={`${formId}-sku`}
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                aria-label="SKU"
              >
                {SKU_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            <button type="submit" className="button primary">
              Create Project
            </button>
          </form>
        </SectionCard>

        {/* ── Project list ───────────────────────────────────────────────── */}
        <SectionCard
          title="Existing Projects"
          description={`${portfolio.length} project${portfolio.length !== 1 ? 's' : ''} in portfolio — sorted by last modified.`}
        >
          {sortedPortfolio.length === 0 ? (
            <p className="muted">No projects yet. Create one above to get started.</p>
          ) : (
            <table
              className="data-table"
              style={{ width: '100%', borderCollapse: 'collapse' }}
              aria-label="Project Portfolio"
            >
              <thead>
                <tr>
                  <th scope="col">Model Name</th>
                  <th scope="col">Product Line</th>
                  <th scope="col">SKU</th>
                  <th scope="col">Last Modified</th>
                  <th scope="col" style={{ width: 200 }}>
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedPortfolio.map((proj) => (
                  <tr key={proj.projectId}>
                    <td>{proj.basicInfo.modelName}</td>
                    <td>{proj.productLine ?? '—'}</td>
                    <td>{proj.sku ?? '—'}</td>
                    <td>{formatDate(proj.lastModified)}</td>
                    <td>
                      <button
                        type="button"
                        className="button primary"
                        style={{ marginRight: '0.4rem' }}
                        onClick={() => proj.projectId && onOpen(proj.projectId)}
                      >
                        Open
                      </button>

                      {confirmDeleteId === proj.projectId ? (
                        <>
                          <button
                            type="button"
                            className="button secondary"
                            style={{ marginRight: '0.25rem' }}
                            onClick={() => {
                              if (proj.projectId) onDelete(proj.projectId);
                              setConfirmDeleteId(null);
                            }}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="button secondary"
                            onClick={() => setConfirmDeleteId(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => setConfirmDeleteId(proj.projectId ?? null)}
                          aria-label={`Delete ${proj.basicInfo.modelName}`}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SectionCard>
      </main>
    </div>
  );
});
