export async function api(path,options={}) {
  const headers=options.body instanceof FormData?{}:{'Content-Type':'application/json'};
  const response=await fetch(path,{...options,headers:{...headers,...options.headers},credentials:'same-origin'});
  if(response.status===204)return null;
  let data;try{data=await response.json();}catch{throw Error(`Server returned HTTP ${response.status}. Please try again.`);}
  if(!response.ok){const detail=data.error?.message||data.detail;const error=Error(typeof detail==='string'?detail:detail?.message||'The request could not be completed. Please try again.');error.status=response.status;if(response.status===401)window.dispatchEvent(new Event('workspace-session-expired'));throw error;}
  return data;
}
export const post=(path,data)=>api(path,{method:'POST',body:JSON.stringify(data)});
export const title=s=>String(s||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
