import { createContext, useContext, useEffect, useState } from 'react'
import { api, clearToken, getToken, setToken } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [permissions, setPermissions] = useState([])
  const [testMode, setTestMode] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!getToken()) { setLoading(false); return }
    api('/api/auth/me')
      .then((d) => { setUser(d.user); setPermissions(d.permissions); setTestMode(!!d.test_mode) })
      .catch(() => clearToken())
      .finally(() => setLoading(false))
  }, [])

  const login = async (username, password) => {
    const d = await api('/api/auth/login', { method: 'POST', form: { username, password } })
    setToken(d.access_token)
    setUser(d.user)
    setPermissions(d.permissions)
    setTestMode(!!d.test_mode)
    return d
  }
  const logout = () => { clearToken(); setUser(null); setPermissions([]) }
  const can = (module) => permissions.includes('*') || permissions.includes(module)

  return (
    <AuthContext.Provider value={{ user, permissions, loading, login, logout, can, testMode }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
