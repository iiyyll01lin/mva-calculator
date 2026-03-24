import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { ProjectState } from '../domain/models';
import { numberValue } from '../utils/formatters';

interface Props {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
}

export const BomMapPage = memo(function BomMapPage({ project, updateProject }: Props) {
  return (
    <div className="stack-xl">
      <SectionCard
        title="BOM Mapping"
        description="Package counts by side and assigned machine group."
      >
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Side</th>
                <th>Bucket</th>
                <th>Machine Group</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {project.bomMap.map((row) => (
                <tr key={row.id}>
                  <td>{row.side}</td>
                  <td>{row.bucket}</td>
                  <td>{row.machineGroup}</td>
                  <td>
                    <input
                      type="number"
                      value={row.count}
                      onChange={(event) =>
                        updateProject((current) => ({
                          ...current,
                          bomMap: current.bomMap.map((item) =>
                            item.id === row.id
                              ? { ...item, count: numberValue(event.target.value) }
                              : item,
                          ),
                        }))
                      }
                    />
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
