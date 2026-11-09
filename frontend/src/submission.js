import {post} from './api.js';
const pending=new Map();

// An uncertain response must not turn a user's retry into another billed job.
// Keep the request key until the server acknowledges it, including across reloads.
export async function submitWorkflow(path,payload){
  const body=JSON.stringify(payload),storageKey=`fiske:submission:${path}`;
  let previous=pending.get(path);
  if(!previous)try{previous=JSON.parse(sessionStorage.getItem(storageKey));}catch{}
  const key=previous?.body===body&&previous?.key?previous.key:crypto.randomUUID();
  pending.set(path,{body,key});
  try{sessionStorage.setItem(storageKey,JSON.stringify({body,key}));}catch{}
  const result=await post(path,{...payload,idempotency_key:key});
  if(pending.get(path)?.key===key)pending.delete(path);
  try{sessionStorage.removeItem(storageKey);}catch{}
  return result;
}
