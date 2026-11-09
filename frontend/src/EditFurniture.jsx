import React,{useEffect,useState} from 'react';

const styles=[['custom','Describe a change'],['round','Round / oval'],['tapered','Tapered'],['curved','Curved']];

export default function EditFurniture({workflowId,parts,selectedPartId,disabled,onApply,pending}){
  const [selected,setSelected]=useState(new Set()),[style,setStyle]=useState('custom'),[instructions,setInstructions]=useState('');
  useEffect(()=>{setSelected(new Set());setStyle('custom');setInstructions('');},[workflowId]);
  useEffect(()=>{if(selectedPartId&&parts.some(part=>part.part_id===selectedPartId))setSelected(old=>new Set([...old,selectedPartId]));},[selectedPartId,parts]);
  if(!workflowId||!parts.length)return null;
  const legs=parts.filter(part=>/\bleg\b|\blegs\b/i.test(part.label));
  const toggle=id=>setSelected(old=>{const next=new Set(old);next.has(id)?next.delete(id):next.add(id);return next;});
  return <section className="card edit-furniture" aria-label="Edit generated furniture">
    <div className="section-heading"><div><h2>Edit generated furniture</h2><p>Select parts and describe a change. The model, CAD files and drawings update together.</p></div><span className="pill">Saved as a new version</span></div>
    {pending&&<p className="edit-progress" role="status">Rebuilding the edited model and every file. Your saved model stays visible until the new package is complete.</p>}
    <div className="edit-layout"><div><label>Parts to change</label><p className="fine-print">Click a part in the 3D view or select it here.</p>
      <div className="edit-selection-actions">{legs.length>0&&<button className="secondary" disabled={disabled} onClick={()=>setSelected(new Set(legs.map(part=>part.part_id)))}>Select all legs ({legs.length})</button>}
        <button className="text-button" disabled={disabled} onClick={()=>setSelected(new Set(parts.map(part=>part.part_id)))}>Select all parts</button>
        <button className="text-button" disabled={disabled} onClick={()=>setSelected(new Set())}>Clear selection</button></div>
      <div className="edit-parts">{parts.map(part=><label key={part.part_id}><input type="checkbox" disabled={disabled} checked={selected.has(part.part_id)} onChange={()=>toggle(part.part_id)} aria-label={`Edit ${part.part_id} ${part.label}`}/><span><small>{part.part_id}</small>{part.label}</span></label>)}</div>
    </div><div><label htmlFor="edit-shape">Shape</label><select id="edit-shape" value={style} disabled={disabled} onChange={event=>setStyle(event.target.value)}>{styles.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
      <label htmlFor="edit-instructions">Change details {style==='custom'?'':'(optional)'}</label><textarea id="edit-instructions" rows="4" maxLength="3000" disabled={disabled} value={instructions} onChange={event=>setInstructions(event.target.value)} placeholder="For example: make the four chair legs tapered, keeping their mounting positions and the chair's overall size."/>
      <p className="fine-print">Existing dimensions and unselected parts are retained. The change is checked against the whole assembly, then saved as a new revision with a complete download package.</p>
      <button className="primary full" disabled={disabled||!selected.size||style==='custom'&&!instructions.trim()} onClick={()=>onApply({part_ids:[...selected],style,instructions})}>Apply change & rebuild all files</button>
      <p className="fine-print">AI edits incur additional usage. Your original files are retained.</p>
    </div></div>
  </section>;
}
