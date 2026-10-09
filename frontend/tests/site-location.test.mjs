import test from 'node:test'
import assert from 'node:assert/strict'
import { locationPayload, locationError, coordinateLink, coordinatesFromLink } from '../src/siteLocation.js'

test('blank metadata clears to null while zero coordinates remain valid', () => {
  assert.deepEqual(locationPayload({latitude:' ',longitude:'',google_maps_url:' '}), {latitude:null,longitude:null,google_maps_url:null})
  assert.equal(locationError({latitude:0,longitude:0}), '')
  assert.match(coordinateLink({latitude:0,longitude:0}), /api=1&query=0%2C0$/)
})
test('coordinates must be a finite pair in range', () => {
  for (const point of [{latitude:47}, {longitude:106}, {latitude:91,longitude:0},
    {latitude:0,longitude:-181}, {latitude:'NaN',longitude:0}]) assert.ok(locationError(point))
})
test('only Google HTTPS map links are accepted without fetches', () => {
  for (const link of ['https://maps.app.goo.gl/abc','https://maps.google.com:443/?q=47,106',
    'https://www.google.com/maps/search/?api=1&query=47.9%2C106.9']) assert.equal(locationError({google_maps_url:link}), '')
  for (const link of ['https://google.com.evil.test/maps/', 'javascript:alert(1)',
    'http://maps.google.com/', 'https://user:password@maps.google.com/',
    'https://maps.google.com:8443/', 'https://google.com/mapsevil', 'https://maps.google.com/with space']) {
    assert.ok(locationError({google_maps_url:link}),link)
  }
})
test('explicit coordinate queries can be copied; viewport centres and short links cannot', () => {
  assert.deepEqual(coordinatesFromLink('https://www.google.com/maps/search/?api=1&query=47.918%2C106.917'), {latitude:47.918,longitude:106.917})
  assert.equal(coordinatesFromLink('https://www.google.com/maps/@47.918,106.917,15z'),null)
  assert.equal(coordinatesFromLink('https://maps.app.goo.gl/abc'),null)
  assert.equal(coordinatesFromLink('https://maps.google.com/?q=100,200'),null)
})
