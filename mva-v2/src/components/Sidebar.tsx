import { useCallback, useEffect, useRef, useState } from 'react';
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
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    startX.current = e.clientX;
    startWidth.current = sidebarWidth;
  }, [sidebarWidth]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = e.clientX - startX.current;
      const clamped = Math.min(500, Math.max(200, startWidth.current + delta));
      setSidebarWidth(clamped);
    };
    const handleMouseUp = () => { isDragging.current = false; };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  return (
    <aside className="sidebar" style={{ width: sidebarWidth }}>
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
