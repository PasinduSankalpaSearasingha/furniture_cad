import React,{useEffect,useState} from 'react';
import {api,post} from './api';

export default function WorkspaceSession({children}){
  const [session,setSession]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState(''),[busy,setBusy]=useState(false);
  const [signupOpen,setSignupOpen]=useState(false),[creating,setCreating]=useState(false);
  const load=async()=>{
    setLoading(true);setError('');
    try{
      const access=await api('/api/v1/workspace/access');
      setSignupOpen(Boolean(access.signup));
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
  const submit=async event=>{
    event.preventDefault();setError('');
    if(creating&&password!==confirm){setError('The two passwords do not match.');return;}
    setBusy(true);
    try{await post(creating?'/api/v1/workspace/signup':'/api/v1/workspace/session',{username,password});setPassword('');setConfirm('');await load();}
    catch(error){setError(error.message);}finally{setBusy(false);}
  };
  const switchMode=()=>{setCreating(!creating);setError('');setPassword('');setConfirm('');};
  const logout=async()=>{await api('/api/v1/workspace/session',{method:'DELETE'});setSession({mode:'authenticated',user:null});};
  if(session?.user)return children({session,logout});
  return <main className="session-page"><section className="card session-card">
    <p className="eyebrow">FISKE STUDIO</p><h1>Photo to CAD</h1>
    <p>Upload furniture, download CAD files, and edit your generated model.</p>
    {loading?<p role="status">Opening your workspace…</p>:error&&!session?<><p role="alert" className="message error">{error}</p><button className="primary full" onClick={load}>Try again</button></>:<form onSubmit={submit}>
      <h2>{creating?'Create your account':'Sign in to your workspace'}</h2>
      <label htmlFor="username">Username</label><input id="username" autoComplete="username" required minLength={creating?3:1} maxLength="80" pattern={creating?'[A-Za-z0-9_.@\\-]+':undefined} title={creating?'Letters, numbers and _ . @ - only':undefined} value={username} onChange={event=>setUsername(event.target.value)}/>
      <label htmlFor="password">Password</label><input id="password" type="password" autoComplete={creating?'new-password':'current-password'} required minLength={creating?8:1} value={password} onChange={event=>setPassword(event.target.value)}/>
      {creating&&<><label htmlFor="confirm">Confirm password</label><input id="confirm" type="password" autoComplete="new-password" required minLength="8" value={confirm} onChange={event=>setConfirm(event.target.value)}/><p className="fine-print">At least 8 characters.</p></>}
      {error&&<p role="alert" className="message error">{error}</p>}
      <button className="primary full" disabled={busy}>{busy?(creating?'Creating account…':'Signing in…'):(creating?'Create account':'Sign in')}</button>
      {signupOpen&&<p className="fine-print">{creating?'Already have an account? ':'New here? '}<button type="button" className="text-button" onClick={switchMode}>{creating?'Sign in':'Create an account'}</button></p>}
    </form>}
  </section></main>;
}
