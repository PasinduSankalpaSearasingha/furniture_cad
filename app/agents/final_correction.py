"""One comparison image and one final AI response, followed by local CAD checks."""
import base64
from hashlib import sha256
from io import BytesIO
import math
from PIL import Image, ImageDraw, ImageOps
from pydantic import Field
from app.engineering_schema import EngineeringReview
from app.engineering_repairs import EngineeringRepairPatch, merge_repair
from app.photo_schema import PhotoDesign
from app.photo_matching import checked_visual_review
from app.pipeline import canonical_hash, safe_directory, file_hash
from app.ai.openai_client import AIServiceError
from app.cad_meshing import CADMeshError

VIEWS = ('reference', 'front', 'rear', 'left', 'right', 'top', 'bottom')


class FinalCADReview(EngineeringReview):
    correction: EngineeringRepairPatch | None = Field(default=None,
        description='Only changed CAD parts and estimated parameters, or null when no valid correction is supported')


def comparison_tile(references, views):
    """Preserve source pixels and aspect ratios in a labelled engineering sheet."""
    if not 1 <= len(references) <= 6 or tuple(views) != VIEWS:
        raise ValueError('A final comparison needs references and all seven ordered CAD views')
    cells = [(f'REFERENCE PHOTO {i+1}', pixels) for i, pixels in enumerate(references)]
    cells += [('ACTUAL CAD / ' + name.upper(), views[name]) for name in VIEWS]
    columns, width, height, gap, label = 3, 550, 380, 12, 30
    rows = math.ceil(len(cells) / columns)
    sheet = Image.new('RGB', (columns*(width+gap)+gap, rows*(height+label+gap)+gap), '#e4e7eb')
    draw = ImageDraw.Draw(sheet)
    inventory = []
    for index, (title, pixels) in enumerate(cells):
        with Image.open(BytesIO(pixels)) as source:
            if source.width*source.height > 16_000_000:
                raise ValueError('Comparison source exceeds the pixel budget')
            thumbnail = ImageOps.contain(source.convert('RGB'), (width, height))
        x, y = gap+(index % columns)*(width+gap), gap+(index // columns)*(height+label+gap)
        draw.rectangle((x, y, x+width, y+height+label), fill='white')
        draw.text((x+10, y+8), title, fill='#152238')
        sheet.paste(thumbnail, (x+(width-thumbnail.width)//2, y+label+(height-thumbnail.height)//2))
        inventory.append(dict(cell=index+1, label=title, source_sha256=sha256(pixels).hexdigest()))
    stream = BytesIO(); sheet.save(stream, format='PNG')
    return stream.getvalue(), inventory


def render_tile(r, plan, build, images, reference, reference_size, suffix):
    from app.engineering_cad import render_preview
    from app.ai.geometry_preview import preview_meshes
    directory = safe_directory(r.config.storage, f'engineering_final_{r.id}_{suffix}_{r.lease}')
    directory.mkdir(exist_ok=False)
    photos = []
    for image in images:
        url = image.get('image_url', '')
        if not url.startswith('data:image/') or ';base64,' not in url:
            raise ValueError('Final correction accepts only existing private image bytes')
        photos.append(base64.b64decode(url.split(';base64,', 1)[1], validate=True))
    coordinates = [(reference['azimuth_deg'], reference['elevation_deg']),
                   (-90, 0), (90, 0), (180, 0), (0, 0), (-90, 89), (-90, -89)]
    views = {}
    mesh_report={};meshes=preview_meshes(build,report=mesh_report)
    for name, (az, el) in zip(VIEWS, coordinates):
        pixels = render_preview(build, az, el, size=reference_size if name=='reference' else (620, 700),meshes=meshes)
        (directory/(name+'.png')).write_bytes(pixels)
        views[name] = pixels
    pixels, inventory = comparison_tile(photos, views)
    (directory/'comparison.png').write_bytes(pixels)
    return dict(directory=directory.name, filename='comparison.png', plan_sha256=canonical_hash(plan.model_dump(mode='json')),
        files={p.name:file_hash(p) for p in directory.iterdir()}, inventory=inventory,raster_meshes=mesh_report)


def verify_tile(r, tile, plan):
    if tile['plan_sha256'] != canonical_hash(plan.model_dump(mode='json')):
        raise ValueError('Final comparison is bound to a different CAD plan')
    directory = safe_directory(r.config.storage, tile['directory'])
    for filename, digest in tile['files'].items():
        if file_hash(safe_directory(directory, filename)) != digest:
            raise ValueError('Final comparison evidence changed')
    return safe_directory(directory, tile['filename']).read_bytes()


def execute(r, plan, build, checks, dimensions, images, reference, reference_size, resolve, call):
    """At most one response request. A failed correction keeps validated geometry.

    Reservation is persisted before requesting AI. An interrupted reservation is
    not automatically submitted again; the model remains a clearly labelled draft.
    """
    def run():
        try:
            tile = r.checkpoint('engineering_final_tile_input', None,
                lambda:render_tile(r, plan, build, images, reference, reference_size, 'input'), 44)
        except CADMeshError:
            review=EngineeringReview(accepted=False,
                findings=['The comparison preview could not be generated. The validated CAD draft was retained.'],
                limitations=['Final AI visual review was not performed.'])
            return dict(plan=plan.model_dump(mode='json'),review=review.model_dump(mode='json'),report=dict(
                policy='ONE_FINAL_COMPARISON_AND_CORRECTION',status='PREVIEW_UNAVAILABLE',error_code='CAD_PREVIEW_UNAVAILABLE',
                input_tile=None,selected_tile=None,source_plan_sha256=canonical_hash(plan.model_dump(mode='json')),
                selected_plan_sha256=canonical_hash(plan.model_dump(mode='json')),request_limit=1,request_reserved=False,
                response_received=False,correction_applied=False,requires_human_visual_review=True,approved_for_manufacturing=False))
        pixels = verify_tile(r, tile, plan)
        with r.db.sessions() as session:
            saved = r.checked_workflow(session).checkpoints
            reserved = 'engineering_final_request_reserved' in saved
            response_saved = 'engineering_final_ai_response' in saved
        report = dict(policy='ONE_FINAL_COMPARISON_AND_CORRECTION', input_tile=tile,
            source_plan_sha256=canonical_hash(plan.model_dump(mode='json')), request_limit=1,
            request_reserved=reserved, response_received=False, correction_applied=False,
            selected_plan_sha256=canonical_hash(plan.model_dump(mode='json')),
            requires_human_visual_review=True, approved_for_manufacturing=False)
        fallback_review = EngineeringReview(accepted=False,
            findings=['Final AI correction was not completed. Inspect the retained CAD draft.'],
            limitations=['No completed final AI review is available.'])
        selected, review = plan, fallback_review
        if reserved and not response_saved:
            report.update(status='INTERRUPTED_FINAL_REQUEST', error_code='FINAL_REQUEST_ALREADY_RESERVED')
        else:
            r.checkpoint('engineering_final_request_reserved', None, lambda:dict(
                source_plan_sha256=report['source_plan_sha256'], tile_sha256=tile['files'][tile['filename']], request_limit=1), 45)
            report['request_reserved'] = True
            # This stage is one response, with no tool loop, implicit model
            # fallback or validation retry requesting another billed completion.
            gateway_models = getattr(r.gateway, 'models', None)
            if isinstance(gateway_models, dict):
                r.gateway.models = {**gateway_models, 'fallback':''}
            try:
                result = r.checkpoint('engineering_final_ai_response', FinalCADReview, lambda:call('review',
                    'Perform the FINAL visual review and parameter correction before CAD delivery. '
                    'The ONE attached comparison sheet labels original reference photos and seven ACTUAL CAD views. '
                    'Read the cell inventory. Compare silhouette, curves, broad faces, negative spaces, supports and component counts. '
                    'Distinguish camera differences from geometric differences. Check only visible evidence; hidden construction remains unverified. '
                    'Return one feature_check for every supplied critical Vxxx feature, referencing existing component IDs. '
                    'Use correction to return ONE small EngineeringRepairPatch containing only changed parts or estimated values. '
                    'Keep every supplied dimension unchanged; preserve furniture item membership, unrelated components and deliberate user edits. '
                    'Return correction=null if no defensible parameter/shape correction is available or no change is needed. '
                    'Do not return executable Python. Do not claim manufacturing approval or exact photographic reconstruction. '
                    'Review acceptance describes the CAD shown in this sheet; a proposed correction has not been visually rechecked.',
                    dict(plan=plan.model_dump(mode='json'), known_values=r.inputs.get('known_values', []),
                        appearance=dimensions.appearance.model_dump(mode='json') if dimensions.appearance else None,
                        solid_checks=checks, cell_inventory=tile['inventory'], reference_view=reference,
                        user_notes=r.inputs.get('notes', ''), final_correction=True), FinalCADReview,
                    pictures=[dict(type='input_image', image_url='data:image/png;base64,'+base64.b64encode(pixels).decode(), detail='high')]), 50)
                report['response_received'] = True
                base = {key:getattr(result, key) for key in EngineeringReview.model_fields}
                review = checked_visual_review(EngineeringReview(**base), dimensions.appearance, {p.part_id for p in plan.parts})
                if result.correction is not None:
                    report['proposed_patch'] = result.correction.model_dump(mode='json')
                    try:
                        candidate = merge_repair(plan, result.correction, r.inputs.get('known_values', []))
                        if candidate.separate_items != plan.separate_items:
                            raise ValueError('Final corrections cannot reclassify components into different furniture items')
                        resolved, _, _, corrected_build, _, corrected_checks = resolve(candidate)
                        if not corrected_checks.get('furniture_items_connected', corrected_checks['assembly_connected']):
                            raise ValueError('Final correction disconnected the furniture')
                        selected = resolved
                        report.update(status='CORRECTED_REVIEW_REQUIRED', correction_applied=True,
                            selected_plan_sha256=canonical_hash(selected.model_dump(mode='json')), validation=corrected_checks)
                        review = review.model_copy(update=dict(accepted=False, findings=[
                            'Final AI parameter corrections were applied and validated locally; inspect the corrected views.']+review.findings[:24]))
                    except (ValueError, AIServiceError) as exc:
                        if isinstance(exc, AIServiceError) and exc.code=='WORKFLOW_LEASE_LOST':raise
                        report.update(status='CORRECTION_REJECTED', error_code='FINAL_CAD_VALIDATION_FAILED')
                        review = review.model_copy(update=dict(accepted=False, findings=[
                            'Final AI correction failed local CAD checks; the previous valid model was retained.']+review.findings[:24]))
                else:
                    report['status'] = 'VISUALLY_REVIEWED' if review.accepted else 'NEEDS_VISUAL_REVISION'
                    report['requires_human_visual_review'] = not review.accepted
            except AIServiceError as exc:
                if exc.code=='WORKFLOW_LEASE_LOST':raise
                report.update(status='FINAL_REVIEW_UNAVAILABLE', error_code=exc.code)
            finally:
                if isinstance(gateway_models, dict):r.gateway.models = gateway_models
        # Produce new evidence for a changed model, without another AI call.
        if report['correction_applied']:
            _, _, _, corrected_build, _, _ = resolve(selected)
            try:
                final_tile = r.checkpoint('engineering_final_tile_selected', None,
                    lambda:render_tile(r, selected, corrected_build, images, reference, reference_size, 'selected'), 65)
                verify_tile(r, final_tile, selected)
                report['selected_tile'] = final_tile
            except CADMeshError:
                selected=plan
                report.update(status='CORRECTION_PREVIEW_UNAVAILABLE',correction_applied=False,
                    selected_plan_sha256=canonical_hash(plan.model_dump(mode='json')),selected_tile=tile,
                    requires_human_visual_review=True)
                review=review.model_copy(update=dict(accepted=False,findings=[
                    'The corrected model could not be previewed; the preceding CAD draft was retained.']+review.findings[:24]))
        else:
            report['selected_tile'] = tile
        return dict(plan=selected.model_dump(mode='json'), review=review.model_dump(mode='json'), report=report)
    result = r.checkpoint('engineering_final_correction', None, run, 67)
    selected = PhotoDesign.model_validate(result['plan'])
    if result['report']['input_tile'] is not None:verify_tile(r, result['report']['input_tile'], plan)
    if result['report']['selected_tile'] is not None:verify_tile(r, result['report']['selected_tile'], selected)
    return selected, EngineeringReview.model_validate(result['review']), result['report']
