import assert from 'node:assert/strict'
import test from 'node:test'
import { fallbackMessage, fallbackPrefix, runOnce } from '../src/plateLookup.js'

// Продын лог 10-25: «олдсонгүй» хайлтын 18/23 нь зөвхөн цифр байсан
test('digits-only lookups get a prefix list instead of a bare "not found"', () => {
  assert.equal(fallbackPrefix('5583'), '5583')
  assert.equal(fallbackPrefix('7063'), '7063')
  assert.equal(fallbackPrefix('55'), '55')
  assert.equal(fallbackPrefix('017'), '017')
})
test('a full plate falls back to its digits (letters may be misread by the camera)', () => {
  assert.equal(fallbackPrefix('6350УКУ'), '6350')
  assert.equal(fallbackPrefix('0360 СББ'), '0360')
  assert.equal(fallbackPrefix('1234-УБА'), '1234')
})
test('too short or letter-first input', () => {
  assert.equal(fallbackPrefix('5'), null)
  assert.equal(fallbackPrefix(''), null)
  assert.equal(fallbackPrefix('УБ1234'), 'УБ12')
})
test('messages say whether a list is shown or the car is not registered at all', () => {
  assert.match(fallbackMessage('7063', 2), /жагсаалтаас машинаа сонгоно/)
  assert.match(fallbackMessage('6350УКУ', 1), /камер үсгийг өөрөөр уншсан/)
  assert.match(fallbackMessage('5583', 0), /«5583»-ээр эхэлсэн машин энэ зогсоолд бүртгэлгүй/)
  assert.match(fallbackMessage('0513УНГ', 0), /«0513»-ээр эхэлсэн/)
})
test('rapid repeats (held Enter, double tap) send only one request', async () => {
  const ref = { current: false }
  let calls = 0
  let release
  const slow = () => new Promise((resolve) => { calls += 1; release = resolve })
  const first = runOnce(ref, slow)
  const skipped = await Promise.all([runOnce(ref, slow), runOnce(ref, slow), runOnce(ref, slow)])
  assert.deepEqual(skipped, [false, false, false])
  release()
  assert.equal(await first, true)
  assert.equal(calls, 1)
  assert.equal(await runOnce(ref, async () => { calls += 1 }), true)   // дараа нь дахин хайж болно
  assert.equal(calls, 2)
})
test('a failed request releases the guard', async () => {
  const ref = { current: false }
  await assert.rejects(runOnce(ref, async () => { throw new Error('network') }))
  assert.equal(ref.current, false)
})
