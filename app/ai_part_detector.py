"""Optional Sol recognition and licensed local segmentation adapter interface."""
import base64
import json
import os
from pathlib import Path
import subprocess
from typing import Literal
from pydantic import Field
from .photo_schema import PhotoModel, PhotoDesign
from .ai_call_ledger import CallLedger

CATEGORIES = ('tabletop','seat','backrest','front_leg','rear_leg','armrest','cabinet_side','door',
              'shelf','rail','curved_frame','support_beam','decorative','glass','unknown')


class PartHypothesis(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    category: Literal['tabletop','seat','backrest','front_leg','rear_leg','armrest','cabinet_side','door',
                      'shelf','rail','curved_frame','support_beam','decorative','glass','unknown']
    region_ids: list[int] = Field(max_length=120)
    evidence: str = Field(max_length=1000)
    uncertainties: list[str] = Field(max_length=30)


class Recognition(PhotoModel):
    design: PhotoDesign
    hypotheses: list[PartHypothesis] = Field(max_length=60)


def _ensure_overall_dimensions(payload, bounds, known_dimensions):
    if not isinstance(payload, dict) or not isinstance(payload.get('design'), dict):
        return payload
    design = dict(payload['design'])
    values = list(design.get('values') or [])
    existing = {item.get('key') for item in values if isinstance(item, dict)}
    confirmed = known_dimensions or {}
    spans = [abs(float(bounds[1][index]) - float(bounds[0][index])) for index in range(3)]
    fallback = {
        'width_mm': confirmed.get('width_mm', spans[0]),
        'depth_mm': confirmed.get('depth_mm', spans[1]),
        'height_mm': confirmed.get('height_mm', spans[2]),
    }
    explanations = {
        'width_mm': 'Overall width derived from the reconstructed mesh bounds.',
        'depth_mm': 'Overall depth derived from the reconstructed mesh bounds.',
        'height_mm': 'Overall height derived from the reconstructed mesh bounds.',
    }
    for key, value in fallback.items():
        if key not in existing and float(value) > 0:
            values.append(dict(key=key, value=float(value), explanation=explanations[key]))
    design['values'] = values
    result = dict(payload)
    result['design'] = design
    return result


def local_adapter(mesh_path, output, *, command=None):
    """Administrator supplied adapter; no arbitrary command supplied by an API user.

    Contract: JSON {regions:[{face_ids:[...],category:...,evidence:...}]}.
    Licensing and installed weights are explicit deployment decisions.
    """
    command = command or os.getenv('FURNITURE_SEGMENTATION_COMMAND')
    if not command:
        return None
    if os.getenv('FURNITURE_SEGMENTATION_LICENSE_ACCEPTED') != 'true':
        raise ValueError('Local segmentation requires reviewed code AND weight licenses')
    argv = json.loads(command)
    if not isinstance(argv,list) or not argv or not all(isinstance(v,str) for v in argv):
        raise ValueError('Segmentation command must be a JSON argument array')
    destination = Path(output)/'adapter_regions.json'
    subprocess.run([*argv,str(Path(mesh_path).resolve()),str(destination.resolve())], check=True, timeout=600)
    return json.loads(destination.read_text(encoding='utf-8'))


def recognize(data, graph, image, output, *, known_dimensions=None, furniture_type='table', sdk=None):
    """One combined semantics/parametric-plan request; one optional local-validation repair.

    Images and face numbers cannot establish manufactured joints. Every proposed
    split stays reviewable and is validated against the actual reference mesh.
    """
    from openai import OpenAI
    from openai.lib._pydantic import to_strict_json_schema
    from .mesh_to_cad import build_native
    output = Path(output)
    ledger = CallLedger(output/'private'/'sol_calls.sqlite')
    sdk = sdk or OpenAI(max_retries=0, timeout=180)
    prompt = dict(furniture_type=furniture_type, mesh_bounds_mm=data.mesh.bounds.tolist(),
                  geometric_regions=graph['components'], confirmed_dimensions=known_dimensions or {})
    content = [dict(type='input_text',text=json.dumps(prompt)),
               dict(type='input_image',image_url='data:image/png;base64,'+base64.b64encode(image).decode(),detail='high')]
    instructions = ('Identify furniture manufactured-part hypotheses from the ORIGINAL photograph and mesh-region evidence. '
        'Return a complete declarative PhotoDesign using native curves, sweeps, lofts or NURBS where needed. '
        'Use consecutive F001 IDs. Bind sizes and relative positions to named parameters. Respect confirmed measurements. '
        'Region IDs refer to supplied geometric regions, not proven manufactured parts. '
        'Do not invent hidden joints, tolerances or confirmed materials. Record uncertain boundaries, occlusion, curved regions '
        'and missing components explicitly. Never output executable code. Categories do not establish physical evidence.')
    failure = None
    for attempt in range(2):
        request = dict(model='gpt-6.1-sol',store=False,instructions=instructions,input=[dict(role='user',content=content)],
                       max_output_tokens=16000,text=dict(format=dict(type='json_schema',name='furniture_recognition',
                       schema=to_strict_json_schema(Recognition),strict=True)))
        if failure:
            request['input'].append(dict(role='user',content='Local geometry/schema validation failed: '+failure+
                '. Return the corrected full object. This is the final permitted request.'))
        def submit(arguments):
            response = sdk.responses.create(**arguments)
            return dict(output_text=response.output_text, response_id=response.id, status=response.status)
        response = ledger.generate(request, submit)
        try:
            if response['status'] != 'completed':
                raise ValueError('Provider response did not complete')
            raw = json.loads(response['output_text'])
            raw = _ensure_overall_dimensions(raw, data.mesh.bounds.tolist(), known_dimensions)
            recognized = Recognition.model_validate(raw)
            for value in recognized.design.values:
                if value.key in (known_dimensions or {}):value.value=known_dimensions[value.key]
            build_native(recognized.design, {'known_values':[dict(key=k,value=v) for k,v in (known_dimensions or {}).items()]})
            ids = {p.part_id for p in recognized.design.parts}
            region_ids = {r['id'] for r in graph['components']}
            if any(h.part_id not in ids or not set(h.region_ids)<=region_ids for h in recognized.hypotheses):
                raise ValueError('Unknown part or geometric region ID in recognition')
            (output/'part_recognition.json').write_text(recognized.model_dump_json(indent=2), encoding='utf-8')
            return recognized
        except ValueError as exc:
            failure = str(exc)[:1500]
    raise ValueError('Both permitted recognition attempts failed local validation: '+failure)
