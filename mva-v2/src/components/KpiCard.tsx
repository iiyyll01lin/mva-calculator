interface KpiCardProps {
  label: string;
  value: string;
  tone?: 'neutral' | 'good' | 'warn';
}

export function KpiCard({ label, value, tone = 'neutral' }: KpiCardProps) {
  return (
    <article className={`kpi-card ${tone}`}>
      <p className="kpi-label">{label}</p>
      <strong>{value}</strong>
    </article>
  );
}
