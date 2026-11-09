import React,{lazy,Suspense,useState,useEffect,useCallback,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import EngineeringDelivery from './EngineeringDelivery';
import Dimensions,{availableDimensions,FurnitureDimensionInputs} from './Dimensions';
import EditFurniture from './EditFurniture';
import WorkspaceSession from './WorkspaceSession';
import DeleteDesignDialog from './DeleteDesignDialog';
import PhotoMatch from './PhotoMatch';
import {prepareImage} from './imageUpload';
import {readStudioLocation,saveStudioLocation} from './navigation';
import {checkedValues,nextCADWorkflow,saveIntent,running} from './workflowState';
import {api,post,title} from './api';
import {submitWorkflow} from './submission';
import './style.css';

const Viewer=lazy(()=>import('./Viewer'));
const DimensionSheet=lazy(()=>import('./DimensionSheet'));
const tabs=[['reference','01','Upload photo'],['model','02','Model & edit'],['manufacturing','03','CAD files']];
const initial=readStudioLocation();
const photoAngles=[['unspecified','Unknown angle'],['perspective','Perspective'],['front','Front'],['rear','Rear'],['left','Left'],['right','Right'],['top','Top'],['bottom','Underside'],['detail','Detail']];
const samePhotoSet=(a,b)=>a.length===b.length&&a.every((value,index)=>value===b[index]);

function App({session,logout}){
  const [projects,setProjects]=useState([]),[project,setProject]=useState(''),[newName,setNewName]=useState('');
  const [tab,setTab]=useState(initial.tab),[loaded,setLoaded]=useState(false);
  const [snapshot,setSnapshot]=useState(null),[workflow,setWorkflow]=useState(null),[images,setImages]=useState([]);
  const [analysisWorkflow,setAnalysisWorkflow]=useState(null),[selectedImageId,setSelectedImageId]=useState('');
  const [referenceViews,setReferenceViews]=useState([]);
  const [values,setValues]=useState({}),[notes,setNotes]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [systems,setSystems]=useState({}),[cadParts,setCadParts]=useState([]),[editPart,setEditPart]=useState('');
  const [dropActive,setDropActive]=useState(false);
  const [deleteTarget,setDeleteTarget]=useState(null);
  const currentProject=useRef(project),selectedImage=useRef(selectedImageId),mutationVersion=useRef(0),actionLock=useRef(false);
  const measurementsPanel=useRef(null),shownMeasurements=useRef('');
  const referenceSelection=useRef([]);
  const referenceSelectionTouched=useRef(false);
  referenceSelection.current=referenceViews;
  currentProject.current=project;selectedImage.current=selectedImageId;
  const analysing=running.has(analysisWorkflow?.status);
  const active=analysing||running.has(workflow?.status)||(snapshot?.workflows||[]).some(item=>item.cad_source==='ENGINEERING_CAD'&&running.has(item.status));
  const configured=systems.openai?.enabled&&systems.openai?.credentials_configured&&systems.cad?.available;
  const models=systems.openai?.models;
  const ready=configured&&(!models||['vision','design','review'].every(role=>models[role]));
  const uploadReady=ready;
  const writable=['admin','designer','operator'].includes(session.user.role);
  const selectedProject=projects.find(item=>item.id===project);
  const canDelete=writable&&(session.user.role==='admin'||!selectedProject?.owner_id||selectedProject.owner_id===session.user.id);

  const changeProject=id=>{
    mutationVersion.current++;shownMeasurements.current='';saveIntent(id,null);
    currentProject.current=id;selectedImage.current='';setProject(id);
    setSnapshot(null);setWorkflow(null);setAnalysisWorkflow(null);setImages([]);setValues({});setSelectedImageId('');
    setNotes('');setEditPart('');setCadParts([]);setError('');setNotice('');
    referenceSelection.current=[];referenceSelectionTouched.current=false;setReferenceViews([]);
  };
  const refresh=useCallback(async()=>{
    if(!project)return;
    const version=mutationVersion.current;
    const [state,references,history]=await Promise.all([api(`/api/projects/${project}`),api(`/api/v1/projects/${project}/images`),api(`/api/v1/projects/${project}/workflows`)]);
    if(currentProject.current!==project||version!==mutationVersion.current)return;
    const next=nextCADWorkflow(state);
    const target=selectedImage.current||history.find(item=>item.mode==='PHOTO_ANALYZE')?.reference_image_ids?.[0]||history.find(item=>item.id===next)?.reference_image_ids?.[0];
    const selectedIds=referenceSelection.current.map(view=>view.image_id);
    const photo=history.find(item=>item.mode==='PHOTO_ANALYZE'&&(selectedIds.length?samePhotoSet(item.reference_image_ids||[],selectedIds):!target||item.reference_image_ids?.includes(target)));
    const [item,analysis]=await Promise.all([next?api(`/api/v1/agents/workflows/${next}`):null,photo?api(`/api/v1/agents/workflows/${photo.id}`):null]);
    if(currentProject.current!==project||version!==mutationVersion.current)return;
    setSnapshot(state);setImages(references.filter(image=>image.kind==='REFERENCE'||image.kind==='AI_CONCEPT_IMAGE'));
    setWorkflow(item);setAnalysisWorkflow(analysis);
    if(!referenceSelection.current.length&&!referenceSelectionTouched.current){
      const restored=analysis||item,labels=restored?.reference_views||[];
      const views=(restored?.reference_image_ids||[]).map(image_id=>({image_id,angle:labels.find(view=>view.image_id===image_id)?.angle||'unspecified'}));
      referenceSelection.current=views;setReferenceViews(views);
    }
  },[project]);
  useEffect(()=>{
    let current=true;
    Promise.all([api('/api/projects'),api('/api/system/openai/status'),api('/api/system/cad/status')])
      .then(([list,openai,cad])=>{if(!current)return;setProjects(list);setSystems({openai,cad});
        if(list.length)changeProject(list.some(item=>item.id===initial.project)?initial.project:list[0].id);setLoaded(true);})
      .catch(error=>{if(current){setError(error.message);setLoaded(true);}});
    return()=>{current=false;};
  },[]);
  useEffect(()=>{refresh().catch(error=>setError(error.message));},[refresh]);
  useEffect(()=>{if(!active)return;const timer=setInterval(()=>refresh().catch(error=>setError(error.message)),2500);return()=>clearInterval(timer);},[active,refresh]);
  useEffect(()=>{if(loaded)saveStudioLocation(project,tab);},[loaded,project,tab]);
  const action=async callback=>{
    if(actionLock.current)return;actionLock.current=true;setBusy(true);setError('');setNotice('');
    try{await callback();}catch(error){setError(error.message);}finally{actionLock.current=false;setBusy(false);}
  };
  const launch=async(id,imageId,analysis,knownValues=[],requestNotes='')=>{
    mutationVersion.current++;
    const ids=analysis.reference_image_ids||[imageId],views=analysis.reference_views||[];
    const path=`/api/projects/${id}/generate-all`,payload={image_id:imageId,image_ids:ids,reference_views:views,cad_source:'ENGINEERING_CAD',analysis_workflow_id:analysis.id,known_values:knownValues,notes:requestNotes,final_ai_correction:true};
    const response=await submitWorkflow(path,payload);
    saveIntent(id,null);
    // Poll even when an idempotent submission resolves to an already completed job.
    if(currentProject.current===id){setWorkflow({id:response.workflow_id,project_id:id,mode:'IMAGE_TO_CAD',cad_source:'ENGINEERING_CAD',status:'QUEUED',stage:'queued',progress:0});setTab('model');
      setNotice('Creating your model and CAD files. You can edit the parts when generation finishes.');}
  };
  const analyse=async(id,imageId,views=referenceSelection.current)=>{
    const selected=views.length?views:[{image_id:imageId,angle:'unspecified'}],ids=selected.map(view=>view.image_id);
    mutationVersion.current++;
    const response=await submitWorkflow(`/api/projects/${id}/analyze`,{image_ids:ids,reference_views:selected,notes});
    saveIntent(id,null);
    if(currentProject.current===id){setAnalysisWorkflow({id:response.workflow_id,project_id:id,mode:'PHOTO_ANALYZE',reference_image_ids:ids,reference_views:selected,status:'QUEUED',stage:'photo_analysis',progress:0});
      setTab('reference');setNotice('Analysing your furniture and estimating measurements. Review and change the sizes before generating CAD.');}
  };
  const upload=(files,append=false)=>action(async()=>{
    const selected=Array.from(files||[]);if(!selected.length)return;
    if(!uploadReady)throw Error('Photo generation is unavailable. Check the backend provider configuration.');
    const base=append?[...referenceSelection.current]:[];
    if(base.length+selected.length>6)throw Error('Choose up to 6 photos of the same furniture. Remove a selected photo before adding another.');
    const prepared=[];for(const file of selected)prepared.push(await prepareImage(file));
    let id=project;
    if(!id){const created=await post('/api/projects',{name:newName.trim()||selected[0].name.replace(/\.[^.]+$/,'').slice(0,120)||'Furniture design'});
      changeProject(created.id);setProjects(old=>[created,...old]);id=created.id;}
    const uploaded=[];
    try{for(const file of prepared){
      const body=new FormData();body.append('file',file);
      const image=await api(`/api/projects/${id}/upload-image`,{method:'POST',body});
      if(currentProject.current!==id)return;
      uploaded.push(image);setImages(old=>[...old,image]);
    }}finally{if(currentProject.current===id&&uploaded.length){
      referenceSelectionTouched.current=true;
      const views=[...base,...uploaded.map(image=>({image_id:image.id,angle:'unspecified'}))];
      referenceSelection.current=views;setReferenceViews(views);selectedImage.current=views[0].image_id;setSelectedImageId(views[0].image_id);
      setAnalysisWorkflow(null);setValues({});
    }}
    if(currentProject.current!==id)return;
    mutationVersion.current++;
    setValues({});setAnalysisWorkflow(null);setWorkflow(null);setSnapshot(null);
    await analyse(id,referenceSelection.current[0].image_id,referenceSelection.current);
  });
  const changeReferences=views=>{
    referenceSelectionTouched.current=true;
    mutationVersion.current++;referenceSelection.current=views;setReferenceViews(views);
    selectedImage.current=views[0]?.image_id||'';setSelectedImageId(views[0]?.image_id||'');
    setAnalysisWorkflow(null);setValues({});setNotice('Photo selection changed. Analyse these views before generating CAD.');
  };

  const full=workflow?.mode==='IMAGE_TO_CAD';
  const editPending=Boolean(workflow?.edit_source_workflow_id&&(workflow.status!=='REVIEW_REQUIRED'||!workflow.result?.delivery));
  const showingSaved=editPending||Boolean(full&&workflow?.result?.available_output&&snapshot?.model_scope?.available_output_workflow_id&&snapshot?.delivery);
  const engineeringResult=showingSaved?snapshot?.engineering_cad:full?workflow?.result?.engineering_cad:snapshot?.engineering_cad;
  const delivery=showingSaved?snapshot?.delivery:full?workflow?.result?.delivery:snapshot?.delivery;
  const agreement=delivery?.cad_export_agreement||engineeringResult?.cad_export_agreement;
  const scope=showingSaved?snapshot?.model_scope:full&&!delivery?null:engineeringResult?{...snapshot?.model_scope,
    cad_workflow_id:workflow?.id||snapshot?.model_scope?.cad_workflow_id,job_id:workflow?.job_id||snapshot?.model_scope?.job_id,
    revision_id:workflow?.revision_id||snapshot?.model_scope?.revision_id,geometry_source:'ENGINEERING_CAD',solid_geometry_verified:agreement?.valid_closed_solids===true}:snapshot?.model_scope;
  const solid=Boolean(scope?.job_id&&scope.geometry_source==='ENGINEERING_CAD'&&scope.solid_geometry_verified===true&&delivery);
  const needsPhotoRevision=engineeringResult?.visual_review?.visual?.accepted===false||engineeringResult?.photo_match?.accepted===false;
  const modelReferenceId=engineeringResult?.workflow?.reference_image_ids?.[0]||workflow?.result?.reference_image_ids?.[0];
  const modelReference=images.find(image=>image.id===modelReferenceId);
  const modelReferences=(engineeringResult?.workflow?.reference_image_ids||workflow?.result?.reference_image_ids||[]).map(id=>images.find(image=>image.id===id)).filter(Boolean);
  const sourceImage=referenceSelectionTouched.current&&!referenceViews.length?null:images.find(image=>image.id===selectedImageId)||images.find(image=>image.id===analysisWorkflow?.reference_image_ids?.[0])||images.find(image=>image.id===workflow?.result?.reference_image_ids?.[0])||images.at(-1);
  const analysisReady=analysisWorkflow?.status==='SUCCEEDED'&&analysisWorkflow.reference_image_ids?.includes(sourceImage?.id)&&(!referenceViews.length||samePhotoSet(analysisWorkflow.reference_image_ids||[],referenceViews.map(view=>view.image_id))&&(!analysisWorkflow.reference_views?.length||referenceViews.every((view,index)=>view.angle===analysisWorkflow.reference_views[index]?.angle)));
  const dimensionWorkflow=(workflow?.reference_image_ids||workflow?.result?.reference_image_ids||[]).includes(sourceImage?.id)?workflow:null;
  const sameAnalysis=workflow?.result?.engineering_cad?.workflow?.analysis_workflow_id===analysisWorkflow?.id;
  const dimensionData=availableDimensions(sourceImage,analysisReady&&!sameAnalysis?null:dimensionWorkflow,values,analysisReady?analysisWorkflow.result:null);
  const progressWorkflow=analysing||!workflow?analysisWorkflow:workflow;
  const availableWorkflow=[analysisWorkflow,dimensionWorkflow].find(item=>item?.result?.available_output);
  const availableOutput=availableWorkflow?.result?.available_output;
  const draftInfo=engineeringResult?.workflow?.budget_draft;
  const failure=[analysisWorkflow,dimensionWorkflow].find(item=>!item?.result?.available_output&&['FAILED','CAD_FAILED','BUDGET_REACHED'].includes(item?.status));
  useEffect(()=>{if(failure||availableOutput||workflow?.status==='REVIEW_REQUIRED'&&delivery||analysisReady&&!active)setNotice('');},[failure?.id,failure?.status,availableWorkflow?.id,workflow?.status,Boolean(delivery),analysisReady,active]);
  useEffect(()=>{
    if(!analysisReady||tab!=='reference'||shownMeasurements.current===analysisWorkflow.id)return;
    shownMeasurements.current=analysisWorkflow.id;
    measurementsPanel.current?.focus({preventScroll:true});
    measurementsPanel.current?.scrollIntoView({block:'start',behavior:'auto'});
  },[analysisReady,analysisWorkflow?.id,tab]);
  useEffect(()=>{setEditPart('');},[scope?.job_id]);
  useEffect(()=>{let current=true;setCadParts([]);if(solid)api(`/api/v1/jobs/${scope.job_id}/components`).then(data=>{if(current)setCadParts(data.components||[]);}).catch(error=>{if(current)setError(error.message);});return()=>{current=false;};},[solid,scope?.job_id]);
  const regenerate=()=>action(async()=>{
    if(!sourceImage||!analysisReady)throw Error('Wait for photo analysis to finish.');
    const known=checkedValues(values,analysisWorkflow.result.matching_inputs||[],dimensionData.values);
    await launch(project,sourceImage.id,analysisWorkflow,known,notes);
  });
  const applyEdit=request=>action(async()=>{
    mutationVersion.current++;const source=scope?.cad_workflow_id;if(!source)throw Error('Generate a CAD model before editing.');
    const response=await submitWorkflow(`/api/projects/${project}/edit-cad`,{...request,source_workflow_id:source,final_ai_correction:true});
    setWorkflow({id:response.workflow_id,project_id:project,mode:'IMAGE_TO_CAD',cad_source:'ENGINEERING_CAD',edit_source_workflow_id:source,status:'QUEUED',stage:'queued',progress:0});
    setNotice('Updating the selected parts. Your saved model and files stay available until the new version is ready.');
  });
  const retry=item=>action(async()=>{
    mutationVersion.current++;await post(`/api/v1/agents/workflows/${item.id}/retry`,{});
    if(item.mode==='PHOTO_ANALYZE')setAnalysisWorkflow(old=>({...old,status:'QUEUED',error:null}));else setWorkflow(old=>({...old,status:'QUEUED',error:null}));
  });
  const deleteDesign=()=>action(async()=>{
    const target=deleteTarget;if(!target)return;
    mutationVersion.current++;
    const result=await api(`/api/projects/${target.id}`,{method:'DELETE'});
    saveIntent(target.id,null);setProjects(old=>old.filter(item=>item.id!==target.id));setDeleteTarget(null);
    if(currentProject.current===target.id){changeProject('');setNewName('');setTab('reference');}
    setNotice(result?.cleanup_pending?'Design deleted. Some files are waiting for cleanup.':`“${target.name}” was deleted.`);
  });
  const deliveryWorkflow=showingSaved?{...workflow,id:scope?.cad_workflow_id,result:{engineering_cad:engineeringResult,value_provenance:snapshot?.revisions?.[0]?.value_provenance}}:workflow;
  const headings={reference:['Photo to CAD','Upload one furniture photo. Get CAD files and an editable 3D model.'],model:['Model & edit','Inspect your furniture from every angle, then change the parts you want.'],manufacturing:['CAD files','Download your whole-furniture model and dimensioned drawing.']};
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar" aria-label="Workspace navigation"><a className="brand" href="/"><span className="brand-mark">f</span><span>fiske<span className="brand-small">PHOTO TO CAD</span></span></a>
      <button className="primary new-design" disabled={busy||active||!writable} onClick={()=>{changeProject('');setNewName('');setTab('reference');}}>+ New design</button>
      <label className="project-select-label" htmlFor="project">Saved designs</label><select id="project" className="project-select" disabled={busy} value={project} onChange={event=>changeProject(event.target.value)}><option value="">New design</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
      {project&&canDelete&&<button className="text-button delete-design" disabled={busy||active} title={active?'Wait for generation to finish before deleting':'Delete this project and its saved designs'} onClick={()=>{setError('');setDeleteTarget({id:project,name:snapshot?.name||selectedProject?.name||'this design'});}}>Delete design</button>}
      <nav>{tabs.map(([id,number,label])=><button key={id} className={tab===id?'nav-item active':'nav-item'} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}><span aria-hidden="true">{number}</span>{label}</button>)}</nav>
      <div className="sidebar-bottom"><p>Your designs and previous files are saved automatically.</p></div>
    </aside>
    {deleteTarget&&<DeleteDesignDialog design={deleteTarget} busy={busy} error={error} onCancel={()=>{setDeleteTarget(null);setError('');}} onDelete={deleteDesign}/>}
    <main id="main"><header className="topbar"><span>{snapshot?.name||projects.find(item=>item.id===project)?.name||'New furniture design'}</span><div>{solid&&<a className="primary" href={`${delivery.download_base}/furniture_complete_package.zip`}>Download CAD package</a>}<button className="secondary" disabled={busy||!project} onClick={()=>action(refresh)}>Refresh</button>{session.mode!=='local'&&<button className="text-button" disabled={busy} onClick={()=>action(logout)}>Sign out</button>}</div></header>
      <div className="content"><div className="page-heading"><div><p className="eyebrow">FURNITURE DESIGN</p><h1>{headings[tab][0]}</h1><p>{headings[tab][1]}</p></div>{solid&&<span className="pill">{needsPhotoRevision?'Draft / closer photo match needed':'Files ready'}</span>}</div>
      {error&&<div role="alert" className="message error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error">×</button></div>}
      {notice&&<div role="status" className="message success">{notice}</div>}
      {availableOutput&&<section className="message warning" role="status"><div><strong>Available output saved</strong><p>{availableOutput.message}</p>{showingSaved&&<p>Your previous saved CAD model and files are shown below.</p>}<a className="secondary" href={`/api/v1/agents/workflows/${availableWorkflow.id}/result`}>{availableOutput.has_saved_cad_plan?'Download saved CAD plan and reference report':'Download available reference and measurement report'}</a>{['CAD_PREVIEW_UNAVAILABLE','AGENT_WORKFLOW_FAILED'].includes(availableOutput.stop_reason?.code)&&<button className="secondary" disabled={busy||active} onClick={()=>retry(availableWorkflow)}>Resume saved CAD</button>}</div></section>}
      {draftInfo&&<section className="message warning" role="status"><div><strong>CAD draft ready to download</strong><p>{draftInfo.message||'Further corrections stopped. The available CAD draft and drawings are saved.'}</p>{!draftInfo.fully_connected&&<p>Some parts remain disconnected. Review the listed assembly issues before using this draft.</p>}</div></section>}
      {!loaded&&<p role="status">Loading your designs…</p>}
      {loaded&&!ready&&<div role="alert" className="message warning">Photo generation is temporarily unavailable. Saved models and downloads are still available. Ask your workspace administrator to check the generation service.</div>}
      {!writable&&<p className="message warning">You have read-only access. You can inspect and download saved designs.</p>}
      {progressWorkflow&&active&&<section className="job-strip" aria-label="Generation progress" aria-live="polite"><div><span className="status-dot pulse"/><strong>{analysing?'Analysing your photo':progressWorkflow.stage_label||title(progressWorkflow.stage)}</strong><small>{analysing?'Identifying furniture, parts and dimensions':'Building and checking your CAD files'}</small></div><span>{progressWorkflow.progress||0}%</span><progress aria-label="Current stage progress" max="100" value={progressWorkflow.progress||0}/></section>}
      {failure&&<section className={`message ${failure.status==='BUDGET_REACHED'?'':'error'} workflow-error`} role="alert"><div><strong>{failure.status==='BUDGET_REACHED'?'AI spending check stopped generation':failure.mode==='PHOTO_ANALYZE'?'Photo analysis stopped':'CAD generation stopped'}</strong><p>{failure.error?.message||'The workflow could not finish.'}</p>{failure.error?.code==='AI_AUTHENTICATION_FAILED'&&<p>Update the backend API key and restart the service before trying again.</p>}{failure.error?.retryable&&<button className="secondary" disabled={busy||active} onClick={()=>retry(failure)}>{failure.mode==='PHOTO_ANALYZE'?'Resume image analysis':'Resume saved workflow'}</button>}</div></section>}
      {tab==='reference'&&<div className="design-layout">
        <section className="card constraints"><div className="card-heading"><span className="step-number">01</span><div><h2>Upload a furniture photo</h2><p>We identify your furniture and suggest measurements for you to review.</p></div></div>
          {!project&&<><label htmlFor="design-name">Design name (optional)</label><input id="design-name" maxLength="120" value={newName} disabled={busy} onChange={event=>setNewName(event.target.value)} placeholder="For example, dining chair"/></>}
          <label className={'upload-zone '+(dropActive?'drag-active':'')} onDragOver={event=>{event.preventDefault();if(uploadReady&&!busy&&!active)setDropActive(true);}} onDragLeave={()=>setDropActive(false)} onDrop={event=>{event.preventDefault();setDropActive(false);if(uploadReady&&!busy&&!active&&writable)upload(event.dataTransfer.files);}}>
            <span aria-hidden="true">↑</span><strong>Choose photos or drop them here</strong><small>Up to 6 angles of the same furniture · JPG, PNG or WebP · 25 MB each</small><input aria-label="Upload furniture photo" type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={!loaded||busy||active||!uploadReady||!writable} onChange={event=>{const files=Array.from(event.target.files||[]);event.target.value='';upload(files);}}/>
          </label>
          {sourceImage&&<div className="selected-reference"><img src={sourceImage.download_url} alt="Uploaded furniture reference"/><div><small>YOUR PHOTO</small><strong>{analysisReady?analysisWorkflow.result.vision?.category:'Furniture reference'}</strong><span>{analysing?'Analysing…':analysisReady?'Analysis complete':'Ready for analysis'}</span></div></div>}
          {referenceViews.length>0&&<div className="reference-views" aria-label="Furniture reference photos">{referenceViews.map((view,index)=>{const image=images.find(item=>item.id===view.image_id);return <div className="reference-view" key={view.image_id}>
            {image&&<img src={image.download_url} alt={`Furniture reference view ${index+1}`}/>}
            <label>View {index+1}{index===0?' · Primary':''}<select aria-label={`Angle for photo ${index+1}`} value={view.angle} disabled={busy||active||!writable} onChange={event=>changeReferences(referenceViews.map(item=>item.image_id===view.image_id?{...item,angle:event.target.value}:item))}>{photoAngles.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
            <div className="reference-view-actions">{index>0&&<button className="secondary" disabled={busy||active||!writable} onClick={()=>changeReferences([view,...referenceViews.filter(item=>item.image_id!==view.image_id)])}>Make primary</button>}<button className="secondary" disabled={busy||active||!writable} aria-label={`Remove photo ${index+1}`} onClick={()=>changeReferences(referenceViews.filter(item=>item.image_id!==view.image_id))}>Remove</button></div>
          </div>;})}</div>}
          {referenceViews.length>0&&referenceViews.length<6&&<label className="secondary add-photo-label">Add more angles<input aria-label="Add furniture angles" type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={busy||active||!ready||!writable} onChange={event=>{const files=Array.from(event.target.files||[]);event.target.value='';upload(files,true);}}/></label>}
          {sourceImage&&!analysisReady&&!active&&<button className="secondary full" disabled={busy||!ready||!writable} onClick={()=>action(()=>analyse(project,referenceViews[0]?.image_id||sourceImage.id,referenceViews))}>{referenceViews.length>1?`Analyse ${referenceViews.length} photos`:'Analyse photo again'}</button>}
          <p className="fine-print">Use front, side, rear and detail views of the same furniture. The first photo is the primary view. More views can clarify shape; physical sizes and hidden construction still need verification. Each photo contributes to AI usage within your spending limit.</p>
        </section>
        <section className="card design-brief" ref={measurementsPanel} tabIndex={-1} aria-label="Furniture measurements"><div className="card-heading"><span className="step-number">02</span><div><h2>{analysisReady?'Review furniture measurements':'Your CAD workflow'}</h2><p>{analysisReady?'Start with the suggested dimensions, then change any sizes before generating.':'Upload, review measurements, then create your model and CAD files.'}</p></div></div>
          {analysisReady?<><FurnitureDimensionInputs data={dimensionData} values={values} onChange={(key,value)=>setValues(old=>({...old,[key]:value}))} disabled={busy||active||!writable}/>
            <label htmlFor="notes">Design notes (optional)</label><textarea id="notes" rows="3" maxLength="5000" disabled={busy||active||!writable} value={notes} onChange={event=>setNotes(event.target.value)} placeholder="Details you want to preserve"/>
            {solid&&dimensionWorkflow?<div className="button-row"><button className="primary" onClick={()=>setTab('model')}>View & edit model</button><button className="secondary" disabled={busy||active||!ready||!writable} onClick={regenerate}>Generate a new version</button></div>:<button className="primary full" disabled={busy||active||!ready||!writable} onClick={regenerate}>Generate CAD files</button>}
          </>:<ol className="workflow-steps"><li><strong>Upload your photo</strong><p>The item, parts and dimensions are identified automatically.</p></li><li><strong>Review your measurements</strong><p>Change the estimated sizes identified for your uploaded furniture.</p></li><li><strong>Generate model & CAD</strong><p>Create your 3D model and editable drawings when the measurements are ready.</p></li></ol>}
        </section>
      </div>}
      {tab==='reference'&&analysisReady&&<details className="card dimension-details"><summary>Dimension sources</summary><Dimensions data={dimensionData}/></details>}
      {tab==='model'&&<Suspense fallback={<p role="status">Loading the model workspace…</p>}>
        <Viewer url={solid?`/api/v1/jobs/${scope.job_id}/files/cabinet.glb`:null} parts={cadParts} posterUrl={solid?`/api/v1/jobs/${scope.job_id}/files/cad_preview.png`:undefined} label="Furniture model" onPartSelect={setEditPart}/>
        {solid&&<><PhotoMatch engineering={engineeringResult} jobId={scope.job_id} reference={modelReference} references={modelReferences}/><EditFurniture workflowId={scope.cad_workflow_id} parts={cadParts} selectedPartId={editPart} disabled={busy||active||!ready||!writable} pending={editPending&&active} onApply={applyEdit}/>
          <EngineeringDelivery key={scope.job_id} compact workflow={deliveryWorkflow} delivery={delivery}/>
          <details className="card model-views"><summary>Model views</summary><div className="render-grid">{Object.entries(agreement.view_files||{isometric:'cad_preview.png'}).map(([name,file])=><a key={name} href={`/api/v1/jobs/${scope.job_id}/files/${file}`} target="_blank" rel="noreferrer"><img loading="lazy" src={`/api/v1/jobs/${scope.job_id}/files/${file}`} alt={`Furniture ${name} view`}/><span>{title(name==='cad_preview'?'isometric':name)}</span></a>)}</div></details>
        </>}
        {!solid&&!active&&<div className="empty-action"><button className="primary" onClick={()=>setTab('reference')}>Upload a photo to begin</button></div>}
      </Suspense>}
      {tab==='manufacturing'&&(solid?<><EngineeringDelivery key={scope.job_id} workflow={deliveryWorkflow} delivery={delivery}/><Suspense fallback={<p role="status">Loading the furniture drawing…</p>}><DimensionSheet jobId={scope.job_id}/></Suspense></>:<section className="card empty-state"><h2>{active?'Your CAD files are being created':'No CAD files yet'}</h2><p>{active?'Files will appear here when the model and exports are ready.':'Upload a furniture photo to create a model, drawings and a download package.'}</p><button className="primary" onClick={()=>setTab(active?'model':'reference')}>{active?'View generation progress':'Upload a photo'}</button></section>)}
      <footer><span>FISKE STUDIO</span><span>Estimated dimensions and construction need review before manufacture.</span></footer>
    </div></main>
  </div>;
}

createRoot(document.getElementById('root')).render(<WorkspaceSession>{props=><App {...props}/>}</WorkspaceSession>);
