import test from 'node:test'
import assert from 'node:assert/strict'
import { exitFacts, reviewReady, reviewDate } from '../src/exitReview.js'

test('ACK and closure never become physical exit confirmation', () => {
  const rows = Object.fromEntries(exitFacts({paid:true,closed:true,read_state:'rejected',command:{status:'SUCCESS'},acknowledged:true}))
  assert.equal(rows['Гарах уншилт'],'Уншилт ирсэн ч татгалзсан')
  assert.equal(rows['Төхөөрөмжийн хариу'],'Зөвшөөрсөн (ACK)')
  assert.equal(rows['Бодитоор гарсан эсэх'],'Тусдаа баталгаа алга')
})
test('unbound paid stay does not imply dispatch', () => {
  const rows=Object.fromEntries(exitFacts({paid:true,closed:false,read_state:'not_linked',command:null}))
  assert.equal(rows['Нээх команд'],'Команд бүртгэгдээгүй')
  assert.equal(rows['Зогсолт'],'Нээлттэй')
})
test('confirmation requires a fresh image, reason and explicit operator selection', () => {
  const valid={proof:{},expiresAt:100,now:50,confirmed:true,reason:'Matched image',busy:false}
  assert.equal(reviewReady(valid),true)
  for (const bad of [{proof:null},{now:100},{confirmed:false},{reason:'   '},{busy:true}]) assert.equal(reviewReady({...valid,...bad}),false)
})

test('proof timestamps support naive UTC and explicit offsets', () => {
  assert.equal(reviewDate('2026-10-10T09:00:00'),reviewDate('2026-10-10T17:00:00+08:00'))
  assert.equal(reviewDate('2026-10-10T09:00:00Z'),reviewDate('2026-10-10T09:00:00'))
  assert.equal(reviewDate('bad'),'Цаг тодорхойгүй')
})
