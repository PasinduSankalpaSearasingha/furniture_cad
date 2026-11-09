import React,{useEffect,useState} from 'react';
import {api} from './api';

const sources={AI_ESTIMATED:'AI estimate',ASSUMED_COMPLETION:'Construction estimate',USER_PROVIDED:'Your input',SAMPLE_DRAWING:'Sample drawing',PHYSICALLY_MEASURED:'Measured'};

export default function DimensionSheet({jobId}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[zoom,setZoom]=useState(100),[attempt,setAttempt]=useState(0);
  useEffect(()=>{
    let current=true;setData(null);setError('');setZoom(100);
    if(jobId)api(`/api/v1/jobs/${jobId}/dimension-sheet`).then(result=>{if(current)setData(result);}).catch(error=>{if(current)setError(error.message);});
    return()=>{current=false;};
  },[jobId,attempt]);
  if(!jobId)return null;
  return <section className="card dimension-sheet" aria-label="All dimensions drawing">
    <div className="section-heading"><div><h2>Whole-furniture drawing</h2><p>Top, front, side and isometric views with overall dimensions and circular radii.</p></div>{data&&<span className="pill">A3 · 1 page · mm</span>}</div>
    {!data&&!error&&<p role="status">Preparing the drawing from your saved furniture model…</p>}
    {error&&<div role="alert"><p>{error}</p><button className="secondary" onClick={()=>setAttempt(old=>old+1)}>Retry drawing</button></div>}
    {data&&<>
      <div className="sheet-actions"><a className="primary" href={data.files.pdf} target="_blank" rel="noreferrer">Open / print PDF ↗</a><a className="secondary" href={data.files.dxf}>Download DXF ↓</a><a className="secondary" href={data.files.zip}>Download drawing files ↓</a>
        <label className="sheet-zoom">Zoom <input type="range" min="50" max="250" step="25" value={zoom} onChange={e=>setZoom(Number(e.target.value))} aria-label="Drawing zoom"/><span>{zoom}%</span></label></div>
      <div className="sheet-viewport"><a href={data.preview_url} target="_blank" rel="noreferrer" aria-label="Open full dimension drawing"><img src={data.preview_url} alt="Whole-furniture drawing with orthographic views, an isometric view and dimensions" style={{width:`${zoom}%`}}/></a></div>
      <p className="fine-print">Drawn from the exported CAD. Dimension sources are listed below. Physical measurements and construction remain unverified.</p>
      {data.sheet.curves&&(data.sheet.curves.circular_radii?.length>0||data.sheet.curves.variable_curves?.length>0)&&<details className="curve-dimensions"><summary>Curve radii from CAD</summary>
        <p className="fine-print">R is a circular radius in millimetres. These values describe the generated CAD; they are not physical measurements recovered from the photos.</p>
        {data.sheet.curves.circular_radii?.length>0&&<div className="table-scroll"><table><thead><tr><th>Circular radius</th><th>CAD edges</th></tr></thead><tbody>{data.sheet.curves.circular_radii.map(item=><tr key={item.radius_mm}><td>R{Number(item.radius_mm.toFixed(3))} mm</td><td>{item.edge_count}</td></tr>)}</tbody></table></div>}
        {data.sheet.curves.variable_curves?.length>0&&<><p className="fine-print">Freeform and elliptical curves have variable radii. The ranges below are sampled local radii, not one constant radius or a verified minimum and maximum.</p><div className="table-scroll"><table><thead><tr><th>Curve</th><th>Sampled local radii (mm)</th></tr></thead><tbody>{data.sheet.curves.variable_curves.map(item=><tr key={item.curve_id}><td>{item.curve_id} · {item.geometry.toLowerCase()}</td><td>{Number(item.sampled_local_radius_min_mm.toFixed(2))} – {Number(item.sampled_local_radius_max_mm.toFixed(2))}</td></tr>)}</tbody></table></div></>}
      </details>}
      <details><summary>Read all dimensions as a table</summary><div className="table-scroll"><table><thead><tr><th>Dimension</th><th>Value</th><th>Source</th></tr></thead><tbody>{data.sheet.parameters.map(item=><tr key={item.key}><td>{item.label}</td><td>{item.value} {item.unit}</td><td>{sources[item.source]||item.source}</td></tr>)}</tbody></table></div>
        {data.sheet.parts.length>0&&<div className="table-scroll"><table><thead><tr><th>Part</th><th>Local X × Y × Z envelope (mm)</th></tr></thead><tbody>{data.sheet.parts.map(part=><tr key={part.part_id}><td>{part.part_id} · {part.label}</td><td>{[part.length_mm,part.width_mm,part.thickness_mm].map(value=>Number(value.toFixed(2))).join(' × ')}</td></tr>)}</tbody></table></div>}</details>
    </>}
  </section>;
}
