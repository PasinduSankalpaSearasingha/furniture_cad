import assert from 'node:assert/strict';
import {checkedValues} from '../frontend/src/workflowState.js';

const fields=[{key:'width_mm',label:'Overall width',unit:'mm'},
  {key:'top_thickness_mm',label:'Top thickness',unit:'mm'},
  {key:'leg_count',label:'Leg count',unit:'count'}];
const estimates={width_mm:{value:2200,source:'AI_ESTIMATED'},
  top_thickness_mm:{value:30,source:'AI_ESTIMATED'},leg_count:{value:4,source:'AI_ESTIMATED'}};
assert.deepEqual(checkedValues({},fields,estimates),[],'Displayed estimates must retain their estimated provenance');
assert.deepEqual(checkedValues({width_mm:'2400'},fields,estimates),[{key:'width_mm',value:2400}]);
const saved={...estimates,width_mm:{value:2300,source:'USER_PROVIDED'}};
assert.deepEqual(checkedValues({},fields,saved),[{key:'width_mm',value:2300}],'Saved user dimensions must survive editing a generated design');
assert.deepEqual(checkedValues({width_mm:''},fields,saved),[],'Clearing a size allows estimation again');
assert.throws(()=>checkedValues({leg_count:'4.5'},fields,estimates),/valid leg count/);
assert.deepEqual(checkedValues({seat_height_mm:'450'},fields,estimates),[],'Only dimensions identified for this image are submitted');
console.log('Measurement review passed: estimates stay estimates, edits are submitted, saved targets persist and invalid counts are rejected.');
