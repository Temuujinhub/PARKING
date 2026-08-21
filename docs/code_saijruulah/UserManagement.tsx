import { useEffect, useState, Fragment } from 'react';

type Perm = { view: boolean; edit: boolean; delete: boolean; create: boolean };
type Perms = Record<string, Perm>;

interface User {
  id: number;
  username: string;
  email?: string;
  name?: string;
  role: string;
  permissions?: Perms;
  is_active: boolean;
  created_at: string;
}

interface Props {
  token: string;
  onClose: () => void;
  embedded?: boolean;
  canEdit?: boolean;
  canDelete?: boolean;
}

const MENUS: { key: string; label: string }[] = [
  { key: 'parking',    label: 'Зогсоол' },
  { key: 'complaints', label: 'Гомдол' },
  { key: 'users',      label: 'Хэрэглэгч' },
  { key: 'reasons',    label: 'Нээх шалтгаан' },
  { key: 'settings',   label: 'Тохиргоо' },
  { key: 'logs',       label: 'Лог' },
  { key: 'reports',    label: 'Тайлан' },
  { key: 'tunnels',    label: 'Салбар' },
];
const emptyPerms = (): Perms => Object.fromEntries(MENUS.map(m => [m.key, { view: false, edit: false, delete: false, create: false }]));
const withDefaults = (p?: Perms): Perms => ({ ...emptyPerms(), ...(p || {}) });
const ROLE_COLOR: Record<string, string> = { admin: 'var(--accent-blue)', manager: 'var(--accent-purple)', operator: 'var(--text-muted)' };

export default function UserManagement({ token, onClose, embedded, canEdit = true, canDelete = true }: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // New user form
  const [newUsername, setNewUsername] = useState('');
  const [newName, setNewName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<'admin' | 'operator' | 'manager'>('operator');
  const [newPerms, setNewPerms] = useState<Perms>(emptyPerms());
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');

  // Edit
  const [editId, setEditId] = useState<number | null>(null);
  const [editPw, setEditPw] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editName, setEditName] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editPerms, setEditPerms] = useState<Perms>(emptyPerms());

  const headers = { 'Content-Type': 'application/json', 'X-Auth-Token': token };

  async function loadUsers() {
    setLoading(true);
    try {
      const res = await fetch('/api/users', { headers });
      if (res.status === 401 || res.status === 403) { setError('Session дууссан байна — дахин нэвтэрнэ үү'); setLoading(false); return; }
      if (!res.ok) { setError('Хэрэглэгчдийг ачаалж чадсангүй'); setLoading(false); return; }
      setUsers(await res.json());
    } catch { setError('Сервертэй холбогдохгүй'); }
    finally { setLoading(false); }
  }

  useEffect(() => { loadUsers(); }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAdding(true); setAddError('');
    try {
      const res = await fetch('/api/users', {
        method: 'POST', headers,
        body: JSON.stringify({ username: newUsername, name: newName, email: newEmail, password: newPassword, role: newRole, permissions: newRole === 'manager' ? newPerms : undefined }),
      });
      const data = await res.json();
      if (data.ok) { setNewUsername(''); setNewName(''); setNewEmail(''); setNewPassword(''); setNewRole('operator'); setNewPerms(emptyPerms()); loadUsers(); }
      else setAddError(data.error || 'Алдаа гарлаа');
    } catch (e: unknown) { setAddError(e instanceof Error ? e.message : 'Алдаа гарлаа'); }
    finally { setAdding(false); }
  }

  async function handleDelete(id: number, username: string) {
    if (!confirm(`"${username}" хэрэглэгчийг устгах уу?`)) return;
    await fetch(`/api/users/${id}`, { method: 'DELETE', headers });
    loadUsers();
  }

  async function handleToggle(id: number) {
    await fetch(`/api/users/${id}/toggle`, { method: 'POST', headers });
    loadUsers();
  }

  function startEdit(u: User) {
    setEditId(u.id); setEditPw(''); setEditEmail(u.email ?? ''); setEditName(u.name ?? ''); setEditRole(u.role); setEditPerms(withDefaults(u.permissions));
  }

  async function handleUpdate(id: number) {
    await fetch(`/api/users/${id}`, {
      method: 'PUT', headers,
      body: JSON.stringify({
        ...(editPw ? { password: editPw } : {}),
        ...(editRole ? { role: editRole } : {}),
        email: editEmail,
        name: editName,
        ...(editRole === 'manager' ? { permissions: editPerms } : {}),
      }),
    });
    setEditId(null); setEditPw(''); setEditEmail(''); setEditName(''); setEditRole(''); setEditPerms(emptyPerms());
    loadUsers();
  }

  // Эрхийн checkbox матриц (функц — фокус алдагдахгүй)
  const permMatrix = (perms: Perms, setPerms: (p: Perms) => void) => {
    const toggle = (menu: string, action: keyof Perm) => {
      const cur = perms[menu] ?? { view: false, edit: false, delete: false, create: false };
      const next = { ...cur, [action]: !cur[action] };
      if ((action === 'edit' || action === 'delete') && next[action]) next.view = true;   // засах/устгах → харах автоматаар
      if (action === 'view' && !next.view) { next.edit = false; next.delete = false; }     // харах хаавал бусад нь ч хаагдана
      setPerms({ ...perms, [menu]: next });
    };
    const th: React.CSSProperties = { fontSize: 11, color: 'var(--text-muted)', padding: '4px 8px', textAlign: 'center', fontWeight: 600 };
    const td: React.CSSProperties = { padding: '4px 8px', textAlign: 'center' };
    return (
      <div style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', borderRadius: 6, padding: 10, marginTop: 8, maxWidth: 400 }}>
        <div style={{ fontSize: 12, color: 'var(--accent-purple)', marginBottom: 6 }}>Цэсний эрх (manager)</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>Цэс</th>
              <th style={th}>Харах</th>
              <th style={th}>Засах</th>
              <th style={th}>Устгах</th>
              <th style={th}>Бүртгэх</th>
            </tr>
          </thead>
          <tbody>
            {MENUS.map(m => {
              const p = perms[m.key] ?? { view: false, edit: false, delete: false, create: false };
              return (
                <tr key={m.key} style={{ borderTop: '1px solid var(--bg-elevated)' }}>
                  <td style={{ ...td, textAlign: 'left', color: 'var(--text-secondary)', fontSize: 12 }}>{m.label}</td>
                  {(['view', 'edit', 'delete'] as const).map(a => (
                    <td key={a} style={td}>
                      <input type="checkbox" checked={!!p[a]} onChange={() => toggle(m.key, a)} style={{ cursor: 'pointer', width: 15, height: 15 }} />
                    </td>
                  ))}
                  <td style={td}>
                    {m.key === 'complaints' ? (
                      <input type="checkbox" checked={!!p.create} onChange={() => toggle(m.key, 'create')} style={{ cursor: 'pointer', width: 15, height: 15 }} />
                    ) : <span style={{ color: 'var(--border)' }}>—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  const inner = (
    <>
      {!embedded && <button className="modal-close" onClick={onClose}>✕</button>}
      {!embedded && <h3 style={{ margin: '0 0 20px', color: 'var(--text-secondary)' }}>Хэрэглэгч удирдлага</h3>}

        {error && <div className="login-error" style={{ marginBottom: 16 }}>{error}</div>}

        {/* User list */}
        <div className="table-wrapper" style={{ marginBottom: 24 }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Нэвтрэх нэр</th>
                <th>Нэр</th>
                <th>Имэйл</th>
                <th>Эрх</th>
                <th>Төлөв</th>
                <th>Бүртгэсэн</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={8} className="center-cell">Ачааллаж байна…</td></tr>
              )}
              {!loading && users.map(u => (
                <Fragment key={u.id}>
                  <tr className="event-row" style={{ opacity: u.is_active === false ? 0.5 : 1 }}>
                    <td style={{ color: 'var(--text-faint)' }}>{u.id}</td>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{u.username}</td>
                    <td>
                      {editId === u.id ? (
                        <input type="text" value={editName} onChange={e => setEditName(e.target.value)} placeholder="нэр"
                          style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 4, padding: '2px 6px', width: 120, fontSize: 12 }} />
                      ) : (
                        <span style={{ fontSize: 12, color: u.name ? 'var(--text-secondary)' : 'var(--text-faint)' }}>{u.name || '—'}</span>
                      )}
                    </td>
                    <td>
                      {editId === u.id ? (
                        <input type="email" value={editEmail} onChange={e => setEditEmail(e.target.value)} placeholder="имэйл"
                          style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 4, padding: '2px 6px', width: 160, fontSize: 12 }} />
                      ) : (
                        <span style={{ fontSize: 12, color: u.email ? 'var(--text-secondary)' : 'var(--text-faint)' }}>{u.email || '—'}</span>
                      )}
                    </td>
                    <td>
                      {editId === u.id ? (
                        <select
                          value={editRole || u.role}
                          onChange={e => setEditRole(e.target.value)}
                          style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 4, padding: '2px 6px' }}
                        >
                          <option value="admin">admin</option>
                          <option value="manager">manager</option>
                          <option value="operator">operator</option>
                        </select>
                      ) : (
                        <span style={{ color: ROLE_COLOR[u.role] ?? 'var(--text-muted)', fontSize: 12 }}>{u.role}</span>
                      )}
                    </td>
                    <td>
                      <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 10,
                        background: u.is_active !== false ? 'var(--badge-green-bg)' : 'var(--badge-red-bg)',
                        color:      u.is_active !== false ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                        {u.is_active !== false ? 'Идэвхтэй' : 'Идэвхгүй'}
                      </span>
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                      {new Date(u.created_at).toLocaleDateString()}
                    </td>
                    <td style={{ display: 'flex', gap: 6 }}>
                      {editId === u.id ? (
                        <>
                          <input
                            type="password"
                            placeholder="Шинэ нууц үг"
                            value={editPw}
                            onChange={e => setEditPw(e.target.value)}
                            style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 4, padding: '3px 8px', width: 120, fontSize: 12 }}
                          />
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px' }} onClick={() => handleUpdate(u.id)}>Хадгалах</button>
                          <button className="btn" style={{ fontSize: 11, padding: '3px 10px', background: 'var(--bg-elevated)', color: 'var(--text-muted)' }} onClick={() => { setEditId(null); setEditPw(''); setEditEmail(''); setEditName(''); setEditRole(''); }}>Болих</button>
                        </>
                      ) : (
                        <>
                          {canEdit && <button className="btn" style={{ fontSize: 11, padding: '3px 10px', background: 'var(--bg-elevated)', color: 'var(--text-muted)' }} onClick={() => startEdit(u)}>Засах</button>}
                          {canEdit && <button className="btn" style={{ fontSize: 11, padding: '3px 10px', background: u.is_active !== false ? 'var(--badge-red-bg)' : 'var(--badge-green-bg2)', color: u.is_active !== false ? 'var(--accent-red)' : 'var(--accent-green)' }} onClick={() => handleToggle(u.id)}>
                            {u.is_active !== false ? 'Блоклох' : 'Нээх'}
                          </button>}
                          {canDelete && u.username !== 'admin' && <button className="btn btn-danger" style={{ fontSize: 11, padding: '3px 10px' }} onClick={() => handleDelete(u.id, u.username)}>Устгах</button>}
                        </>
                      )}
                    </td>
                  </tr>
                  {editId === u.id && (editRole || u.role) === 'manager' && (
                    <tr>
                      <td colSpan={8} style={{ paddingBottom: 12 }}>{permMatrix(editPerms, setEditPerms)}</td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>

        {/* Add user form */}
        {canEdit && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 20 }}>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>Шинэ хэрэглэгч нэмэх</div>
          <form onSubmit={handleAdd} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div className="login-field" style={{ flex: '1 1 120px' }}>
              <label>Нэвтрэх нэр</label>
              <input type="text" value={newUsername} onChange={e => setNewUsername(e.target.value)} placeholder="username" required />
            </div>
            <div className="login-field" style={{ flex: '1 1 140px' }}>
              <label>Нэр</label>
              <input type="text" value={newName} onChange={e => setNewName(e.target.value)} placeholder="Овог нэр" />
            </div>
            <div className="login-field" style={{ flex: '1 1 160px' }}>
              <label>Имэйл</label>
              <input type="email" value={newEmail} onChange={e => setNewEmail(e.target.value)} placeholder="name@easy-parking.mn" />
            </div>
            <div className="login-field" style={{ flex: '1 1 120px' }}>
              <label>Нууц үг</label>
              <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="password" required />
            </div>
            <div className="login-field" style={{ flex: '0 0 110px' }}>
              <label>Эрх</label>
              <select value={newRole} onChange={e => setNewRole(e.target.value as 'admin' | 'operator' | 'manager')}
                style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}>
                <option value="operator">operator</option>
                <option value="manager">manager</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <button type="submit" className="btn btn-primary" disabled={adding} style={{ alignSelf: 'flex-end', marginBottom: 0 }}>
              {adding ? '…' : '+ Нэмэх'}
            </button>
          </form>
          {newRole === 'manager' && permMatrix(newPerms, setNewPerms)}
          {addError && <div className="login-error" style={{ marginTop: 8 }}>{addError}</div>}
        </div>
        )}
    </>
  );
  if (embedded) return inner;
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        {inner}
      </div>
    </div>
  );
}
