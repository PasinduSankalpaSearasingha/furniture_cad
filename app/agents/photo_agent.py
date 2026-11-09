"""Image analysis, optional known inputs, and a component-by-component CAD plan."""
import ast
import json
import math
from uuid import UUID
from pydantic import Field, model_validator
from app.models import StrictModel, CabinetSpec
from app.photo_schema import PhotoDesign, PhotoInput, InputValue
from app.ai.schemas import WorkflowRequest, FurnitureVisionResult
from app.ai.openai_client import AIServiceError
from app.ai.prompt_templates import BASE
from app.furniture_references import FurnitureReferenceView,validate_references,MULTIVIEW


class PhotoRequest(WorkflowRequest):
    known_values: list[InputValue] = Field(default_factory=list,max_length=40)
    material_hint: str | None = Field(default=None,max_length=160)
    source_revision_id: UUID | None = None
    reference_views: list[FurnitureReferenceView] = Field(default_factory=list,max_length=6)

    @model_validator(mode='after')
    def check_inputs(self):
        validate_references(self.image_ids,self.reference_views)
        if len({v.key for v in self.known_values})!=len(self.known_values): raise ValueError('A known input was supplied twice')
        return self


class ReferenceView(StrictModel):
    azimuth_deg: float = Field(default=-110,ge=-180,le=180)
    elevation_deg: float = Field(default=12,ge=-70,le=70)


class PhotoAnalysis(StrictModel):
    vision: FurnitureVisionResult
    matching_inputs: list[PhotoInput] = Field(min_length=3,max_length=24)
    summary: str = Field(min_length=1,max_length=1600)
    reference_view: ReferenceView | None = None

    @model_validator(mode='after')
    def check_inputs(self):
        keys=[v.key for v in self.matching_inputs]
        if len(keys)!=len(set(keys)) or not {'width_mm','depth_mm','height_mm'}<=set(keys):
            raise ValueError('Image analysis must provide unique matching inputs and overall dimensions')
        return self


ANALYZE=BASE+''' Inspect the uploaded furniture photo and return observations plus relevant OPTIONAL input fields.
Always include width_mm, depth_mm, height_mm. Then choose 3-21 numeric inputs relevant to THIS furniture:
for example seat_height_mm, seat_thickness_mm, arm_height_mm, leg_width_mm, leg_count, shelf_count,
glass_thickness_mm, cushion_thickness_mm, back_angle_deg. Include only applicable visible features.
Choose dimensions for the major visible parts: tabletop length/width/thickness and leg dimensions for tables;
seat/back/arm dimensions for chairs; carcass, door, drawer-front and shelf dimensions for storage.
Use clear labels and units mm/degrees/count. All estimated_value suggestions are UNVERIFIED estimates;
explain which visible proportion or common starting size you used. The UI displays editable suggestions;
untouched suggestions remain estimates, and user changes become explicit target dimensions.
Use keys ending in _mm for lengths, _deg for angles and _count for counts. Include useful part dimensions
such as drawer_front_height_mm, shelf_thickness_mm or leg_width_mm only when applicable to the detected item.
Identify the furniture freely. Do not select, name, recommend or fit a catalog/template.
Record every visible component and distinctive outline/curve, including small supports and aprons.
For visibly circular corners/bends, include relevant optional radius inputs such as corner_radius_mm.
Radius suggestions are estimates. A freeform spline or ellipse has variable curvature, not one circular radius.
Perspective and hidden surfaces create uncertainty; do not claim exact photographic reconstruction.'''
ANALYZE+=''' Estimate reference_view for an orthographic comparison: X right, Y toward the furniture back,
Z up. A camera in front has azimuth -90 degrees; front-right is -60, front-left -120.
Elevation is the camera angle above the seat/furniture, usually 5-25 degrees. Preserve the photo viewpoint.'''

DESIGN=BASE+''' Create a photo-specific 3D furniture component plan closely following the supplied reference.
There is NO furniture catalog or template. Inspect the image and build its visible silhouette, outlines,
number of members, relative positions, curved paths, leg sweep/taper, seat outline and thickness.
Include visible under-seat aprons/stretchers, back supports, projecting arms and distinctive details.
Use the requested known_values as fixed constraints. Infer all missing values from image proportions
and the supplied values. When none are supplied, estimate common dimensions and label assumptions.
Infer overall width_mm, depth_mm, height_mm; return every known key with its EXACT supplied value.
Express geometry using parameter names and simple + - * / arithmetic so supplied values actually drive it.
Do not silently ignore a supplied value or replace the photograph with a generic furniture form.

Geometry contract (millimetres, X left-right, Y front-back, Z up; object lies in 0..width/depth/height):
- parts are F001,F002,... in contact-trimming order: primary surfaces, rails, supports, legs/details.
- box: size X/Y/Z, centered at local origin; corner_radius rounds vertical edges.
- cylinder: elliptical XY section using size X/Y; height size Z; centered at local origin.
- ellipsoid: centered solid with overall local diameters size X/Y/Z, useful for soft volumes.
- outline_panel: actual local XY contour in outline, extruded size Z symmetrically about Z=0.
  curve_mode bezier uses chains of cubic Bezier controls: 4,7,10,...,37 points (3n+1),
  last point equals the first for a smooth closed seat. Use curved outlines for photo contours.
- loft: 2-6 local XY sections (center X/Y/Z, width/depth, rectangle/ellipse) at increasing Z;
  use changing centers and sizes for curved/tapered/splayed legs and sculpted members.
- round_sweep: diameter=size X; path contains actual local 3D centerline, including arm end height.
- rect_sweep: width=size X and depth=size Y. Use for flattened curved rails rather than forcing a round tube.
- ellipse_sweep: oval section with diameter size X along local section horizontal and size Y vertically.
  Prefer this for rounded, flattened wooden chair arms. Its path follows the visible centerline.
  Path curve_mode polyline has straight segments; bezier has 3n+1 controls in C1-continuous cubic segments.
  At joins, adjacent control handles must be collinear. Avoid self-intersection and excessively tight bends.
- dish_depth: shallow spherical cut on the top of box/outline_panel/cylinder, less than 0.7*thickness.
- position moves the local origin to the assembly. rotation_deg applies local X, then Y, then Z rotations.
- All shapes require positive size entries, even when only some size entries apply. Unused outline/path/sections are [].
- Provide each part's visible evidence and approximate RGB colour. Material must be the nearest honest category;
  hidden material/connection estimates must be listed as assumptions.
- Use only numbers, parameter identifiers, parentheses and + - * / in coordinate expressions; no functions or code.
  Named values themselves are finite numeric estimates. Reuse the parameter identifiers directly in dimensions.
- Members are trimmed against earlier parts at contacts. Keep each trimmed part a single connected solid.
  Give supports enough overlap to contour against rails; avoid thin detached slivers.
Ensure actual total envelope fits returned overall dimensions; supplied dimensions must match geometry.
Keep the plan economical (normally 8-30 parts, maximum 60). Model what is visible, not an invented mechanism.
List remaining differences (fine carving, invisible surfaces, perspective uncertainty). Never claim exact fidelity
or physical measurement recovery. This is a review reconstruction, not manufacturing-approved CAD.'''
DESIGN+=''' Preserve the reference's distinctive design instead of substituting common shapes.
Estimates establish scale only: keep visible relative proportions, component counts, curved outlines,
arm-end projection, seat sculpting, leg sweep, taper and softened profiles from the actual image.
All user-supplied values are deliberate edits; alter only the corresponding dimension and its necessary
dependent placements, retaining other design features. Keep all dimensions parametric, including member
sections, seat heights and thicknesses, so later value edits can reuse this exact design.
Include a named value and meaningful geometry references for every numeric matching_input selected during
photo analysis, using its estimated value only as a starting suggestion. This enables later precise edits.
For rounded rectangular loft sections use corner_radius (same corner topology in every section).
Retain meaningful named radius parameters for circular corners and fillets. Do not substitute a single radius
for an organic freeform curve or ellipse; preserve its actual profile and disclose variable curvature.
Use edge_radius for small real edge fillets on a solid, only where they fit. For round/oval sweeps use
rounded_ends=true when the photographed rail ends have softened rims. This fillets the terminal edges
inside the swept envelope, without extending the path; shape any longer rounded tips with additional sections.
Use enough local sections/control points to represent visible curves. Avoid sharp rectangular profiles
and flat-ended rails when the photo shows softened edges. Do not invent carving or hidden mechanisms.'''
DESIGN+=''' For genuinely sculpted seats, surface_dishes supplies up to eight shallow local ellipsoidal cuts.
Each has center=[local X,local Y], radius_x/radius_y/radius_z and depth, all in mm or parameter expressions.
The ellipsoid center is at local Z=size Z/2+radius_z-depth; only its shallow lower region cuts the top.
Depth must be positive, less than radius_z and less than 0.7*seat thickness. Two separate offset dishes can
leave a raised central transition, unlike one spherical dish. Keep dish_depth=0 when replacing the old
spherical treatment. Match visible surface contours; do not use these cuts to invent hidden cavities.'''
DESIGN+=''' Smooth sculpted panels may instead use surface_grid={depths:[[...],...]}, a rectangular
3-to-7 by 3-to-7 grid spanning the panel's XY bounding rectangle. Rows go from front (-Y) to back (+Y),
columns from left (-X) to right (+X). Each entry is a downward offset from the nominal panel top,
nonnegative and below 0.65*panel thickness. It makes one continuous B-spline top over a flat bottom.
Do not combine a surface grid with dish_depth or surface_dishes. Edge fillets on sculpted panels are
not generally supported: use the surface profile itself for visible shaping and leave edge_radius=0.
For a continuously changing oval rail use geometry=profile_sweep and sweep_sections with fraction,
width and depth. Fractions increase from 0 to 1 along the path; use 2..12 sections. Small but positive
terminal sections (minimum 0.2 mm) with gradually larger neighbouring sections form tapered rounded noses.
The broad section remains horizontal relative to local Z. Keep one connected rail instead of separate
floating tip caps. Do not combine profile_sweep with rounded_ends; represent its complete profile.'''
DESIGN+=''' For smooth loft components, represent rounded cross-sections using each section's corner_radius
or ellipse profile. Leave global edge_radius=0 on these lofts; all-edge filleting can fail even at small
radii on smooth loft seams. Only use an edge fillet after an actual successful CAD build validates it.'''


def analyze(gateway,images,notes,reference_views=()):
    config=getattr(gateway,'config',None)
    return gateway.structured('vision',ANALYZE+'\n'+MULTIVIEW,
        dict(notes=notes,reference_views=list(reference_views),reference_image_count=len(images)),PhotoAnalysis,images=images,
        max_output_tokens=getattr(config,'photo_max_output_tokens',12000),
        timeout_seconds=getattr(config,'photo_timeout_seconds',300),reasoning_effort='medium')


def plan(gateway,images,observations,inputs,feedback=None):
    return gateway.structured('design',DESIGN,dict(observations=observations,notes=inputs.get('notes',''),
        known_values=inputs.get('known_values',[]),material_hint=inputs.get('material_hint'),
        correction=feedback),PhotoDesign,images=images,
        max_output_tokens=getattr(getattr(gateway,'config',None),'photo_max_output_tokens',12000),
        timeout_seconds=getattr(getattr(gateway,'config',None),'photo_timeout_seconds',300),
        reasoning_effort=getattr(getattr(gateway,'config',None),'photo_reasoning_effort','low') or None)


def specification(plan,inputs,name):
    values={v.key:v.value for v in plan.values}
    known={v['key']:v['value'] for v in inputs.get('known_values',[])}
    if set(known)-values.keys(): raise AIServiceError('PHOTO_INPUT_NOT_APPLIED','The design omitted a supplied input; it must be corrected')
    if any(values[key]!=value for key,value in known.items()):
        raise AIServiceError('PHOTO_INPUT_CHANGED','The AI changed a supplied input; it must be corrected')
    used=set()
    def collect(value):
        if isinstance(value,str):
            try: used.update(n.id for n in ast.walk(ast.parse(value,mode='eval')) if isinstance(n,ast.Name))
            except SyntaxError: pass
        elif isinstance(value,list):
            for entry in value: collect(entry)
        elif isinstance(value,dict):
            for entry in value.values(): collect(entry)
    for part in plan.parts:
        data=part.model_dump(mode='json')
        collect({k:data[k] for k in ('position','rotation_deg')})
        collect(data.get('edge_radius',0))
        collect(data.get('surface_dishes',[]))
        collect(data.get('surface_grid'))
        collect(data.get('features',[]))
        if part.geometry=='nurbs_panel':
            collect(data['surface_nurbs']);collect(data['size'][2:])
        elif part.geometry=='loft': collect(data['sections'])
        elif part.geometry in ('round_sweep','ellipse_sweep','rect_sweep','profile_sweep'):
            collect(data['path']);collect(data['size'][:1 if part.geometry=='round_sweep' else 2])
            collect(data.get('sweep_sections',[]))
        elif part.geometry=='outline_panel':
            collect(data['outline']);collect(data['size'][2:]);collect(data['dish_depth'])
        else:
            collect(data['size'])
            if part.geometry=='box': collect(data['corner_radius'])
            if part.geometry in ('box','cylinder'): collect(data['dish_depth'])
    for key,value in known.items():
        if key.endswith('_count'):
            term=key.removesuffix('_count').replace('_',' ')
            matches=sum(term in part.label.lower() for part in plan.parts)
            if term=='drawer':
                # A drawer has one front but several separately exported box
                # walls/bottoms; those solids must not inflate its logical count.
                fronts=sum('drawer' in part.label.lower() and 'front' in part.label.lower() for part in plan.parts)
                if fronts:matches=fronts
            if value!=int(value) or matches!=int(value):
                raise AIServiceError('PHOTO_INPUT_NOT_APPLIED',f'The supplied {key} does not match the component plan')
        elif key not in used:
            raise AIServiceError('PHOTO_INPUT_NOT_APPLIED',f'The supplied {key} must drive a geometry expression')
    spec=CabinetSpec(project_name=name,family='photo',photo_design=plan,dimension_provenance='estimated',
        carcass_material='solid_wood',panel_thickness_mm=18,**{k:values[k] for k in ('width_mm','depth_mm','height_mm')})
    provenance={key:dict(value=value,source='USER_PROVIDED' if key in known else 'AI_ESTIMATED',verified=False)
        for key,value in values.items()}
    for key,evidence in inputs.get('dimension_sources',{}).items():
        if key in known:
            provenance[key].update(evidence,value=values[key],verified=False)
    return spec,provenance


def geometry_envelope(build):
    """Primary furniture dimensions; detached companions retain their own scope."""
    from app.furniture_items import dimensions_for,primary_part_ids
    return {axis+'_mm':value for axis,value in dimensions_for(build,primary_part_ids(build)).items()}


def estimate_adjustments(provenance):
    return {key:dict(estimated_value=info['original_estimate'],resolved_value=info['value'])
        for key,info in provenance.items() if info.get('source')=='AI_ESTIMATED' and
        info.get('basis')=='GENERATED_CAD_ENVELOPE' and 'original_estimate' in info}


def retain_estimate_basis(provenance,adjustments):
    for key,change in adjustments.items():
        info=provenance.get(key,{})
        if info.get('source')=='AI_ESTIMATED' and info.get('value')==change['resolved_value']:
            info.update(basis='GENERATED_CAD_ENVELOPE',original_estimate=change['estimated_value'])


def preflight(spec,known,*,check_estimates=True):
    from app.photo_cad import create_photo_furniture
    build=create_photo_furniture(spec)
    for key,actual in geometry_envelope(build).items():
        if key not in known and not check_estimates: continue
        tolerance=max(.5,getattr(spec,key)*.002) if key in known else max(2,getattr(spec,key)*.01)
        if abs(actual-getattr(spec,key))>tolerance:
            raise ValueError(f'Actual {key} envelope {actual:.2f} differs from the design value {getattr(spec,key):.2f}')
    return build


def resolve_estimated_geometry(plan,inputs,name):
    """Reconcile optional overall estimates while retaining the generated physical geometry.

    Rebinding only the affected expressions preserves existing parts, curves and
    user-supplied member dimensions. The resolved plan remains parametric and is
    rebuilt and validated before it can be frozen into an immutable revision.
    """
    from app.photo_schema import PhotoDesign
    spec,provenance=specification(plan,inputs,name)
    known={v['key'] for v in inputs.get('known_values',[])}
    original=preflight(spec,known,check_estimates=False)
    envelope=geometry_envelope(original)
    corrections={key:dict(estimated_value=getattr(spec,key),resolved_value=round(actual,6))
        for key,actual in envelope.items() if key not in known and
        abs(actual-getattr(spec,key))>max(2,getattr(spec,key)*.01)}
    if not corrections: return plan,spec,provenance,original,{}
    ratios={key:change['estimated_value']/change['resolved_value'] for key,change in corrections.items()}

    class BindEstimate(ast.NodeTransformer):
        def visit_Name(self,node):
            if node.id not in ratios: return node
            return ast.copy_location(ast.BinOp(left=node,op=ast.Mult(),right=ast.Constant(ratios[node.id])),node)

    expression_fields={'position','rotation_deg','size','corner_radius','edge_radius','dish_depth','surface_dishes','surface_grid','sweep_sections','outline','path','sections',
                       'features','center','width','depth','depths','radius','radius_x','radius_y','radius_z'}
    def rebind(value):
        if isinstance(value,dict): return {key:rebind(item) if key in expression_fields else item for key,item in value.items()}
        if isinstance(value,list): return [rebind(item) for item in value]
        if isinstance(value,str):
            tree=ast.parse(value,mode='eval')
            if any(isinstance(node,ast.Name) and node.id in ratios for node in ast.walk(tree)):
                return ast.unparse(BindEstimate().visit(tree))
        return value

    raw=plan.model_dump(mode='json')
    raw['parts']=[rebind(part) for part in raw['parts']]
    for value in raw['values']:
        change=corrections.get(value['key'])
        if change:
            value['value']=change['resolved_value']
            value['explanation']=(value['explanation'][:160]+f" Unverified estimate reconciled from {change['estimated_value']:g} "
                f"to {change['resolved_value']:g} mm using the generated CAD envelope; not a physical measurement.")[:400]
    note='Optional overall estimates reconciled to the generated CAD envelope without resizing parts: '+', '.join(
        f"{key} {change['estimated_value']:g} -> {change['resolved_value']:g} mm" for key,change in corrections.items())+'. Still unverified.'
    if len(raw['assumptions'])<30: raw['assumptions'].append(note)
    else: raw['assumptions'][-1]+=' '+note
    resolved=PhotoDesign.model_validate(raw)
    spec,provenance=specification(resolved,inputs,name)
    rebuilt=preflight(spec,known)
    # Reconciliation may change estimated metadata, never the actual design or a supplied constraint.
    for pid,shape in original.shapes.items():
        before,after=shape.val(),rebuilt.shapes[pid].val()
        from app.geometry import exact_bounds
        a,b=exact_bounds(before),exact_bounds(after)
        if (not math.isclose(before.Volume(),after.Volume(),rel_tol=1e-8,abs_tol=1e-5) or
            any(not math.isclose(getattr(a,axis+edge),getattr(b,axis+edge),abs_tol=1e-5)
                for axis in 'xyz' for edge in ('min','max'))):
            raise ValueError('Estimated-dimension reconciliation changed a component; revise its geometry plan')
    for key,change in corrections.items():
        provenance[key].update(basis='GENERATED_CAD_ENVELOPE',original_estimate=change['estimated_value'])
    return resolved,spec,provenance,rebuilt,corrections
