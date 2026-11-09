import React from 'react';

export const dimensionLabels=[['width_mm','Overall width'],['depth_mm','Overall depth'],['height_mm','Overall height']];
const sources={SAMPLE_DRAWING:'Sample drawing',USER_PROVIDED:'Your input',AI_ESTIMATED:'AI estimate'};

export function availableDimensions(image,workflow,overrides,analysis){
  const match=workflow?.dimensions?.match||image?.provenance?.dimension_match;
  const values={...Object.fromEntries((analysis?.matching_inputs||[]).map(field=>[field.key,{value:field.estimated_value,source:'AI_ESTIMATED'}])),
    ...Object.fromEntries(Object.entries(match?.dimensions||{}).map(([key,value])=>[key,{value,source:'SAMPLE_DRAWING'}])),
    ...workflow?.dimensions?.values};
  for(const [key,value] of Object.entries(overrides))if(value!=='')values[key]={value:Number(value),source:'USER_PROVIDED'};
  return {match,values,fields:analysis?.matching_inputs||[],item:analysis?.vision?.category,summary:analysis?.summary};
}

export function FurnitureDimensionInputs({data,values,onChange,disabled}){
  if(!data.fields.length)return <p className="dimension-discovery">Upload a furniture photograph first. Image analysis will identify the item, its parts and the dimensions you can customise.</p>;
  const overall=data.fields.filter(field=>dimensionLabels.some(([key])=>key===field.key));
  const parts=data.fields.filter(field=>!dimensionLabels.some(([key])=>key===field.key));
  return <div className="furniture-dimensions">
    <p className="detected-item">Detected item: <strong>{data.item||'Furniture'}</strong></p>
    {data.summary&&<p className="fine-print">{data.summary}</p>}
    {[['Overall dimensions',overall],['Part dimensions',parts]].filter(([,fields])=>fields.length).map(([label,fields])=><fieldset className="dimension-group" key={label}><legend>{label}</legend><div className="parameter-grid">{fields.map(field=>{
      const current=data.values[field.key],unit=field.unit==='degrees'?'°':field.unit==='count'?'qty':'mm';
      return <label key={field.key} title={field.explanation}>{field.label}<div className="input-unit"><input aria-label={field.label} type="number"
        min={field.unit==='degrees'?-180:field.unit==='count'?0:dimensionLabels.some(([key])=>key===field.key)?10:.2}
        max={field.unit==='degrees'?180:field.unit==='count'?60:10000} step={field.unit==='count'?1:'any'}
        disabled={disabled} placeholder={current?.value??field.estimated_value} value={values[field.key]??current?.value??field.estimated_value??''}
        onChange={event=>onChange(field.key,event.target.value)}/><span>{unit}</span></div>
        <small className="dimension-source">{values[field.key]!==undefined&&values[field.key]!==''?'Your measurement':values[field.key]===''?'Estimated if blank':current?.source==='SAMPLE_DRAWING'?'Matching drawing':current?.source==='USER_PROVIDED'?'Saved measurement':'AI estimate'}</small>
      </label>;
    })}</div></fieldset>)}
    <p className="fine-print">These suggested sizes are based on your furniture photo or a matching drawing. Change the measurements to the sizes you want, then generate CAD. Clearing a field lets the planner estimate it.</p>
  </div>;
}

export default function Dimensions({data}){
  if(!data.match&&!Object.keys(data.values).length)return null;
  const matched=data.match?.status==='MATCHED';
  return <section className="card dimension-summary" aria-label="Image dimensions">
    <div className="section-heading"><div><h2>{matched?`Matched sample: ${data.match.label}`:'Image dimensions'}</h2>
      <p>{matched?'Target sizes from your matching shop drawing. Your inputs take priority.':'No reliable sample match. Missing sizes are estimated during image analysis.'}</p></div>
      {matched&&<a className="secondary" href={`${data.match.drawing_url}#page=${data.match.drawing.page}`} target="_blank" rel="noreferrer">View sample drawing ↗</a>}</div>
    <dl className="dimension-facts">{dimensionLabels.map(([key,label])=><div key={key}><dt>{label}</dt>
      <dd>{data.values[key]?`${Number(data.values[key].value).toLocaleString()} mm`:'Estimating…'}</dd>
      <small>{data.values[key]?sources[data.values[key].source]||'Unverified dimension':'Awaiting image analysis'}</small></div>)}</dl>
    {data.match?.notes?.map(note=><p className="dimension-note" key={note}>{note}</p>)}
    <p className="fine-print">Drawing sizes and photo estimates are unverified for the uploaded furniture.</p>
  </section>;
}
