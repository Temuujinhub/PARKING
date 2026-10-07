// Local-only, synthetic UI fixture. No request is forwarded to a real backend.
import React from 'react'
import {createRoot} from 'react-dom/client'
import Drivers from '../src/pages/Drivers.jsx'
import {AuthProvider} from '../src/auth.jsx'
import {ToastHost} from '../src/components/ui.jsx'
import '../src/index.css'
localStorage.setItem('parking_token','synthetic-local-preview')
const base={full_name:'ХБИ',company:'Туршилт',contract_type:'SPECIAL',site_name:'Бүх зогсоол',access_scope:'site',
  valid_from:'2026-08-07T00:00:00',valid_to:'2027-08-07T00:00:00',is_active:false}
const rows=[{...base,id:'one',plate_number:'1111УБА'}, {...base,id:'two',plate_number:'2222УБА'},
  {...base,id:'expired',plate_number:'3333УБА',valid_to:'2026-01-01T00:00:00',valid_from:'2025-01-01T00:00:00'},
  {...base,id:'duplicate',plate_number:'4444УБА',note:'Давхардсан жишээ'},
  {...base,id:'active',plate_number:'5555УБА',is_active:true}]
window.fetch=async(path,options={})=>{
  const u=new URL(path,location.origin)
  let value
  if(u.pathname==='/api/auth/me') value={user:{role:'ADMIN',username:'synthetic'},permissions:['drivers']}
  else if(u.pathname==='/api/admin/sites') value=[]
  else if(u.pathname==='/api/admin/drivers/duplicates') value=[]
  else if(u.pathname==='/api/admin/drivers/companies') value=[{company:'Туршилт',count:rows.length}]
  else if(u.pathname==='/api/admin/driver-type/rules') value={effective_from:'21:00',effective_until:'08:00'}
  else if(u.pathname==='/api/admin/drivers' && (!options.method||options.method==='GET')) {
    value=rows.filter(r=>(!u.searchParams.has('is_active')||r.is_active===(u.searchParams.get('is_active')==='true'))
      && (!u.searchParams.get('q')||r.full_name.includes(u.searchParams.get('q'))||r.plate_number.includes(u.searchParams.get('q'))))
  } else if(u.pathname==='/api/admin/drivers/bulk-status') {
    const body=JSON.parse(options.body)
    const items=rows.filter(r=>body.ids.includes(r.id)).map(r=>({id:r.id,plate_number:r.plate_number,
      reason:r.is_active===body.is_active?'unchanged':!body.is_active?'change':r.id==='expired'?'expired':r.id==='duplicate'?'active_duplicate':'change'}))
    const change_count=items.filter(r=>r.reason==='change').length
    value={items,change_count,changed:change_count,unchanged_count:items.filter(r=>r.reason==='unchanged').length,
      blocked_count:items.filter(r=>!['change','unchanged'].includes(r.reason)).length,is_active:body.is_active,preview_token:'a'.repeat(64)}
    if(!body.dry_run) rows.forEach(r=>{if(items.some(item=>item.id===r.id&&item.reason==='change'))r.is_active=body.is_active})
  } else throw new Error('Fixture rejects request: '+u.pathname)
  return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
}
createRoot(document.getElementById('root')).render(<AuthProvider><main className="p-5 space-y-4"><p className="text-sm text-slate-300">Локал туршилт · зөвхөн зохиомол бүртгэл</p><Drivers /><ToastHost /></main></AuthProvider>)
