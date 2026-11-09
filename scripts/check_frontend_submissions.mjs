// Simulate a server accepting a paid job while its response is lost in transit.
import assert from 'node:assert/strict';
import {submitWorkflow} from '../frontend/src/submission.js';
const storage=new Map();
globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
const accepted=new Map();let loseResponse=true;
globalThis.fetch=async(path,options)=>{
  const body=JSON.parse(options.body),key=body.idempotency_key;
  if(!accepted.has(key))accepted.set(key,{id:'workflow-'+accepted.size,body});
  if(loseResponse){loseResponse=false;throw Error('Connection lost after acceptance');}
  return new Response(JSON.stringify({workflow_id:accepted.get(key).id}),{status:202});
};
const path='/api/projects/synthetic/edit-cad',payload={source_workflow_id:'source',part_ids:['F002'],style:'tapered'};
await assert.rejects(submitWorkflow(path,payload),/Connection lost/);
const first=await submitWorkflow(path,payload);
assert.equal(accepted.size,1,'A retry must reuse the accepted job');
assert.equal(first.workflow_id,'workflow-0');assert.equal(storage.size,0);
await submitWorkflow(path,payload);
assert.equal(accepted.size,2,'An acknowledged request may be intentionally submitted again');
loseResponse=true;await assert.rejects(submitWorkflow(path,payload),/Connection lost/);
await submitWorkflow(path,{...payload,style:'curved'});
assert.equal(accepted.size,4,'Changed instructions must receive their own request key');
loseResponse=true;const reloadPath='/api/projects/reloaded/generate-all';
await assert.rejects(submitWorkflow(reloadPath,payload),/Connection lost/);
const reloaded=await import('../frontend/src/submission.js?reloaded');
await reloaded.submitWorkflow(reloadPath,payload);
assert.equal(accepted.size,5,'A retry after page reload must reuse the stored request key');
globalThis.sessionStorage={getItem:()=>{throw Error('Storage blocked');},setItem:()=>{throw Error('Storage blocked');},removeItem:()=>{throw Error('Storage blocked');}};
loseResponse=true;await assert.rejects(submitWorkflow('/api/projects/private/generate-all',payload),/Connection lost/);
await submitWorkflow('/api/projects/private/generate-all',payload);
assert.equal(accepted.size,6,'A retry must also reuse its job when browser storage is blocked');
console.log('Workflow submissions passed: lost-response retry creates one job, acknowledged resubmission and changed inputs create new jobs.');
