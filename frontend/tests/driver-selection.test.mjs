import test from 'node:test'
import assert from 'node:assert/strict'
import { registrationStatus, registrationValidity, selectDisplayed, toggleDriver } from '../src/driverSelection.js'

test('registration activity and time validity are independent', () => {
  const row={is_active:false,valid_from:'2026-08-07T00:00:00',valid_to:'2027-08-07T00:00:00'}
  assert.equal(registrationStatus(row),'Идэвхгүй')
  assert.equal(registrationValidity(row,Date.parse('2026-10-07T00:00:00Z')),'Хүчинтэй')
  assert.equal(registrationStatus({...row,is_active:true}),'Идэвхтэй')
  assert.equal(registrationValidity(row,Date.parse('2028-01-01T00:00:00Z')),'Хугацаа дууссан')
})
test('UTC boundaries and invalid date ranges do not invent valid rights', () => {
  const row={valid_from:'2026-10-07T00:00:00',valid_to:'2026-10-08T00:00:00'}
  assert.equal(registrationValidity(row,Date.parse('2026-10-07T00:00:00Z')),'Хүчинтэй')
  assert.equal(registrationValidity(row,Date.parse('2026-10-06T23:59:59Z')),'Хугацаа эхлээгүй')
  assert.equal(registrationValidity({...row,valid_to:'bad'}),'Огноо шалгах')
  assert.equal(registrationValidity({...row,valid_to:null}),'Огноо шалгах')
})
test('select all is exactly the displayed set, with explicit deselection', () => {
  const rows=[{id:'one'},{id:'two'}]
  assert.deepEqual(selectDisplayed(rows,true),['one','two'])
  assert.deepEqual(selectDisplayed(rows,false),[])
  assert.deepEqual(toggleDriver(['one'],'one',true),['one'])
  assert.deepEqual(toggleDriver(['one','two'],'one',false),['two'])
})
