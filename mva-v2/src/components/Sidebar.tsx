import type { TabId } from '../domain/models';

const tabs: Array<{ id: TabId; label: string; blurb: string }> = [
  { id: 'dashboard', label: 'Dashboard', blurb: 'KPIs and release status' },
  { id: 'simulation', label: 'Simulation', blurb: 'Demand, rates, BOM, process' },
  { id: 'plant', label: 'Plant', blurb: 'Labor, overhead, equipment, space' },
  { id: 'product', label: 'Product', blurb: 'L10/L6 setup and standards' },
  { id: 'reports', label: 'Reports', blurb: 'Imports, exports, review gate' },
];

interface SidebarProps {
  activeTab: TabId;
  onSelect: (tab: TabId) => void;
}

export function Sidebar({ activeTab, onSelect }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <p className="eyebrow">MVA v2</p>
        <h1>StreamWeaver</h1>
        <p className="muted">Modular manufacturing simulation and cost engineering workspace.</p>
      </div>
      <nav className="nav-stack" aria-label="Primary">
        {tabs.map((tab) => (
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
      </nav>
    </aside>
  );
}
