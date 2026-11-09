export const running=new Set(['QUEUED','RUNNING','CAD_QUEUED','ESTIMATED_READY']);
const intentKey=project=>`fiske:pending-cad:${project}`;

export function readIntent(project){
  try{return JSON.parse(sessionStorage.getItem(intentKey(project)))||null;}catch{return null;}
}
export function saveIntent(project,intent){
  try{if(intent)sessionStorage.setItem(intentKey(project),JSON.stringify(intent));else sessionStorage.removeItem(intentKey(project));}catch{/* The current tab can still complete when storage is unavailable. */}
}

export function checkedValues(values,fields,existing={}){
  const allowed=new Map(fields.map(field=>[field.key,field]));
  const saved=Object.fromEntries(fields.filter(field=>existing[field.key]?.source==='USER_PROVIDED').map(field=>[field.key,existing[field.key].value]));
  return Object.entries({...saved,...values}).filter(([key,value])=>value!==''&&allowed.has(key)).map(([key,value])=>{
    const number=Number(value),field=allowed.get(key);
    const minimum=field.unit==='degrees'?-180:field.unit==='count'?0:['width_mm','depth_mm','height_mm'].includes(key)?10:.2;
    const maximum=field.unit==='degrees'?180:field.unit==='count'?60:10000;
    if(!Number.isFinite(number)||number<minimum||number>maximum||field.unit==='count'&&!Number.isInteger(number))
      throw Error(`Enter a valid ${field.label.toLowerCase()} between ${minimum} and ${maximum}, or leave it blank.`);
    return {key,value:number};
  });
}

export function nextCADWorkflow(state){
  const workflows=(state.workflows||[]).filter(workflow=>workflow.cad_source==='ENGINEERING_CAD'&&workflow.mode==='IMAGE_TO_CAD');
  // Model scope preserves a completed source while a pending or failed edit stays resumable.
  return workflows.find(workflow=>running.has(workflow.status))?.id||workflows[0]?.id||state.model_scope?.cad_workflow_id;
}
