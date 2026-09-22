// Local fixture only: no request leaves this browser, no real credentials.
import React from 'react'
import { createRoot } from 'react-dom/client'
import HospitalBenefitsPanel from '../src/pages/settings/HospitalBenefitsPanel.jsx'
import '../src/index.css'
let rows = []
const url = '/api/admin/hospital-integrations'
window.fetch = async (path, options = {}) => {
  let result
  if (path === '/api/admin/sites') result = [{ id: '00000000-0000-4000-8000-000000000001', name: 'Туршилтын зогсоол', site_code: 'TEST', is_active: true }]
  else if (path === url && (!options.method || options.method === 'GET')) result = { integrations: rows, can_leave_unassigned: true, encryption_ready: true }
  else if (path === url && options.method === 'POST') {
    result = { ...JSON.parse(options.body), id: '00000000-0000-4000-8000-000000000002', key_set: false, key_version: 0 }
    rows = [...rows, result]
  } else if (path.startsWith(url + '/') && options.method === 'PUT') {
    result = { ...rows[0], ...JSON.parse(options.body) }; rows = [result]
  } else return new Response(JSON.stringify({ detail: 'Fixture operation unavailable' }), { status: 400 })
  return new Response(JSON.stringify(result), { status: 200 })
}
createRoot(document.getElementById('root')).render(<main className="mx-auto max-w-5xl p-4 md:p-8">
  <div className="mb-4 flex justify-between gap-3"><p>Зөвхөн локал туршилтын өгөгдөл</p><button type="button" onClick={() => document.documentElement.classList.toggle('dark')}>Өдөр / шөнө</button></div>
  <HospitalBenefitsPanel />
</main>)
