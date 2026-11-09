import React,{useEffect,useState} from 'react';
import {api} from './api';
import Viewer from './Viewer';

export default function PartExports({jobId}){
  const [parts,setParts]=useState([]),[selected,setSelected]=useState(null),[view,setView]=useState('model'),[error,setError]=useState('');
  useEffect(()=>{
    let current=true;setParts([]);setSelected(null);setError('');
    if(jobId)api(`/api/v1/jobs/${jobId}/components`).then(data=>{if(current)setParts(data.components.filter(p=>p.geometry_source==='ENGINEERING_CAD'));}).catch(e=>{if(current)setError(e.message);});
    return()=>{current=false;};
  },[jobId]);
  if(!jobId||(!parts.length&&!error))return null;
  const base=`/api/v1/jobs/${jobId}/files`;
  return <section className="card part-exports" aria-label="Individual part CAD files">
    <div className="section-heading"><div><h2>Individual part files</h2><p>Inspect and download each closed solid CAD part.</p></div><span className="pill">{parts.length} solid parts</span></div>
    <p className="fine-print">Previews and drawings describe the exported solid parts. Missing measurements are estimates; stock, joints and machining tolerances remain unverified.</p>
    {error&&<p role="alert">{error}</p>}
    <div className="part-export-grid">{parts.map(part=><article key={part.part_id} className="part-export-card">
      <button className="part-open" aria-label={`Preview ${part.label}`} onClick={()=>{setSelected(part);setView('model');}}><img src={part.thumbnail_url} loading="lazy" alt={part.label}/><small>{part.part_id} · CLOSED SOLID · REVIEW</small><strong>{part.label}</strong><span>{[part.length_mm,part.width_mm,part.thickness_mm].map(n=>n.toFixed(1)).join(' × ')} mm</span></button>
      <div className="part-downloads">{Object.entries(part.files).map(([kind,file])=><a key={kind} href={`${base}/${file}`} aria-label={`Download ${part.part_id} ${kind.toUpperCase()}`}>{kind.toUpperCase()} ↓</a>)}</div>
    </article>)}</div>
    {selected&&<div className="part-inspector" aria-label="Individual part preview">
      <div className="section-heading"><h3>{selected.part_id} · {selected.label}</h3><button className="secondary" onClick={()=>setSelected(null)}>Close part preview</button></div>
      <div className="part-view-tabs">{[['model','3D part'],['drawing','DXF drawing'],['pdf','PDF sheet']].map(([key,label])=><button key={key} className={view===key?'primary':'secondary'} onClick={()=>setView(key)}>{label}</button>)}</div>
      {view==='model'&&<Viewer url={selected.model_url} posterUrl={selected.thumbnail_url} parts={[selected]} label={selected.label}/>}
      {view==='drawing'&&<img className="part-drawing" src={`${base}/${selected.files.dxf}/preview`} alt={`${selected.part_id} dimensioned review drawing`}/>}
      {view==='pdf'&&<iframe className="part-pdf" src={`${base}/${selected.files.pdf}/preview`} title={`${selected.part_id} PDF review sheet`}/>}
      <p className="fine-print">{selected.note}</p>
    </div>}
  </section>;
}
