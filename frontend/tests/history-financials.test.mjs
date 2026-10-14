import assert from 'node:assert/strict'
import test from 'node:test'
import {createRequire} from 'node:module'
import {build} from 'esbuild'

const bundle = await build({stdin: {contents: `
  import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
  import {MemoryRouter} from 'react-router-dom';
  import {ExitReadCell, HistoryPaymentCell, SessionDebtCell} from './src/components/HistoryFinancials.jsx';
  import {Badge} from './src/components/ui.jsx';
  export function render(name, props) { const component = {ExitReadCell, HistoryPaymentCell, SessionDebtCell, Badge}[name];
    return renderToStaticMarkup(<MemoryRouter>{React.createElement(component, props)}</MemoryRouter>); }
`, resolveDir: process.cwd(), loader: 'jsx'}, bundle: true, write: false, platform: 'node',
  format: 'cjs', jsx: 'automatic', tsconfigRaw: {}, packages: 'external', logLevel: 'silent'})
const compiled = {exports: {}}
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const render = compiled.exports.render
const payment = {id:'payment-1', session_id:'new-stay', provider:'QPAY', amount:30000,
  paid_at:'2026-09-29T10:43:49', allocation:{known:true, parking_amount:5000, previous_debt_amount:25000}}

test('closed status never claims a missing camera read', () => {
  assert.match(render('Badge', {value:'MANUAL_CLOSED'}), /Хаасан/)
  assert.doesNotMatch(render('Badge', {value:'MANUAL_CLOSED'}), /Гарах уншилтгүй/)
  assert.match(render('ExitReadCell', {session:{exit_read_status:'CAMERA_READ'}}), /Гарах камерт уншигдсан/)
  assert.match(render('ExitReadCell', {session:{exit_read_status:'NO_CAMERA_READ'}}), /Гарах камерын уншилт бүртгэгдээгүй/)
  assert.match(render('ExitReadCell', {session:{exit_device_id:'manual-camera'}}), /уншилт баталгаажаагүй/)
  assert.match(render('ExitReadCell', {session:{exit_read_status:'RECORDED_EXIT'}}), /Гарах цаг бүртгэлтэй/)
})
test('old paid debt links to exact later stay and payment', () => {
  const html = render('SessionDebtCell', {session:{session_debts:[{id:'debt-1', amount:25000,
    status:'PAID', paid_at:payment.paid_at, payment}]}})
  assert.match(html, /href="\/history\?session_id=new-stay&amp;payment_id=payment-1"/)
  assert.match(html, /25,000₮ өрийг/)
  assert.match(html, /QPay QR/)
  assert.match(html, /aria-label="[^"]*Холбогдох төлбөрийн задаргаа харах/)
})
test('pending and cancelled debt never link or claim payment', () => {
  for (const status of ['PENDING','CANCELLED']) {
    const html = render('SessionDebtCell', {session:{session_debts:[{id:'debt-1', amount:25000, status, payment}]}})
    assert.doesNotMatch(html, /href=|өрийг|QPay/)
    assert.match(html, status==='PENDING' ? /Төлөгдөөгүй/ : /Цуцалсан/)
  }
})
test('bank total and invoice allocations are visibly separate', () => {
  const html = render('HistoryPaymentCell', {session:{payments:[payment]}, selectedPaymentId:payment.id})
  assert.match(html, /<details[^>]*open=""/)
  assert.match(html, /<summary[^>]*>[\s\S]*Зогсолт 5,000₮ \+ өмнөх өр 25,000₮[\s\S]*<\/summary>/)
  assert.match(html, /Нийт гүйлгээ<\/dt><dd>30,000₮/)
})
test('legacy data is labelled unknown rather than treating all money as current parking', () => {
  const html = render('HistoryPaymentCell', {session:{payments:[{...payment, allocation:{known:false}}]}})
  assert.match(html, /Баталгаатай задаргаа хадгалагдаагүй/)
  assert.doesNotMatch(html, /<dt>Энэ зогсолт/)
})
test('unavailable cross-site or standalone payment cannot produce a broken stay link', () => {
  for (const p of [null, {...payment, session_id:null}]) {
    const html = render('SessionDebtCell', {session:{session_debts:[{id:'one',amount:25000,status:'PAID',paid_at:payment.paid_at,payment:p}]}})
    assert.match(html, /төлсөн/)
    assert.doesNotMatch(html, /href=/)
  }
})
test('untrusted provider text is escaped by React', () => {
  assert.doesNotMatch(render('HistoryPaymentCell', {session:{payments:[{...payment,provider:'<script>alert(1)</script>'}]}}), /<script>/)
})
