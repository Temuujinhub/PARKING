import { useEffect, useState } from 'react';
import { format } from 'date-fns';

type Tab = 'complaints' | 'anpr' | 'activity';
interface Props { token: string }

interface ComplaintsReport {
  total: number;
  byDept: { operations: number; finance: number; system: number };
  byStatus: { open: number; in_progress: number; resolved: number };
  resolvedCount: number;
  avgResolutionMinutes: number | null;
}
interface AnprReport {
  total: number;
  byParkingLot: { name: string; count: number }[];
  byDay: { date: string; count: number }[];
  byDirection: { entering: number; exiting: number; unknown: number };
}
interface ActivityEvent { at: string; user: string; action: 'open' | 'close'; cam: string; reason: string; ok: boolean }
interface ActivityReport {
  total: number;
  byUser: { user: string; opens: number; closes: number }[];
  events: ActivityEvent[];
}

const TAB_LABELS: Record<Tab, string> = {
  complaints: 'Гомдлын статистик',
  anpr:       'ANPR эвент',
  activity:   'Ажиллагсдын лог',
};
const DEPT_LABELS: Record<string, string> = { operations: 'Үйл ажиллагаа', finance: 'Санхүү', system: 'Систем' };
const STATUS_LABELS: Record<string, string> = { open: 'Шинэ', in_progress: 'Хийгдэж буй', resolved: 'Шийдсэн' };

function formatMinutes(mins: number): string {
  const days = Math.floor(mins / 1440);
  const hours = Math.floor((mins % 1440) / 60);
  const rem = Math.round(mins % 60);
  if (days > 0) return `${days} өдөр ${hours} цаг`;
  if (hours > 0) return `${hours} цаг ${rem} мин`;
  return `${rem} мин`;
}

function todayStr() { return format(new Date(), 'yyyy-MM-dd'); }
function daysAgoStr(n: number) { return format(new Date(Date.now() - n * 86400000), 'yyyy-MM-dd'); }

const statBox: React.CSSProperties = {
  background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8,
  padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 4, minWidth: 140,
};
const statLabel: React.CSSProperties = { fontSize: 12, color: 'var(--text-muted)' };
const statValue: React.CSSProperties = { fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' };

export default function ReportsPage({ token }: Props) {
  const [tab, setTab] = useState<Tab>('complaints');
  const [from, setFrom] = useState(daysAgoStr(30));
  const [to, setTo] = useState(todayStr());
  const [loading, setLoading] = useState(false);
  const [complaintsData, setComplaintsData] = useState<ComplaintsReport | null>(null);
  const [anprData, setAnprData] = useState<AnprReport | null>(null);
  const [activityData, setActivityData] = useState<ActivityReport | null>(null);
  const H = { 'X-Auth-Token': token };

  async function load() {
    setLoading(true);
    const qs = `?from=${from}&to=${to}`;
    try {
      if (tab === 'complaints') {
        const r = await fetch(`/api/reports/complaints${qs}`, { headers: H });
        if (r.ok) setComplaintsData(await r.json());
      } else if (tab === 'anpr') {
        const r = await fetch(`/api/reports/anpr${qs}`, { headers: H });
        if (r.ok) setAnprData(await r.json());
      } else {
        const r = await fetch(`/api/reports/activity${qs}`, { headers: H });
        if (r.ok) setActivityData(await r.json());
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [tab]);

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)',
    borderRadius: 6, padding: '6px 10px', fontSize: 13, outline: 'none',
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Эхлэх:</label>
        <input type="date" value={from} max={to} onChange={e => setFrom(e.target.value)} style={inputStyle} />
        <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Дуусах:</label>
        <input type="date" value={to} min={from} max={todayStr()} onChange={e => setTo(e.target.value)} style={inputStyle} />
        <button className="btn btn-primary" style={{ fontSize: 13, padding: '6px 16px' }} onClick={load} disabled={loading}>
          {loading ? 'Ачааллаж байна…' : 'Шинэчлэх'}
        </button>
      </div>

      <div className="admin-panel-tabs" style={{ marginBottom: 16 }}>
        {(Object.keys(TAB_LABELS) as Tab[]).map(t => (
          <button key={t} className={`admin-tab-btn${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === 'complaints' && (
        <div>
          {!complaintsData && <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>{loading ? 'Ачааллаж байна…' : 'Өгөгдөл байхгүй'}</div>}
          {complaintsData && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <div style={statBox}><span style={statLabel}>Нийт гомдол</span><span style={statValue}>{complaintsData.total}</span></div>
                <div style={statBox}><span style={statLabel}>Шийдсэн</span><span style={{ ...statValue, color: 'var(--accent-green)' }}>{complaintsData.resolvedCount}</span></div>
                <div style={statBox}>
                  <span style={statLabel}>Дундаж шийдсэн хугацаа</span>
                  <span style={statValue}>{complaintsData.avgResolutionMinutes !== null ? formatMinutes(complaintsData.avgResolutionMinutes) : '—'}</span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Хариуцагч хэлтсээр</div>
                  <table className="detail-table">
                    <tbody>
                      {Object.entries(complaintsData.byDept).map(([k, v]) => (
                        <tr key={k}><td>{DEPT_LABELS[k] ?? k}</td><td>{v}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Төлөвөөр</div>
                  <table className="detail-table">
                    <tbody>
                      {Object.entries(complaintsData.byStatus).map(([k, v]) => (
                        <tr key={k}><td>{STATUS_LABELS[k] ?? k}</td><td>{v}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'anpr' && (
        <div>
          {!anprData && <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>{loading ? 'Ачааллаж байна…' : 'Өгөгдөл байхгүй'}</div>}
          {anprData && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <div style={statBox}><span style={statLabel}>Нийт эвент</span><span style={statValue}>{anprData.total}</span></div>
                <div style={statBox}><span style={statLabel}>Орсон</span><span style={{ ...statValue, color: 'var(--accent-blue)' }}>{anprData.byDirection.entering}</span></div>
                <div style={statBox}><span style={statLabel}>Гарсан</span><span style={{ ...statValue, color: 'var(--accent-orange)' }}>{anprData.byDirection.exiting}</span></div>
              </div>

              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Зогсоолоор</div>
                  <table className="detail-table">
                    <tbody>
                      {anprData.byParkingLot.length === 0 && <tr><td colSpan={2}>—</td></tr>}
                      {anprData.byParkingLot.map(r => <tr key={r.name}><td>{r.name}</td><td>{r.count}</td></tr>)}
                    </tbody>
                  </table>
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Өдрөөр</div>
                  <table className="detail-table">
                    <tbody>
                      {anprData.byDay.length === 0 && <tr><td colSpan={2}>—</td></tr>}
                      {anprData.byDay.map(r => <tr key={r.date}><td>{r.date}</td><td>{r.count}</td></tr>)}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'activity' && (
        <div>
          {!activityData && <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>{loading ? 'Ачааллаж байна…' : 'Өгөгдөл байхгүй'}</div>}
          {activityData && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <div style={statBox}><span style={statLabel}>Нийт үйлдэл</span><span style={statValue}>{activityData.total}</span></div>
              </div>

              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Хэрэглэгчээр</div>
                <table className="detail-table">
                  <tbody>
                    {activityData.byUser.length === 0 && <tr><td colSpan={3}>—</td></tr>}
                    {activityData.byUser.map(u => (
                      <tr key={u.user}><td>{u.user}</td><td>Нээсэн: {u.opens}</td><td>Хаасан: {u.closes}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue-pale)', marginBottom: 8 }}>Дэлгэрэнгүй ({activityData.events.length})</div>
                <div className="table-wrapper" style={{ maxHeight: 360, overflowY: 'auto' }}>
                  <table>
                    <thead>
                      <tr><th>Цаг</th><th>Хэрэглэгч</th><th>Үйлдэл</th><th>Камер</th><th>Шалтгаан</th><th>Үр дүн</th></tr>
                    </thead>
                    <tbody>
                      {activityData.events.length === 0 && <tr><td colSpan={6} className="center-cell">Үйлдэл байхгүй байна</td></tr>}
                      {activityData.events.map((e, i) => (
                        <tr key={i}>
                          <td style={{ whiteSpace: 'nowrap', color: 'var(--text-faint)' }}>{e.at}</td>
                          <td>{e.user}</td>
                          <td style={{ color: e.action === 'open' ? 'var(--accent-green)' : 'var(--accent-red)' }}>{e.action === 'open' ? 'Нээсэн' : 'Хаасан'}</td>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{e.cam}</td>
                          <td>{e.reason}</td>
                          <td>{e.ok ? '✓' : '✕'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
