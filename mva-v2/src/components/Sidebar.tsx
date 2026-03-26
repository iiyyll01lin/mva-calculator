import { useRef, useState } from 'react';
import type { TabId } from '../domain/models';

const tabGroups: Array<{
  heading: string;
  tabs: Array<{ id: TabId; label: string; blurb: string }>;
}> = [
  {
    heading: 'Simulation',
    tabs: [
      { id: 'basic', label: 'Basic Information', blurb: 'Plan, shifts, utilization, and OEE' },
      { id: 'machine_rates', label: 'Machine Rates', blurb: 'Machine-group throughput assumptions' },
      { id: 'bom_map', label: 'BOM Mapping', blurb: 'Top and bottom side package allocation' },
      { id: 'model_process', label: 'Model Process', blurb: 'Editable manufacturing routing steps' },
      { id: 'simulation_results', label: 'Simulation Results', blurb: 'UPH, takt, bottleneck, and balance' },
    ],
  },
  {
    heading: 'Plant Inputs',
    tabs: [
      { id: 'mva_plant_rates', label: 'Labor Rate & Efficiency', blurb: 'Labor, yield, material, SGA, profit, ICC' },
      { id: 'mva_plant_env', label: 'Environment & Equipment Rate', blurb: 'Overhead, building, and power assumptions' },
      { id: 'mva_plant_equipment', label: 'Equipment List', blurb: 'Baseline, template, delta, and extra equipment' },
      { id: 'mva_plant_space', label: 'Space Setup', blurb: 'Matrix allocation, 40/60 split, and space cost' },
      { id: 'mva_plant_dloh_idl', label: 'DLOH-L & IDL Setup', blurb: 'Direct and indirect labor tables' },
    ],
  },
  {
    heading: 'MPM Report Setup',
    tabs: [
      { id: 'mpm_l10', label: 'MPM Setup (L10)', blurb: 'L10 product metadata and imports' },
      { id: 'mpm_l6', label: 'MPM Setup (L6)', blurb: 'L6 product metadata and imports' },
    ],
  },
  {
    heading: 'Labor Time Estimation',
    tabs: [
      { id: 'mva_labor_l10', label: 'Labor Time (L10)', blurb: 'L10 station table and capacity' },
      { id: 'mva_labor_l6', label: 'Labor Time (L6)', blurb: 'L6 segment and station estimates' },
    ],
  },
  {
    heading: 'MVA Summary',
    tabs: [
      { id: 'mva_summary_l10', label: 'Summary (L10)', blurb: 'L10 summary, review gate, and export' },
      { id: 'mva_summary_l6', label: 'Summary (L6)', blurb: 'L6 summary, review gate, and export' },
    ],
  },
];

interface SidebarProps {
  activeTab: TabId;
  onSelect: (tab: TabId) => void;
}

export function Sidebar({ activeTab, onSelect }: SidebarProps) {
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const isResizing = useRef(false);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startWidth = sidebarWidth;

    // Apply global drag polish: lock cursor and inhibit text selection
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      const delta = ev.clientX - startX;
      setSidebarWidth(Math.min(500, Math.max(200, startWidth + delta)));
    };

    const onMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };

  return (
    <aside
      className="sidebar"
      style={{ '--sidebar-width': `${sidebarWidth}px` } as React.CSSProperties}
    >
      <div className="brand-block">
        <p className="eyebrow">MVA v2</p>
        <h1 className="brand-title">StreamWeaver</h1>
        <p className="author-line">Author: Jason YY Lin</p>
        <p className="muted">Modular manufacturing simulation and cost engineering workspace.</p>
      </div>
      <nav className="nav-stack" aria-label="Primary">
        {tabGroups.map((group) => (
          <section key={group.heading} className="nav-group">
            <p className="nav-group-heading">{group.heading}</p>
            <div className="nav-group-list">
              {group.tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={tab.id === activeTab ? 'nav-button active' : 'nav-button'}
                  onClick={() => onSelect(tab.id)}
                >
                  <span>{tab.label}</span>
                  <small>{tab.blurb}</small>
                </button>
              ))}
            </div>
          </section>
        ))}
      </nav>
      {/* Drag handle — sits on the right edge of the sidebar */}
      <div
        className="sidebar-resizer"
        onMouseDown={handleMouseDown}
        role="separator"
        aria-label="Resize sidebar"
        aria-orientation="vertical"
      />
    </aside>
  );
}
