import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-dvh flex items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4">
        <div className="text-center mb-2">
          <div className="text-3xl font-bold"><span className="text-accent">P</span> Smart Parking</div>
          <div className="text-sm text-slate-500 mt-1">Удирдлагын системд нэвтрэх</div>
        </div>
        <div>
          <label className="label" htmlFor="u">Нэвтрэх нэр</label>
          <input id="u" className="input" value={username} onChange={(e) => setUsername(e.target.value)}
            autoComplete="username" required autoFocus />
        </div>
        <div>
          <label className="label" htmlFor="p">Нууц үг</label>
          <input id="p" type="password" className="input" value={password}
            onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </div>
        {error && <div role="alert" className="text-sm text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{error}</div>}
        <button className="btn-primary w-full justify-center" disabled={busy}>
          {busy ? 'Нэвтэрч байна…' : 'Нэвтрэх'}
        </button>
      </form>
    </div>
  )
}
