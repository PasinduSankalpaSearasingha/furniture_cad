import React,{useRef,useState} from 'react';
import {api} from './api';

export default function EngineeringDelivery({workflow,delivery,compact=false}){
  const [selected,setSelected]=useState(null),[document,setDocument]=useState(null),[error,setError]=useState('');
  const previewVersion=useRef(0);
  if(!delivery)return null;
  const engineering=workflow?.result?.engineering_cad,proof=delivery.cad_export_agreement;
  const items=delivery.items||[];
  const whole=proof?.output_scope==='WHOLE_FURNITURE';
  const downloads=[['STEP','cad/cabinet.step'],['STL','cad/cabinet.stl'],['GLB','cad/cabinet.glb'],
    ['Drawing DXF','cad/all_dimensions_REVIEW.dxf'],['Drawing PDF',whole?'cad/all_dimensions_REVIEW.pdf':'cad/drawings_REVIEW.pdf'],
    ['Drawing SVG','cad/all_dimensions_REVIEW.svg'],['FreeCAD','cad/furniture_REVIEW.FCStd']]
    .map(([label,path])=>({label,item:items.find(item=>item.path===path)})).filter(entry=>entry.item);
  const open=async item=>{
    const version=++previewVersion.current;setSelected(item);setDocument(null);setError('');
    if(/\.(csv|json|md)$/.test(item.path))try{const result=await api(item.preview_url);if(version===previewVersion.current)setDocument(result);}catch(error){if(version===previewVersion.current)setError(error.message);}
  };
  const visual=engineering?.visual_review?.visual,critic=engineering?.visual_review?.critic;
  const repairs=(engineering?.workflow?.geometry_repairs||[]).filter(item=>item.kind==='ESTIMATED_PROFILE_CORNER_ROUNDING');
  const localLofts=(engineering?.workflow?.geometry_repairs||[]).filter(item=>item.kind==='LOCAL_LOFT_REBUILD');
  const separateItems=(engineering?.workflow?.furniture_items||[]).filter(item=>item.item_id!=='PRIMARY');
  const costStop=engineering?.workflow?.cost_control_stop;
  const budgetDraft=engineering?.workflow?.budget_draft;
  const manual=engineering?.workflow?.manual_reconstruction;
  const budget=workflow?.ai_budget;
  const needsRevision=!!costStop||visual?.accepted===false||engineering?.photo_match?.accepted===false||delivery.photo_match?.accepted===false;
  return <section className="card delivery-card" aria-label="Engineering CAD files">
    <div className="section-heading"><div><h2>{costStop?'Available CAD draft':needsRevision?'Draft CAD / closer photo match needed':'Your CAD files are ready'}</h2><p>{costStop?budgetDraft?.message||'Further corrections stopped. Download the available CAD draft and review any remaining issues.':manual?'Reconstructed locally from the supplied reference views. Review the shape against your photos; no AI visual approval was performed.':needsRevision?'The geometry was exported, but the visual review found differences from your photo.':compact?'Download the furniture model and its dimensioned drawing.':'Download the whole-furniture package or choose a file format.'}</p></div><span className="pill">{needsRevision?'Draft':'Whole furniture'}</span></div>
    {repairs.length>0&&<p className="fine-print" role="note"><strong>Edge rounding changed.</strong> The same radius now rounds the profile corners on {repairs.length} {repairs.length===1?'component':'components'}; the broad face rims remain unrounded. Review these edges before manufacture.</p>}
    {localLofts.length>0&&<p className="fine-print" role="note"><strong>Curves repaired locally.</strong> {localLofts.length} {localLofts.length===1?'curved component was':'curved components were'} rebuilt from the saved profiles, retaining their section dimensions and radii. These repairs used no extra AI requests. Compare the resulting curves with your photos.</p>}
    {separateItems.length>0&&<p className="fine-print" role="note">The main furniture and {separateItems.map(item=>item.label).join(', ')} have separate dimension checks. Their combined layout is included in these files.</p>}
    {costStop&&<p className="fine-print" role="note"><strong>Automatic corrections stopped.</strong> {costStop.message} The available design is saved in these files as a draft.</p>}
    {budgetDraft?.remaining_issues?.length>0&&<div className="fine-print" role="note"><strong>Remaining design problems</strong><ul>{budgetDraft.remaining_issues.map((issue,index)=><li key={index}>{issue.message}</li>)}</ul></div>}
    {budget&&<p className="fine-print">Estimated AI usage: ${Number(budget.spent_usd).toFixed(2)} of ${Number(budget.maximum_usd).toFixed(2)}{budget.includes_linked_analysis?' including photo analysis':''}.{budget.uncertain?' Some usage is still unconfirmed.':''}</p>}
    <a className="primary" href={`${delivery.download_base}/furniture_complete_package.zip`}>Download CAD package (.zip) ↓</a>
    {!compact&&<>
      <div className="download-formats">{downloads.map(({label,item})=><a className="secondary" key={label} href={item.download_url}>Download {label}</a>)}</div>
      <p className="fine-print">Editable STEP model and DXF drawing, plus GLB, STL, PDF and SVG. Missing sizes remain labelled estimates.</p>
      <details><summary>Browse all {items.length} files</summary><div className="delivery-files">{items.map(item=><div className="delivery-file" key={item.path}>
        <a href={item.preview_url||item.download_url} onClick={event=>{if(item.preview_url){event.preventDefault();open(item);}}} target="_blank" rel="noreferrer"><span>{item.path}</span><small>{Math.max(1,Math.round(item.size_bytes/1024))} KB</small></a>
        <a href={item.download_url} aria-label={`Download ${item.path}`}>↓</a>
      </div>)}</div></details>
      {selected&&<div className="delivery-preview"><div className="section-heading"><strong>{selected.path}</strong><button className="secondary" onClick={()=>{previewVersion.current++;setSelected(null);}}>Close preview</button></div>
        {/\.(dxf|png|svg)$/.test(selected.path)&&<img src={selected.preview_url} alt={selected.path}/>}
        {selected.path.endsWith('.pdf')&&<iframe src={selected.preview_url} title={selected.path}/>}
        {error&&<p role="alert">{error}</p>}{document?.text&&<pre>{document.text}</pre>}
        {document?.columns&&<div className="table-scroll"><table><thead><tr>{document.columns.map((column,index)=><th key={index}>{column}</th>)}</tr></thead><tbody>{document.rows.map((row,index)=><tr key={index}>{row.map((value,index)=><td key={index}>{String(value??'')}</td>)}</tr>)}</tbody></table></div>}
      </div>}
      <details><summary>Model checks & dimension sources</summary>
        <p className="fine-print">The assembled model is checked for valid solid geometry and agreement with the previews. A single photo cannot verify physical dimensions or hidden construction.</p>
        <p className="fine-print">Photo review: {visual?.accepted&&critic?.accepted?'AI review completed; inspect the model before manufacture.':'Inspect the model and review the findings below.'}</p>
        {[...(critic?.findings||[]),...(visual?.findings||[])].map((text,index)=><p key={index} className="fine-print">{text}</p>)}
        {workflow?.result?.value_provenance&&<div className="table-scroll"><table><thead><tr><th>Dimension</th><th>Value</th><th>Source</th></tr></thead><tbody>{Object.entries(workflow.result.value_provenance).map(([key,item])=><tr key={key}><td>{key}</td><td>{item.value}</td><td>{{USER_PROVIDED:'Your input',SAMPLE_DRAWING:'Sample drawing',AI_ESTIMATED:'AI estimate',ASSUMED_COMPLETION:'Construction estimate'}[item.source]||item.source}</td></tr>)}</tbody></table></div>}
      </details>
    </>}
  </section>;
}
