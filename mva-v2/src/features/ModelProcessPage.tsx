import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { ProjectState } from '../domain/models';
import { numberValue } from '../utils/formatters';

interface Props {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
}

const sideBadgeClass = (side: string) => {
  if (side === 'Top') return 'side-badge top';
  if (side === 'Bottom') return 'side-badge bottom';
  return 'side-badge neutral';
};

export const ModelProcessPage = memo(function ModelProcessPage({ project, updateProject }: Props) {
  return (
    <div className="stack-xl">
      <SectionCard
        title="Model Process"
        description="Editable process routing matching the legacy model-process worksheet."
        actions={
          <button
            type="button"
            className="button ghost"
            onClick={() =>
              updateProject((current) => ({
                ...current,
                processSteps: [
                  ...current.processSteps,
                  { step: current.processSteps.length + 1, process: '', side: 'N/A' },
                ],
              }))
            }
          >
            Add Step
          </button>
        }
      >
        <div className="data-table-wrapper sticky-header tall-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>Seq</th>
                <th>Process / Machine Group</th>
                <th>Side</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {project.processSteps.map((row, index) => (
                <tr key={`${row.step}-${index}`}>
                  <td>
                    <input
                      type="number"
                      value={row.step}
                      onChange={(event) =>
                        updateProject((current) => ({
                          ...current,
                          processSteps: current.processSteps.map((r, i) =>
                            i === index
                              ? { ...r, step: Math.max(1, numberValue(event.target.value)) }
                              : r,
                          ),
                        }))
                      }
                      placeholder="Seq"
                    />
                  </td>
                  <td>
                    <input
                      value={row.process}
                      onChange={(event) =>
                        updateProject((current) => ({
                          ...current,
                          processSteps: current.processSteps.map((r, i) =>
                            i === index ? { ...r, process: event.target.value } : r,
                          ),
                        }))
                      }
                      placeholder="Process / Machine Group"
                    />
                  </td>
                  <td>
                    <div className="process-side-cell">
                      <select
                        value={row.side}
                        onChange={(event) =>
                          updateProject((current) => ({
                            ...current,
                            processSteps: current.processSteps.map((r, i) =>
                              i === index ? { ...r, side: event.target.value } : r,
                            ),
                          }))
                        }
                      >
                        <option value="Top">Top</option>
                        <option value="Bottom">Bottom</option>
                        <option value="N/A">N/A</option>
                      </select>
                      <span className={sideBadgeClass(row.side)}>{row.side}</span>
                    </div>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() =>
                        updateProject((current) => ({
                          ...current,
                          processSteps: current.processSteps
                            .filter((_, i) => i !== index)
                            .map((r, i) => ({ ...r, step: i + 1 })),
                        }))
                      }
                    >
                      Delete
                    </button>
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
