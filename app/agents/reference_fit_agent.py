"""Checkpointed local reference fitting, reused by photo and saved-plan workflows."""
import base64
import copy
from io import BytesIO
import numpy as np
from PIL import Image
from app.database import ImageAsset
from app.ai.images import read_image
from app.ai.openai_client import AIServiceError
from app.ai.reference_mask import png_bytes
from app.ai.reference_fitting import VERSION,fit_plan,prepare_target,mesh_from_build,render_mask,metrics,boundary
from app.reference_fit_schema import ReferenceFitOptions,FitView
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash,safe_directory,file_hash


def reference_inputs(r,options,image_ids=None):
    ids=image_ids or r.inputs.get('image_ids',[])
    views=options.views or [FitView(image_id=i) for i in ids[:4]]
    result=[];fingerprints=[]
    with r.db.sessions() as session:
        for view in views:
            asset=session.get(ImageAsset,str(view.image_id))
            if not asset or asset.project_id!=r.project_id or asset.kind not in ('REFERENCE','AI_CONCEPT_IMAGE','AI_CONCEPT'):
                raise AIServiceError('REFERENCE_FIT_IMAGE_INVALID','Choose a design reference from this project')
            result.append((view,read_image(asset,r.config)))
            fingerprints.append(dict(image_id=asset.id,sha256=asset.sha256))
    return result,fingerprints


def fit_reference(r,plan,observations,name,image_ids=None,options=None):
    options=ReferenceFitOptions.model_validate(options or {})
    images,fingerprints=reference_inputs(r,options,image_ids)
    options=options.model_copy(update={'views':[view for view,_ in images]})
    digest=canonical_hash(dict(version=VERSION,plan=plan.model_dump(mode='json'),
        references=fingerprints,options=options.model_dump(mode='json'),observations=observations.get('reference_view')))[:16]
    key='reference_fit_'+digest
    def execute():
        def progress(stage):
            r.stage=stage
            with r.db.sessions.begin() as session:
                workflow=r.checked_workflow(session);workflow.stage=stage
            r.record('reference_fitting',dict(operation=stage))
        selected,report,_,artifacts=fit_plan(plan,r.inputs,name,images,options,observations,progress)
        directory=safe_directory(r.config.storage,'reference_fit_'+r.id);directory.mkdir(exist_ok=True)
        files={}
        for filename,pixels in artifacts:
            filename=digest+'_'+filename;path=safe_directory(directory,filename);path.write_bytes(pixels);files[filename]=file_hash(path)
        report.update(source_plan_sha256=canonical_hash(plan.model_dump(mode='json')),
            measured_plan_sha256=canonical_hash(selected.model_dump(mode='json')),references=fingerprints,
            directory=directory.name,files=files,download_base=f'/api/v1/agents/workflows/{r.id}/fit',
            options=options.model_dump(mode='json'),artifact_prefix=digest)
        return dict(plan=selected.model_dump(mode='json'),report=report)
    result=r.checkpoint(key,None,execute,46)
    selected=PhotoDesign.model_validate(result['plan'])
    if canonical_hash(selected.model_dump(mode='json'))!=result['report']['measured_plan_sha256']:
        raise AIServiceError('REFERENCE_FIT_INTEGRITY_FAILED','Fitted geometry checkpoint changed')
    directory=safe_directory(r.config.storage,result['report']['directory'])
    for filename,digest in result['report']['files'].items():
        path=safe_directory(directory,filename)
        if not path.is_file() or file_hash(path)!=digest:
            raise AIServiceError('REFERENCE_FIT_INTEGRITY_FAILED','Fitting artifact changed')
    return selected,result['report']


def load_context(r,report):
    if not report or not report.get('views'):return []
    options=ReferenceFitOptions.model_validate(report['options'])
    images,fingerprints=reference_inputs(r,options)
    if fingerprints!=report['references']:
        raise AIServiceError('REFERENCE_FIT_INTEGRITY_FAILED','Reference images changed after fitting')
    directory=safe_directory(r.config.storage,report['directory']);context=[]
    for index,(view,pixels) in enumerate(images):
        filename=report['artifact_prefix']+f'_mask_{index}.png';path=safe_directory(directory,filename)
        if filename not in report['files'] or file_hash(path)!=report['files'][filename]:
            raise AIServiceError('REFERENCE_FIT_INTEGRITY_FAILED','Fitting mask changed')
        view=view.model_copy(update={'mask_png':base64.b64encode(path.read_bytes()).decode()})
        context.append((prepare_target(pixels,view,options.resolution),report['views'][index]['camera']))
    return context


def measure(build,plan,context):
    if not context:return []
    mesh=mesh_from_build(build,plan)
    return [metrics(render_mask(mesh,camera,target['size']),target,mesh,camera,target['view'].landmarks)
            for target,camera in context]


def require_improvement(before,after,interior_only=False):
    if not before:return
    if ((not interior_only and np.mean([m['loss'] for m in after])>=np.mean([m['loss'] for m in before])-.0005) or
        any(a['loss']>b['loss']+.003 or (b['landmark_error_fraction'] is not None and
            a['landmark_error_fraction']>b['landmark_error_fraction']+.002) for a,b in zip(after,before))):
        raise ValueError('Actual CAD correction did not improve the measured fixed-camera silhouette/landmark fit')


def final_measurement(r,plan,build,report,context):
    if not context:return report
    digest=canonical_hash(plan.model_dump(mode='json'))
    if digest==report['measured_plan_sha256']:return report
    def execute():
        result=copy.deepcopy(report);mesh=mesh_from_build(build,plan)
        directory=safe_directory(r.config.storage,result['directory'])
        for index,(target,camera) in enumerate(context):
            mask=render_mask(mesh,camera,target['size']);measured=metrics(mask,target,mesh,camera,target['view'].landmarks)
            result['views'][index]['after_geometry_fit']=measured
            old_name=report['artifact_prefix']+f'_before_{index}.png'
            old_path=safe_directory(directory,old_name)
            if file_hash(old_path)!=report['files'].get(old_name):
                raise AIServiceError('REFERENCE_FIT_INTEGRITY_FAILED','Comparison artifact changed')
            with Image.open(old_path) as image:old=np.array(image.convert('L'))>=128
            overlay=target['image'].copy();overlay[target['edge']]=[230,65,65]
            overlay[boundary(old)]=[60,110,245];overlay[boundary(mask)]=[25,190,100]
            for suffix,pixels in [(f'after_{index}.png',png_bytes(mask.astype(np.uint8)*255)),(f'overlay_{index}.png',png_bytes(overlay))]:
                filename=digest[:16]+'_'+suffix;path=safe_directory(directory,filename);path.write_bytes(pixels);result['files'][filename]=file_hash(path)
        result.update(measured_plan_sha256=digest,post_review_geometry_changed=True,final_artifact_prefix=digest[:16],
            geometry_loss_after=float(np.mean([v['after_geometry_fit']['loss'] for v in result['views']])))
        return result
    return r.checkpoint('reference_fit_final_'+digest[:16],None,execute,54)
