import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { ProjectState } from '../domain/models';
import { numberValue } from '../utils/formatters';

interface Props {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
}

export const MachineRatesPage = memo(function MachineRatesPage({ project, updateProject }: Props) {
  return (
    <div className="stack-xl">
      <SectionCard
        title="Machine Rates"
        description="Rate edits feed the simulation and downstream MVA calculations immediately."
      >
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Group</th>
                <th>Description</th>
                <th>Rate / Hr</th>
              </tr>
            </thead>
            <tbody>
              {project.machines.map((machine) => (
                <tr key={machine.id}>
                  <td>{machine.group}</td>
                  <td>{machine.description}</td>
                  <td>
                    <input
                      aria-label={`Rate ${machine.group}`}
                      type="number"
                      value={machine.rate}
                      onChange={(event) =>
                        updateProject((current) => ({
                          ...current,
                          machines: current.machines.map((item) =>
                            item.id === machine.id
                              ? { ...item, rate: numberValue(event.target.value) }
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
