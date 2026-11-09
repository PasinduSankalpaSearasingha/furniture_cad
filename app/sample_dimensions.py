"""Conservative image matching against the supplied, dimensioned shop drawings.

The catalogue is application data. Document text never becomes agent instructions.
Only the same reference image (including resized/recompressed copies) supplies
dimensions. Resemblance to a furniture category is not measurement evidence.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import hashlib
import json

import numpy as np
from PIL import Image, ImageOps
from scipy.fft import dctn

ROOT = Path(__file__).resolve().parent / 'reference_data' / 'momentro'
AXES = ('width_mm', 'depth_mm', 'height_mm')


def descriptor(data):
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        aspect = image.width / image.height
        pixels = np.asarray(image.resize((64, 64), Image.Resampling.LANCZOS), dtype=float) / 255
        grey = np.asarray(image.convert('L').resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    low = dctn(grey, norm='ortho')[:8, :8].flatten()[1:]
    return aspect, pixels, low > np.median(low)


@lru_cache(maxsize=1)
def catalogue():
    data = json.loads((ROOT / 'catalogue.json').read_text(encoding='utf-8'))
    references = []
    for sample in data['samples']:
        for reference in sample['references']:
            content = (ROOT / reference['file']).read_bytes()
            if hashlib.sha256(content).hexdigest() != reference['sha256']:
                raise ValueError('Sample reference integrity failed')
            references.append((sample, reference, descriptor(content)))
    return data['samples'], references


def match_dimensions(encoded):
    _, references = catalogue()
    digest = hashlib.sha256(encoded).hexdigest()
    aspect, pixels, phash = descriptor(encoded)
    candidates = {}
    for sample, reference, (ref_aspect, ref_pixels, ref_hash) in references:
        exact = digest in reference['normalized_sha256']
        distance = int(np.count_nonzero(phash != ref_hash))
        error = float(np.mean(np.abs(pixels - ref_pixels)))
        # Both structure and colour must agree; a hash alone can match unrelated
        # furniture silhouettes. Reject blank/low-information images as well.
        if exact or (abs(aspect / ref_aspect - 1) <= .035 and distance <= 5
                     and error <= .035 and float(pixels.std()) > .055):
            score = 0 if exact else error + distance / 100
            previous = candidates.get(sample['id'])
            if previous is None or score < previous[0]:
                candidates[sample['id']] = (score, sample, 'IDENTICAL_IMAGE' if exact else 'RESIZED_IMAGE')
    ranked = sorted(candidates.values(), key=lambda item: item[0])
    if not ranked or (len(ranked) > 1 and ranked[1][0] - ranked[0][0] < .025):
        return dict(status='NO_MATCH', dimensions={}, unit='mm', verified=False,
                    message='No reliable sample image match. Missing dimensions will be AI estimates.')
    _, sample, method = ranked[0]
    return dict(status='MATCHED', sample_id=sample['id'], label=sample['label'], method=method,
                dimensions=dict(sample['dimensions']), unit='mm', source='SAMPLE_DRAWING', verified=False,
                drawing=dict(sample['drawing']), notes=list(sample['notes']),
                variants=sample.get('variants', []),
                drawing_url=f"/api/furniture-samples/{sample['id']}/drawing")


def dimension_inputs(match, supplied):
    """Freeze drawing values as constraints, with explicit user inputs winning."""
    values = dict(match.get('dimensions', {})) if match.get('status') == 'MATCHED' else {}
    sources = {key: dict(source='SAMPLE_DRAWING', sample_id=match['sample_id'],
                         drawing=match['drawing'], verified=False) for key in values}
    for entry in supplied:
        values[entry['key']] = entry['value']
        sources[entry['key']] = dict(source='USER_PROVIDED', verified=False)
    keys=[*AXES,*[key for key in values if key not in AXES]]
    return [dict(key=key, value=values[key]) for key in keys if key in values], sources


def workflow_dimensions(workflow):
    """Expose available dimensions while CAD is still being generated."""
    inputs = workflow.input_data or {}
    values = {v['key']: dict(value=v['value'], source='AI_ESTIMATED', verified=False)
              for v in (workflow.checkpoints.get('engineering_dimensions', {}).get('payload', {}).get('values', []))}
    for entry in inputs.get('known_values', []):
        values[entry['key']] = dict(value=entry['value'],
            **inputs.get('dimension_sources', {}).get(entry['key'], dict(source='USER_PROVIDED', verified=False)))
    final = (workflow.result or {}).get('value_provenance', {})
    values.update(final)
    return dict(values=values, match=inputs.get('dimension_match'), unit='mm',fields=inputs.get('dimension_fields',[]))
