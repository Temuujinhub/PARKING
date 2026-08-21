import { useState, type FormEvent } from 'react';

interface LoginResult {
  token: string;
  username: string;
  role: string;
}

interface Props {
  onLogin: (result: LoginResult) => void;
}

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]     = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (data.ok) {
        sessionStorage.setItem('anpr_token', data.token);
        sessionStorage.setItem('anpr_user', JSON.stringify({ username: data.username, role: data.role, permissions: data.permissions }));
        onLogin(data);
      } else {
        setError(data.error || 'Нэвтрэх амжилтгүй боллоо');
      }
    } catch {
      setError('Серверт холбогдохгүй байна');
    } finally {
      setLoading(false);
    }
  }

  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('anpr_theme') === 'light' ? 'light' : 'dark'));
  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('anpr_theme', next);
  }

  return (
    <div className="login-overlay">
      <button
        className="theme-toggle-btn"
        onClick={toggleTheme}
        title={theme === 'dark' ? 'Цагаан горим' : 'Хар горим'}
        style={{ position: 'absolute', top: 16, right: 16 }}
      >
        {theme === 'dark' ? '☀' : '🌙'}
      </button>
      <div className="login-box">
        <img src="/Easy-Parking-Logo-04.webp" alt="Easy Parking" className="login-logo-img" />
        <form onSubmit={handleSubmit} className="login-form">
          <div className="login-field">
            <label>Нэвтрэх нэр</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="admin"
              autoFocus
              autoComplete="username"
            />
          </div>
          <div className="login-field">
            <label>Нууц үг</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
            />
          </div>
          {error && <div className="login-error">{error}</div>}
          <button type="submit" className="login-btn" disabled={loading || !username || !password}>
            {loading ? 'Нэвтэрч байна…' : 'Нэвтрэх'}
          </button>
        </form>
      </div>
    </div>
  );
}
