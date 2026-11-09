"""Bounded, recorded repairs of estimated photo geometry; never relax checks."""
import ast
from app.photo_schema import PhotoDesign,number


class EdgeRoundingError(ValueError):
    def __init__(self,part_id,message):
        self.part_id=part_id
        super().__init__(message)


def profile_corner_repair(plan,error,inputs):
    """Replace an estimated, impossible all-edge fillet with explicit corners.

    This changes edge selection, not radius or dimensions. Unrounded broad-face
    rims are disclosed for visual review. Explicit supplied radii and existing
    edge features are never revised automatically.
    """
    if not isinstance(error,EdgeRoundingError):return None
    part=next((p for p in plan.parts if p.part_id==error.part_id),None)
    if (part is None or part.geometry!='outline_panel' or part.curve_mode!='bezier' or
            part.surface_grid or part.surface_dishes or part.dish_depth or
            any(f.operation in ('fillet','chamfer') for f in part.features)):
        return None
    values={v.key:v.value for v in plan.values}
    radius=number(part.edge_radius,values)
    if not 0<radius<min(number(v,values) for v in part.size)/2:return None
    names=({n.id for n in ast.walk(ast.parse(part.edge_radius,mode='eval')) if isinstance(n,ast.Name)}
        if isinstance(part.edge_radius,str) else set())
    if names & {v['key'] for v in inputs.get('known_values',[])}:return None
    raw=plan.model_dump(mode='json')
    target=next(p for p in raw['parts'] if p['part_id']==part.part_id)
    target.pop('edge_radius',None)
    note=(f'{part.part_id}: estimated all-edge rounding could not be built. Applied the same {radius:g} mm radius '
        'to sharp extrusion-profile corners only; broad-face rims remain unrounded and require visual review. '
        'Parameter values, placement and supplied dimensions were retained.')
    target.setdefault('features',[]).append(dict(operation='fillet',radius=part.edge_radius,
        edge_selector='vertical_corners',evidence=note))
    if len(raw['assumptions'])<30:raw['assumptions'].append(note)
    else:raw['assumptions'][-1]+=' '+note
    report=dict(part_id=part.part_id,kind='ESTIMATED_PROFILE_CORNER_ROUNDING',radius_mm=radius,
        original_edge_selection='all_edges',applied_edge_selection='vertical_corners',
        broad_face_rims_rounded=False,requires_visual_review=True,description=note)
    return PhotoDesign.model_validate(raw),report


def resolve_repaired_geometry(plan,inputs,name):
    from app.agents.photo_agent import resolve_estimated_geometry
    candidate=plan;repairs=[]
    if inputs.get('output_scope')=='WHOLE_FURNITURE':
        from app.furniture_items import infer_saved_companion
        inferred=infer_saved_companion(candidate,inputs)
        if inferred:
            candidate,report=inferred;repairs.append(report)
        if not inputs.get('engineering_edit') and not inputs.get('engineering_reexport_of') and not inputs.get('engineering_local_completion') and (
                inferred or inputs.get('sweep_frame_policy')=='WORLD_UP_V2'):
            raw=candidate.model_dump(mode='json');changed=[];panels=[]
            for part in raw['parts']:
                if part['geometry'] in ('ellipse_sweep','rect_sweep') and part.get('sweep_frame','LEGACY')=='LEGACY':
                    part['sweep_frame']='WORLD_UP';changed.append(part['part_id'])
                if part['geometry']=='loft':part['loft_mode']='BOUNDED'
                if part['geometry'] in ('box','outline_panel','cylinder') and (part.get('surface_grid') or part.get('dish_depth')):
                    if part.get('panel_frame','LEGACY')!='BOUNDS':panels.append(part['part_id'])
                    part['panel_frame']='BOUNDS'
            candidate=PhotoDesign.model_validate(raw)
            if changed:
                repairs.append(dict(kind='WORLD_UP_SWEEP_FRAME',part_ids=changed,
                    description='Oval and rectangular sweep width uses the horizontal section axis and depth uses its vertical axis; supplied parameter values were retained.'))
            if panels:
                repairs.append(dict(kind='PANEL_SURFACE_FRAME',part_ids=panels,
                    description='Panel crown/dish shaping uses the actual outline bounds and vertical extrusion, preserving the contour and avoiding self-intersecting normal offsets.'))
    while True:
        try:
            result=resolve_estimated_geometry(candidate,inputs,name)
            result[3].warnings.extend(r['description'] for r in repairs)
            return (*result,[*repairs,*getattr(result[3],'construction_adjustments',[])])
        except EdgeRoundingError as exc:
            repair=(profile_corner_repair(candidate,exc,inputs)
                if inputs.get('output_scope')=='WHOLE_FURNITURE' and not inputs.get('engineering_edit') and
                sum(r['kind']=='ESTIMATED_PROFILE_CORNER_ROUNDING' for r in repairs)<4 else None)
            if repair is None:raise
            candidate,report=repair;repairs.append(report)
