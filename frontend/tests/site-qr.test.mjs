import assert from 'node:assert/strict'
import test from 'node:test'
import {createRequire} from 'node:module'
import {build} from 'esbuild'
import {printedQrError, qrImageUrl} from '../src/siteQr.js'

const printed = 'https://app.easy-parking.mn/checkout/565a3ddb-1c01-4201-ad1c-81fc449cbdd7'
// The browser-only toast provider is not exercised by static rendering.
globalThis.window = {}
const bundle = await build({stdin:{contents:`
  import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
  import SiteQrModal from './src/pages/settings/SiteQrModal.jsx';
  import SiteEditModal from './src/pages/settings/SiteEditModal.jsx';
  export const renderQr = props => renderToStaticMarkup(<SiteQrModal {...props}/>);
  export const renderEdit = props => renderToStaticMarkup(<SiteEditModal {...props}/>);
`,resolveDir:process.cwd(),loader:'jsx'},bundle:true,write:false,platform:'node',format:'cjs',
  jsx:'automatic',packages:'external',logLevel:'silent'})
const compiled={exports:{}}
new Function('require','module','exports',bundle.outputFiles[0].text)(createRequire(import.meta.url),compiled,compiled.exports)
const {renderQr,renderEdit}=compiled.exports

test('one printed URL stays intact; duplicate and malformed routes are rejected',()=>{
  for(const valid of [printed,printed.replace('/checkout/','/check-cost/'),'https://app.easy-parking.mn/pay?site=SPORT','']) {
    assert.equal(printedQrError(valid),'')
  }
  for(const bad of [printed+printed,printed+'\n'+printed,'javascript:alert(1)',
    'https://app.easy-parking.mn/pay?site=SPORT&site=KH',printed+'#wrong',
    'https://app.easy-parking.mn/pay?site=',printed+'/extra']) assert.ok(printedQrError(bad))
})

test('bad persisted URL shows actionable error and cannot be copied or downloaded',()=>{
  const html=renderQr({qrSite:{name:'Sport',site_code:'SPORT',pay_url:printed+printed}})
  assert.match(html,/role="alert"/)
  assert.match(html,/Зогсоолын «Засах»/)
  assert.doesNotMatch(html,/<img|<a /)
  assert.match(html,/<button[^>]*disabled=""[^>]*aria-label="Хуулах"/)
})

test('valid modal shows the printed URL and cache revision on preview and download',()=>{
  const html=renderQr({qrSite:{name:'Sport',site_code:'SPORT',pay_url:printed}})
  assert.match(html,/<code[^>]*>https:\/\/app.easy-parking.mn\/checkout\/565a3ddb/)
  assert.match(html,/<img /)
  assert.ok(html.includes(qrImageUrl('SPORT',printed).replaceAll('&','&amp;')))
  assert.notEqual(qrImageUrl('SPORT',printed),qrImageUrl('SPORT',printed+printed))
  assert.notEqual(qrImageUrl('SPORT',printed,0),qrImageUrl('SPORT',printed,1))
})

test('invalid edit exposes inline accessible error, opens its section and disables saving',()=>{
  const props={editing:{name:'Sport',site_code:'SPORT',qr_url:printed+printed},templates:[],tenants:[],setEditing:()=>{}}
  const html=renderEdit(props)
  assert.match(html,/aria-invalid="true" aria-describedby="printed-qr-error"/)
  assert.match(html,/id="printed-qr-error" role="alert"/)
  assert.match(html,/<details[^>]*open=""/)
  assert.match(html,/<button disabled=""[^>]*>Хадгалах<\/button>/)
  const valid=renderEdit({...props,editing:{...props.editing,qr_url:printed}})
  assert.doesNotMatch(valid,/id="printed-qr-error"/)
  assert.match(valid,/aria-invalid="false"/)
})
