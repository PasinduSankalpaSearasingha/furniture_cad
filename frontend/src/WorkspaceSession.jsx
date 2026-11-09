import React,{useEffect,useState} from 'react';
import {api,post} from './api';

export default function WorkspaceSession({children}){
  const [session,setSession]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false);
  const load=async()=>{
    setLoading(true);setError('');
    try{
      const access=await api('/api/v1/workspace/access');
      if(access.mode==='local')setSession(access);
      else try{setSession({mode:'authenticated',user:await api('/api/v1/auth/me')});}catch(error){if(error.status!==401)throw error;setSession({mode:'authenticated',user:null});}
    }catch(error){setError(error.message);}finally{setLoading(false);}
  };
  useEffect(()=>{
    load();
    const expired=()=>setSession({mode:'authenticated',user:null});
    window.addEventListener('workspace-session-expired',expired);
    return()=>window.removeEventListener('workspace-session-expired',expired);
  },[]);
  const login=async event=>{
    event.preventDefault();setBusy(true);setError('');
    try{await post('/api/v1/workspace/session',{username,password});setPassword('');await load();}
    catch(error){setError(error.message);}finally{setBusy(false);}
  };
  const logout=async()=>{await api('/api/v1/workspace/session',{method:'DELETE'});setSession({mode:'authenticated',user:null});};
  if(session?.user)return children({session,logout});
  return <main className="session-page"><section className="card session-card">
    <p className="eyebrow">FISKE STUDIO</p><h1>Photo to CAD</h1>
    <p>Upload furniture, download CAD files, and edit your generated model.</p>
    {loading?<p role="status">Opening your workspace…</p>:error&&!session?<><p role="alert" className="message error">{error}</p><button className="primary full" onClick={load}>Try again</button></>:<form onSubmit={login}>
      <h2>Sign in to your workspace</h2>
      <label htmlFor="username">Username</label><input id="username" autoComplete="username" required maxLength="80" value={username} onChange={event=>setUsername(event.target.value)}/>
      <label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={event=>setPassword(event.target.value)}/>
      {error&&<p role="alert" className="message error">{error}</p>}
      <button className="primary full" disabled={busy}>{busy?'Signing in…':'Sign in'}</button>
    </form>}
  </section></main>;
}
