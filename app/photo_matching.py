"""Visible-shape contracts and camera-aligned review of actual CAD geometry."""
import base64
from io import BytesIO
from PIL import Image
from app.engineering_schema import EngineeringPhotoView

POLICY='VISIBLE_SHAPE_V2'


def reconstruction_models(config):
    models=dict(config.ai_models)
    if config.photo_design_model:models['design']=config.photo_design_model
    if config.photo_review_model:models['review']=config.photo_review_model
    return models


def reference_camera(inputs,dimensions):
    source=(inputs.get('source_photo_analysis') or {}).get('reference_view')
    view=source or (dimensions.reference_view.model_dump(mode='json') if dimensions.reference_view else None)
    return EngineeringPhotoView.model_validate(view or dict(azimuth_deg=-110,elevation_deg=18)).model_dump(mode='json')


def reference_render_size(images):
    # Keep the reference's aspect ratio and enough pixels to inspect support faces.
    if images:
        data=images[0].get('image_url','')
        if data.startswith('data:image/'):
            with Image.open(BytesIO(base64.b64decode(data.split(',',1)[1]))) as image:
                width,height=image.size
            factor=min(1,1280/width,1280/height)
            return (max(128,round(width*factor)),max(128,round(height*factor)))
    return (900,900)


def checked_visual_review(review,appearance,component_ids=None):
    if appearance is None:return review
    expected={feature.feature_id for feature in appearance.critical_features}
    checks={item.feature_id:item for item in review.feature_checks}
    findings=list(review.findings)
    missing=expected-checks.keys()
    invalid=len(checks)!=len(review.feature_checks) or bool(checks.keys()-expected)
    if component_ids is not None and any(set(item.component_ids)-component_ids for item in checks.values()):invalid=True
    if missing:findings.append('Visible reference features were not checked: '+', '.join(sorted(missing)))
    if invalid:findings.append('The visual feature inventory differs from the reference appearance contract.')
    unresolved=[item for fid,item in checks.items() if fid in expected and item.status!='MATCH']
    for item in unresolved:
        findings.append(item.feature_id+': '+item.evidence+' '+item.correction)
    return review.model_copy(update=dict(accepted=review.accepted and not missing and not invalid and not unresolved,
        findings=findings[:25]))


def photo_match(reviews):
    visual=(reviews or {}).get('visual') or {}
    return dict(status='VISUAL_MATCH_REVIEWED' if visual.get('accepted') else 'NEEDS_VISUAL_REVISION',
        accepted=visual.get('accepted',False),findings=visual.get('findings',[]),
        feature_checks=visual.get('feature_checks',[]),exact_reconstruction_verified=False)
