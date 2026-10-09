// In-memory disposable fixture. Never forwards requests to any backend.
import React, {useState} from 'react'
import {createRoot} from 'react-dom/client'
import Drivers from '../src/pages/Drivers.jsx'
import SiteLocationFields from '../src/pages/settings/SiteLocationFields.jsx'
import {AuthProvider} from '../src/auth.jsx'
import {ToastHost} from '../src/components/ui.jsx'
import '../src/index.css'

localStorage.setItem('parking_token','synthetic-local-preview')
const sites=[{id:'sport',name:'Туршилтын SPORT',site_code:'SPORT'}, {id:'other',name:'Туршилтын OTHER',site_code:'OTHER'}]
let rows=Array.from({length:205},(_,i)=>({id:String(i),plate_number:`${String(i).padStart(4,'0')}УБА`,
  full_name:`Зохиомол ${i}`,company:'Туршилт',contract_type:'CONTRACT',is_active:false,
  site_id:i<150?'sport':'other',site_name:i<150?sites[0].name:sites[1].name,
  valid_from:'2026-01-01T00:00:00',valid_to:'2027-01-01T00:00:00',access_scope:'site'}))
const counters={}
const filtered=f=>rows.filter(r=>(!f.site_id||r.site_id===f.site_id) && (!f.q||r.full_name.includes(f.q)||r.plate_number.includes(f.q))
  && (!f.company||r.company===f.company) && (!f.contract_type||r.contract_type===f.contract_type)
  && (f.is_active==null||f.is_active===''||r.is_active===(String(f.is_active)==='true')))
window.fetch=async(path,options={})=>{
  const u=new URL(path,location.origin)
  counters[u.pathname]=(counters[u.pathname]||0)+1
  document.getElementById('requests').textContent=JSON.stringify(counters)
  const body=options.body?JSON.parse(options.body):{}
  let value
  if(u.pathname==='/api/auth/me') value={user:{role:'ADMIN',username:'synthetic'},permissions:['drivers']}
  else if(u.pathname==='/api/admin/driver-type/rules') value={effective_from:'21:00',effective_until:'08:00'}
  else if(u.pathname==='/api/admin/drivers/options') value=sites
  else if(u.pathname==='/api/admin/drivers/page') {
    const set=filtered(Object.fromEntries(u.searchParams)),offset=Number(u.searchParams.get('cursor')||0)
    value={items:set.slice(offset,offset+100),total:set.length,next_cursor:offset+100<set.length?String(offset+100):null}
  } else if(u.pathname==='/api/admin/drivers/select-filtered') value={ids:filtered(body).map(r=>r.id)}
  else if(u.pathname==='/api/admin/drivers/company-search') value=[{company:'Туршилт',count:rows.length}]
  else if(u.pathname==='/api/admin/drivers/bulk-delete') {
    const set=rows.filter(r=>body.ids.includes(r.id))
    value={selected:set.length,items:set,preview_token:'a'.repeat(64),deleted:set.length}
    if(!body.dry_run) rows=rows.filter(r=>!body.ids.includes(r.id))
  } else if(u.pathname==='/api/admin/drivers/export') {
    const set=body.ids?rows.filter(r=>body.ids.includes(r.id)):filtered(body)
    document.getElementById('export-proof').textContent=`EXPORT_ROWS=${set.length}; MODE=${body.ids?'selected':'filtered'}`
    return new Response('synthetic fixture; XLSX is verified separately in backend tests')
  } else if(u.pathname==='/api/admin/drivers/duplicates') value=[]
  else throw new Error('Fixture rejected '+u.pathname)
  return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
}
function Demo(){
  const [location,setLocation]=useState({})
  return <AuthProvider><main className="p-5 space-y-4">
    <p>Локал туршилт — зөвхөн зохиомол өгөгдөл. Refresh хийхэд бүртгэлүүд сэргэнэ.</p>
    <details><summary>Сүлжээний хүсэлтийн тоо</summary><pre id="requests" /></details><p id="export-proof" />
    <Drivers /><section className="card"><SiteLocationFields value={location} onChange={setLocation} /></section><ToastHost />
  </main></AuthProvider>
}
createRoot(document.getElementById('root')).render(<Demo />)
