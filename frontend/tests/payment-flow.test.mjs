import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'
import { paymentOutcome } from '../src/paymentOutcome.js'

test('a paid invoice with a remaining balance never promises a gate opening', () => {
  const result = paymentOutcome({ status: 'PAID', amount_due: 600, barrier_command_status: 'SUCCESS' })
  assert.equal(result.kind, 'partial')
  assert.equal(result.needsBalanceRefresh, true)
  assert.match(result.message, /600/)
})

for (const status of ['PENDING', 'UNKNOWN', 'FAILED']) {
  test(`paid money with gate ${status} directs review without a second payment`, () => {
    const result = paymentOutcome({ amount_due: 0, session_status: 'CLOSED', barrier_command_status: status })
    assert.equal(result.kind, 'gate_attention')
    assert.match(result.message, /Дахин төлөхгүйгээр/)
  })
}

test('late settlement of a closed stay does not promise a new gate command', () => {
  assert.equal(paymentOutcome({ amount_due: 0, session_status: 'MANUAL_CLOSED', barrier_command_status: 'NOT_REQUESTED' }).kind, 'closed')
})
test('an ACK and an advance payment have different instructions', () => {
  assert.equal(paymentOutcome({ amount_due: 0, barrier_command_status: 'SUCCESS' }).kind, 'acknowledged')
  assert.equal(paymentOutcome({ amount_due: 0, session_status: 'PAID' }).kind, 'ready')
})
test('missing status from an older backend does not invent success', () => {
  assert.equal(paymentOutcome({ status: 'PAID' }).kind, 'unknown')
})

// Exercise the real React component in both supported parking layouts.
const bundle = await build({ stdin: {
  contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
    import SiteEditModal from './src/pages/settings/SiteEditModal.jsx';
    export function render(overrides) { return renderToStaticMarkup(React.createElement(SiteEditModal, {
      editing: {id:'fixture',name:'Fixture',site_code:'TEST',zone_code:'A',capacity:20,...overrides},
      setEditing(){}, templates:[], sites:[], onSubmit(){}
    })); }`, resolveDir: process.cwd(), loader: 'jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  packages: 'external', logLevel: 'silent' })
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const render = compiled.exports.render
test('single-site inner cameras expose unlimited hours with an associated label', () => {
  const html = render({ has_inner_lanes: true, parent_site_id: null, transit_max_hours: 0 })
  assert.match(html, /for="inner-time-hours"/)
  assert.match(html, /id="inner-time-hours"[^>]+value="0"/)
  assert.match(html, /0 = дотор бүртгэгдсэн бүх хугацааг үнэгүй хасна/)
})
test('separate nested sites retain the hours setting', () => {
  assert.match(render({ parent_site_id: 'outer', transit_max_hours: 12 }), /id="inner-time-hours"[^>]+value="12"/)
})
test('an ordinary site does not display an inapplicable inner-time control', () => {
  assert.doesNotMatch(render({ has_inner_lanes: false, parent_site_id: null }), /id="inner-time-hours"/)
})
