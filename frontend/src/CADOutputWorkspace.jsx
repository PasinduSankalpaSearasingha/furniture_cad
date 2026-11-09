import React,{lazy,Suspense,useEffect,useState} from 'react';
import {api,post} from './api';
const Viewer=lazy(()=>import('./Viewer'));
const sides=['front','back','left','right','top','bottom'];

export default function CADOutputWorkspace({job,onRevision,disabled=false}){
  const [delivery,setDelivery]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [partId,setPartId]=useState('assembly'),[side,setSide]=useState('front'),[tab,setTab]=useState('model');
  const [values,setValues]=useState({}),[confirm,setConfirm]=useState({}),[label,setLabel]=useState('');
  const [review,setReview]=useState('pending'),[faces,setFaces]=useState(''),[construction,setConstruction]=useState('');
  const file=name=>`/api/mesh-reconstruction/${job}/files/${name}`;
  useEffect(()=>{let active=true;setDelivery(null);setError('');setPartId('assembly');
    api(file('cad_delivery.json')).then(data=>{if(active){setDelivery(data);setValues(Object.fromEntries(data.parameters.map(p=>[p.key,p.value])));setConfirm({});}}).catch(e=>{if(active)setError(e.message);});
    return()=>{active=false;};},[job]);
  const part=delivery?.parts.find(p=>p.part_id===partId);
  useEffect(()=>{setLabel(part?.label||'');setReview(part?.review_status||'pending');setFaces('');setConstruction('');},[partId,delivery]);
  async function revise(){
    setBusy(true);setError('');
    try{
      const parameters={},confirmed_dimensions={};
      for(const p of delivery.parameters)if(Number(values[p.key])!==p.value){
        parameters[p.key]=Number(values[p.key]);if(p.locked&&confirm[p.key])confirmed_dimensions[p.key]=Number(values[p.key]);
      }
      const face_assignments={};if(part&&faces.trim())face_assignments[partId]=faces.split(/[\s,]+/).filter(Boolean).map(value=>{
        if(!/^\d+$/.test(value))throw Error('Face IDs must be non-negative integers separated by commas.');return Number(value);
      });
      const result=await post(`/api/mesh-reconstruction/${job}/revise`,{idempotency_key:crypto.randomUUID(),parameters,confirmed_dimensions,
        part_labels:part&&label!==part.label?{[partId]:label}:{},boundary_reviews:part?{[partId]:review}:{},face_assignments,
        construction_updates:construction.trim()?[JSON.parse(construction)]:[]});
      onRevision(await api(result.status_url));
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  if(!delivery)return <p role={error?'alert':undefined}>{error||'Loading editable CAD and drawings…'}</p>;
  const drawing=delivery.drawings.sheets.find(s=>s.part_id===partId);
  const prefix=partId==='assembly'?'drawings/assembly':`drawings/parts/${partId}`;
  const preview=tab==='regions'?'reference/part_regions.glb':part?part.files.preview:delivery.files.assembly_preview;
  const pdf=tab==='validation'?delivery.files.validation_pdf:tab==='package'?delivery.files.manufacturing_pdf:`drawings/${drawing.pdf}`;
  return <section className="cad-output-workspace" aria-label="Editable CAD and engineering drawings">
    <h3>CAD, components and drawings</h3>
    <p>Geometry validated · manufacturing review required. Sol requests: {delivery.ai_usage.reserved_requests} / 2. FreeCAD: {delivery.freecad.status}.</p>
    <label>Inspect component<select value={partId} onChange={e=>setPartId(e.target.value)}><option value="assembly">Complete assembly</option>{delivery.parts.map(p=><option key={p.part_id} value={p.part_id}>{p.part_id} — {p.label}</option>)}</select></label>
    <div className="button-row">{[['model','3D CAD'],['regions','Mesh regions'],['drawings','Six sides'],['pdf','Drawing PDF'],['package','Manufacturing PDF'],['validation','Validation PDF']].map(([value,text])=><button key={value} className="secondary" aria-pressed={tab===value} onClick={()=>setTab(value)}>{text}</button>)}</div>
    {['model','regions'].includes(tab)&&<Suspense fallback={<p>Loading geometry…</p>}><Viewer key={`${job}:${preview}`} url={file(preview)} parts={part&&tab!=='regions'?[part]:delivery.parts} label={tab==='regions'?'Inferred mesh part boundaries':'Native CAD'} onPartSelect={id=>{if(delivery.parts.some(p=>p.part_id===id))setPartId(id);}}/></Suspense>}
    {tab==='regions'&&<p>Colors show inferred face assignments. Red regions are unassigned. Select a component to review its boundary; physical manufacturing splits remain unverified.</p>}
    {tab==='drawings'&&<><div className="button-row">{sides.map(name=><button key={name} aria-pressed={side===name} onClick={()=>setSide(name)}>{name}</button>)}</div>
      <p>{partId} · {side} · {delivery.drawings.projection.replace('_',' ')} · sheet scale shown in drawing, mm</p>
      <img src={file(`${prefix}/${side}.svg`)} style={{width:'100%',height:620,objectFit:'contain',background:'white'}} alt={`${partId} ${side} native vector drawing`}/>
      <div className="button-row"><a href={file(`${prefix}/${side}.svg`)}>Download SVG</a><a href={file(`${prefix}/${side}.dxf`)}>Download DXF</a></div></>}
    {['pdf','package','validation'].includes(tab)&&<iframe title={`${partId} ${tab} PDF`} src={file(pdf)} width="100%" height="700"/>}
    {part&&<><p>{part.geometry} · {part.category} · {part.face_ids.length.toLocaleString()} reference faces · {part.boundary_status.replaceAll('_',' ')}</p><p>{part.source_evidence}</p><ul>{part.uncertainties.map((item,i)=><li key={i}>{item}</li>)}</ul></>}
    <details><summary>Review dimensions and components</summary>
      <p>Saving creates a new CAD revision using the saved reference mesh. Local regeneration uses no Sol requests and no new Neural4D job.</p>
      <fieldset disabled={busy||disabled}><legend>CAD parameters</legend><div className="mesh-controls">{delivery.parameters.map(p=><label key={p.key}>{p.key.replaceAll('_',' ')}<input type="number" step="any" value={values[p.key]} disabled={p.locked&&!confirm[p.key]} onChange={e=>setValues(old=>({...old,[p.key]:e.target.value}))}/><small>{p.explanation}</small>{p.locked&&<span><input type="checkbox" checked={!!confirm[p.key]} onChange={e=>setConfirm(old=>({...old,[p.key]:e.target.checked}))}/> Supply a new confirmed measurement</span>}</label>)}</div>
      {part&&<><label>Component label<input value={label} maxLength="120" onChange={e=>setLabel(e.target.value)}/></label><label>Boundary review<select value={review} onChange={e=>setReview(e.target.value)}><option value="pending">Needs review</option><option value="accepted">Designer accepts boundary</option><option value="rejected">Reject inferred boundary</option></select></label>
        <details><summary>Advanced region and construction editing</summary><p>Face IDs address the preserved reference mesh. Replacement regions must be connected and cannot overlap other reviewed assignments.</p><label>Replacement face IDs<textarea value={faces} onChange={e=>setFaces(e.target.value)} placeholder="Comma-separated original face IDs"/></label><a href={file('part_registry.json')}>Reference face and vertex registry</a>
        <p>Download the component parameters to inspect its declarative construction. Keep the existing part ID when editing curves, profiles or NURBS controls.</p><a href={file(part.files.parameters)}>Component parameters</a><label>Replacement component declaration<textarea rows="8" value={construction} onChange={e=>setConstruction(e.target.value)} placeholder="PhotoPart JSON"/></label></details></>}
      <button onClick={revise}>{busy?'Regenerating…':'Save review and regenerate CAD'}</button></fieldset>
    </details>
    {error&&<p className="message error" role="alert">{error}</p>}
    <details open><summary>Export center</summary><div className="button-row">
      <a href={file(part?part.files.step:delivery.files.assembly_step)}>STEP</a>
      {(part?part.files.freecad:delivery.files.assembly_freecad)&&<a href={file(part?part.files.freecad:delivery.files.assembly_freecad)}>Editable FreeCAD</a>}
      <a href={file(part?part.files.parameters:delivery.files.parameters)}>Parameters</a><a href={file(delivery.files.source)}>CAD source</a>
      <a href={file(`drawings/${drawing.pdf}`)}>Six-side PDF</a><a href={file(delivery.files.manufacturing_pdf)}>Manufacturing review PDF</a>
      <a href={file(delivery.files.validation_pdf)}>Geometry validation PDF</a><a href={file('manufacturing/bom.csv')}>BOM</a><a href={file('manufacturing/cutting_envelopes_REVIEW.csv')}>Cutting envelopes (review)</a><a href={file('README_EDITABLE_CAD.txt')}>Editing instructions</a><a href={file('package.zip')}>Complete package</a>
    </div></details>
  </section>;
}
