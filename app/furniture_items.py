"""Dimension and contact scopes for primary furniture and detached companions."""
import re
from app.geometry import exact_bounds,trimmed_bounds


def item_contracts(plan):
    values={v.key:v.value for v in plan.values}
    separate={pid for item in plan.separate_items for pid in item.part_ids}
    contracts=[dict(item_id='PRIMARY',label=plan.furniture_type,part_ids=[p.part_id for p in plan.parts if p.part_id not in separate],
        dimension_keys=dict(width='width_mm',depth='depth_mm',height='height_mm'))]
    contracts.extend(dict(item_id=item.item_id,label=item.label,part_ids=list(item.part_ids),
        dimension_keys=dict(width=item.width_key,depth=item.depth_key,height=item.height_key),evidence=item.evidence)
        for item in plan.separate_items)
    for item in contracts:item['target_dimensions_mm']={axis:values[key] for axis,key in item['dimension_keys'].items()}
    return contracts


def primary_part_ids(build):
    items=getattr(build,'furniture_items',[])
    return items[0]['part_ids'] if items else list(build.shapes)


def shape_spans(build,part_ids=None):
    ids=list(build.shapes) if part_ids is None else part_ids
    if not ids:raise ValueError('Furniture dimension scope has no geometry')
    clipped={r['part_id'] for r in getattr(build,'construction_adjustments',[])
        if r.get('kind')=='BOUNDED_LOFT_ENVELOPE'}
    bounds=[(trimmed_bounds if pid in clipped else exact_bounds)(build.shapes[pid].val()) for pid in ids]
    return [(min(getattr(b,a+'min') for b in bounds),max(getattr(b,a+'max') for b in bounds)) for a in 'xyz']


def dimensions_for(build,part_ids=None):
    return dict(zip(('width','depth','height'),(hi-lo for lo,hi in shape_spans(build,part_ids))))


def separate_item_checks(build):
    checked=[]
    for item in getattr(build,'furniture_items',[])[1:]:
        actual=dimensions_for(build,item['part_ids'])
        for axis,key in item['dimension_keys'].items():
            target=item['target_dimensions_mm'][axis];error=abs(actual[axis]-target)
            if error>.01:raise ValueError(f'{item["label"]} {key} must match its own CAD envelope; requested {target:g}, actual {actual[axis]:.6f}')
            checked.append(dict(item_id=item['item_id'],parameter=key,requested_mm=target,actual_mm=actual[axis],error_mm=error,tolerance_mm=.01))
    return checked


def infer_saved_companion(plan,inputs):
    """Migrate an evidenced legacy detached ottoman, without moving any parts."""
    if plan.separate_items or any(inputs.get(k) for k in ('engineering_edit','engineering_reexport_of','engineering_local_completion')):return None
    analysis=inputs.get('source_photo_analysis') or {}
    source=str(analysis.get('summary','')).lower()
    notes=' '.join(plan.assumptions).lower()
    if not (re.search(r'(separate|detached)\b.{0,65}\b(ottoman|footstool)',source) and
            re.search(r'exclud\w*\b.{0,80}\b(ottoman|footstool)',notes)):
        return None
    values={v.key:v.value for v in plan.values}
    for term in ('ottoman','footstool'):
        keys=[term+'_'+axis+'_mm' for axis in ('width','depth','height')]
        ids=[p.part_id for p in plan.parts if re.search(r'\b'+term+r'\b',p.label,re.I)]
        if not ids or len(ids)==len(plan.parts) or not set(keys)<=values.keys():continue
        raw=plan.model_dump(mode='json')
        note=f'Detached {term} retains its own dimensions and placement; width_mm, depth_mm and height_mm apply to the primary furniture only.'
        raw['separate_items']=[dict(item_id='A001',label='Detached '+term,part_ids=ids,
            width_key=keys[0],depth_key=keys[1],height_key=keys[2],evidence=note)]
        if len(raw['assumptions'])<30:raw['assumptions'].append(note)
        from app.photo_schema import PhotoDesign
        return PhotoDesign.model_validate(raw),dict(kind='SEPARATE_FURNITURE_ITEM_SCOPE',item_id='A001',part_ids=ids,
            description=note,geometry_changed=False)
    return None
