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
  onOpenAiInsights: () => void;
}

const DISPLAY_NAME_FALLBACK = 'Avery, Yeh';

export function Sidebar({ activeTab, onSelect, onOpenAiInsights }: SidebarProps) {
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const isResizing = useRef(false);
  const displayName = sessionStorage.getItem('mva_display_name') ?? DISPLAY_NAME_FALLBACK;
  // Derive initials: "Avery, Yeh" → "AY"
  const initials = displayName
    .split(/[,\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase())
    .slice(0, 2)
    .join('');

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startWidth = sidebarWidth;

    // Lock cursor and inhibit text selection globally while dragging
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      setSidebarWidth(Math.min(500, Math.max(200, startWidth + (ev.clientX - startX))));
    };

    const onMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
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
      {/* AI Insights button */}
      <button
        type="button"
        className="ai-insights-trigger"
        onClick={onOpenAiInsights}
        aria-label="Open AI Optimization Insights"
      >
        <span className="ai-insights-trigger-icon" aria-hidden="true">✨</span>
        <span className="ai-insights-trigger-label">AI Insights</span>
      </button>

      {/* User profile block */}
      <div className="user-profile-block">
        <div className="user-avatar" aria-hidden="true">{initials}</div>
        <div className="user-profile-info">
          <p className="user-display-name">{displayName}</p>
          <p className="user-role-label">Engineer</p>
        </div>
      </div>

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
