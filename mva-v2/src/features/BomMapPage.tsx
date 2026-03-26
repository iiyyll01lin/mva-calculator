import { memo, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { SectionCard } from '../components/SectionCard';
import type { ProjectState } from '../domain/models';
import { numberValue } from '../utils/formatters';

// Pixel height of a single BOM row (input inside td).
const ROW_HEIGHT = 44;
// Maximum visible height of the scrollable BOM table before virtualisation kicks in.
const MAX_TABLE_HEIGHT = 440;

interface Props {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
}

/**
 * BOM Mapping page with virtualised row rendering.
 *
 * For small datasets (< 10 rows) the container simply sizes to content;
 * for large datasets (5 000+ BOM lines) only the visible rows are mounted in
 * the DOM, reducing layout work from O(N) to O(visible).
 *
 * The "padding row" technique keeps browser scrollbar geometry accurate without
 * absolute-positioning hacks that break responsive table layout.
 */
export const BomMapPage = memo(function BomMapPage({ project, updateProject }: Props) {
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: project.bomMap.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();
  const totalHeight = rowVirtualizer.getTotalSize();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom = totalHeight - (virtualRows.length > 0 ? virtualRows[virtualRows.length - 1].end : 0);

  // Grow the container to fit small datasets; cap at MAX_TABLE_HEIGHT for large ones.
  const containerHeight = Math.min(project.bomMap.length * ROW_HEIGHT + 48, MAX_TABLE_HEIGHT);

  return (
    <div className="stack-xl">
      <SectionCard
        title="BOM Mapping"
        description="Package counts by side and assigned machine group."
      >
        <div
          ref={parentRef}
          className="data-table-wrapper"
          style={{ height: containerHeight, overflowY: 'auto' }}
        >
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
              {paddingTop > 0 && (
                <tr style={{ height: paddingTop }}>
                  <td colSpan={4} />
                </tr>
              )}
              {virtualRows.map((vRow) => {
                const row = project.bomMap[vRow.index];
                return (
                  <tr key={row.id} style={{ height: ROW_HEIGHT }}>
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
                );
              })}
              {paddingBottom > 0 && (
                <tr style={{ height: paddingBottom }}>
                  <td colSpan={4} />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
});
