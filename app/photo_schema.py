"""Photo-derived geometry data. Contains no executable CAD or Python programs."""
import ast
import math
import operator
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PhotoModel(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,validate_default=True)


# Estimate reconciliation adds bounded numeric scale factors to existing
# expressions. Keep room for those factors; number() still enforces the tighter
# AST node/depth budget and the same non-executable arithmetic allowlist.
Expression=float | Annotated[str,Field(min_length=1,max_length=512)]
Point=Annotated[list[Expression],Field(min_length=3,max_length=3)]
Point2=Annotated[list[Expression],Field(min_length=2,max_length=2)]
Color=Annotated[float,Field(ge=0,le=1)]
Key=Annotated[str,Field(pattern=r'^[a-z][a-z0-9_]{0,59}$')]


def number(expression,values):
    """Evaluate bounded arithmetic, never eval/exec, calls, paths or attributes."""
    if isinstance(expression,(int,float)):
        result=float(expression)
    else:
        try:tree=ast.parse(expression,mode='eval')
        except SyntaxError as exc:raise ValueError('Invalid geometry arithmetic syntax') from exc
        if sum(1 for _ in ast.walk(tree))>48: raise ValueError('Geometry expression is too complex')
        operations={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
        def read(node,depth=0):
            if depth>12: raise ValueError('Geometry expression is too deep')
            if isinstance(node,ast.Constant) and type(node.value) in (float,int): return node.value
            if isinstance(node,ast.Name) and node.id in values: return values[node.id]
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
                return read(node.operand,depth+1)*(1 if isinstance(node.op,ast.UAdd) else -1)
            if isinstance(node,ast.BinOp) and type(node.op) in operations:
                left,right=read(node.left,depth+1),read(node.right,depth+1)
                if isinstance(node.op,ast.Div) and right==0:raise ValueError('Geometry arithmetic cannot divide by zero')
                value=operations[type(node.op)](left,right)
                # Products in dimension ratios may exceed the final coordinate
                # bound, e.g. 530*255/530. Keep arithmetic and final bounds separate.
                if not math.isfinite(value) or abs(value)>1e12: raise ValueError('Geometry intermediate value exceeds limits')
                return value
            raise ValueError('Only supplied parameters and simple arithmetic are allowed')
        result=read(tree.body)
    if not math.isfinite(result) or abs(result)>100000: raise ValueError('Geometry value exceeds limits')
    return float(result)


class InputValue(PhotoModel):
    key: Key
    value: float = Field(ge=-5000,le=10000)


class PhotoInput(PhotoModel):
    key: Key
    label: str = Field(min_length=1,max_length=90)
    unit: Literal['mm','degrees','count']
    estimated_value: float = Field(ge=-5000,le=10000)
    explanation: str = Field(min_length=1,max_length=400)


class ResolvedValue(InputValue):
    explanation: str = Field(min_length=1,max_length=400)


class LoftSection(PhotoModel):
    center: Point
    width: Expression
    depth: Expression
    profile: Literal['rectangle','ellipse'] = 'rectangle'
    corner_radius: Expression = Field(default=0,exclude_if=lambda value:value==0)


class CADFeature(PhotoModel):
    operation: Literal['hole','groove','pocket','fillet','chamfer','dowel','dado','rabbet','mortise','tenon','confirmat']
    position: Point = (0,0,0)
    size: Point = (1,1,1)
    axis: Literal['X','Y','Z'] = 'Z'
    radius: Expression = 1
    depth: Expression = 1
    edge_selector: Literal['vertical_corners','top_edges','all_edges'] = 'vertical_corners'
    evidence: str = Field(default='Unverified design feature; requires joinery and manufacturing review',max_length=500)


class SurfaceDish(PhotoModel):
    center: Point2
    radius_x: Expression
    radius_y: Expression
    radius_z: Expression
    depth: Expression


class SurfaceGrid(PhotoModel):
    # Rows run front-to-back (local Y), columns left-to-right (local X).
    # Values are downward distances from the nominal panel top, in millimetres.
    depths: list[Annotated[list[Expression],Field(min_length=3,max_length=7)]] = Field(min_length=3,max_length=7)

    @model_validator(mode='after')
    def rectangular(self):
        if len({len(row) for row in self.depths})!=1: raise ValueError('Surface grid must be rectangular')
        return self


class SweepSection(PhotoModel):
    fraction: float = Field(ge=0,le=1)
    width: Expression
    depth: Expression


class RationalSurface(PhotoModel):
    control_points: list[Annotated[list[Point],Field(min_length=2,max_length=8)]] = Field(min_length=2,max_length=8)
    weights: list[Annotated[list[Expression],Field(min_length=2,max_length=8)]] | None = None

    @model_validator(mode='after')
    def rectangular_grid(self):
        if len({len(row) for row in self.control_points}) != 1:
            raise ValueError('NURBS control grid must be rectangular')
        if self.weights is not None and (len(self.weights)!=len(self.control_points) or
            any(len(row)!=len(self.control_points[0]) for row in self.weights)):
            raise ValueError('NURBS weights must match the control grid')
        return self


class PhotoPart(PhotoModel):
    part_id: str = Field(pattern=r'^F[0-9]{3}$')
    label: str = Field(min_length=1,max_length=120)
    evidence: str = Field(min_length=1,max_length=500)
    geometry: Literal['box','cylinder','ellipsoid','outline_panel','loft','round_sweep','ellipse_sweep','rect_sweep','profile_sweep','nurbs_panel']
    material: Literal['solid_wood','plywood','mdf','glass','metal','plastic','foam','fabric']
    color_rgb: Annotated[list[Color],Field(min_length=3,max_length=3)]
    position: Point = (0,0,0)
    rotation_deg: Point = (0,0,0)
    size: Point = (1,1,1)
    corner_radius: Expression = 0
    edge_radius: Expression = Field(default=0,exclude_if=lambda value:value==0)
    rounded_ends: bool = Field(default=False,exclude_if=lambda value:not value)
    sweep_frame: Literal['LEGACY','WORLD_UP'] = Field(default='LEGACY',exclude_if=lambda value:value=='LEGACY')
    loft_mode: Literal['SMOOTH','BOUNDED'] = Field(default='SMOOTH',exclude_if=lambda value:value=='SMOOTH')
    panel_frame: Literal['LEGACY','BOUNDS'] = Field(default='LEGACY',exclude_if=lambda value:value=='LEGACY')
    dish_depth: Expression = 0
    surface_dishes: list[SurfaceDish] = Field(default_factory=list,max_length=8,exclude_if=lambda value:not value)
    surface_grid: SurfaceGrid | None = Field(default=None,exclude_if=lambda value:value is None)
    surface_nurbs: RationalSurface | None = Field(default=None,exclude_if=lambda value:value is None)
    sweep_sections: list[SweepSection] = Field(default_factory=list,max_length=12,exclude_if=lambda value:not value)
    outline: list[Point2] = Field(default_factory=list,max_length=37)
    path: list[Point] = Field(default_factory=list,max_length=25)
    curve_mode: Literal['polyline','bezier'] = 'polyline'
    sections: list[LoftSection] = Field(default_factory=list,max_length=6)
    features: list[CADFeature] = Field(default_factory=list,max_length=24,exclude_if=lambda value:not value)


class SeparateFurnitureItem(PhotoModel):
    item_id: str = Field(pattern=r'^A[0-9]{3}$')
    label: str = Field(min_length=1,max_length=120)
    part_ids: list[Annotated[str,Field(pattern=r'^F[0-9]{3}$')]] = Field(min_length=1,max_length=60)
    width_key: Key
    depth_key: Key
    height_key: Key
    evidence: str = Field(min_length=1,max_length=500)


class PhotoDesign(PhotoModel):
    furniture_type: str = Field(min_length=1,max_length=120)
    summary: str = Field(min_length=1,max_length=2400)
    values: list[ResolvedValue] = Field(min_length=3,max_length=60)
    parts: list[PhotoPart] = Field(min_length=1,max_length=60)
    assumptions: list[str] = Field(min_length=1,max_length=30)
    unrepresented_features: list[str] = Field(default_factory=list,max_length=30)
    separate_items: list[SeparateFurnitureItem] = Field(default_factory=list,max_length=8,exclude_if=lambda value:not value)

    @model_validator(mode='before')
    @classmethod
    def normalize_single_item(cls, data):
        if not isinstance(data, dict):
            return data
        items = data.get('separate_items') or []
        parts = data.get('parts') or []
        if len(items) == 1 and len(parts) > 0:
            item = items[0]
            part_ids = {part.get('part_id') for part in parts if isinstance(part, dict)}
            if (set(item.get('part_ids', [])) == part_ids and
                    {item.get('width_key'), item.get('depth_key'), item.get('height_key')} ==
                    {'width_mm', 'depth_mm', 'height_mm'}):
                data = dict(data)
                data['separate_items'] = []
        if parts:
            data = dict(data)
            normalized_parts = []
            repairs = []
            for part in parts:
                part = cls.repair_part(part, repairs)
                if (isinstance(part, dict) and part.get('geometry') == 'rect_sweep' and
                        part.get('sweep_sections')):
                    part = dict(part)
                    part['geometry'] = 'profile_sweep'
                normalized_parts.append(part)
            data['parts'] = normalized_parts
            assumptions = list(data.get('assumptions') or [])
            if repairs and len(assumptions) < 30:
                assumptions.append('Local corrections applied to the model output: ' + '; '.join(repairs[:6])[:400])
                data['assumptions'] = assumptions
        return data

    @staticmethod
    def repair_part(part, repairs):
        """Fix unambiguous model slips locally so they never cost a repair request."""
        if not isinstance(part, dict):
            return part
        part = dict(part)
        name = part.get('part_id', '?')
        if part.get('geometry') in ('rect_sweep', 'ellipse_sweep') and part.get('sweep_sections'):
            # Varying sections are a profile sweep (elliptical sections, same as ellipse_sweep).
            part['geometry'] = 'profile_sweep'
            repairs.append(f'{name} treated as a profile sweep because it has sweep sections')
        if part.get('rounded_ends') and part.get('geometry') not in ('round_sweep', 'ellipse_sweep'):
            part['rounded_ends'] = False
            repairs.append(f'{name} rounded ends removed (needs a round or oval sweep)')
        # Size slots the CAD builder never reads (lofts and sweeps are defined by
        # sections or paths) only need a valid placeholder, not a real dimension.
        unused = {'loft': (0, 1, 2), 'profile_sweep': (0, 1, 2), 'round_sweep': (1, 2),
                  'ellipse_sweep': (2,), 'rect_sweep': (2,)}.get(part.get('geometry'), ())
        size = part.get('size')
        if unused and isinstance(size, (list, tuple)) and len(size) == 3:
            size = list(size)
            for slot in unused:
                value = size[slot]
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not .2 <= value <= 10000:
                    size[slot] = 1.
                    repairs.append(f'{name} unused size slot {slot} set to a 1 mm placeholder')
            part['size'] = size
        return part

    @model_validator(mode='after')
    def check_plan(self):
        values={v.key:v.value for v in self.values}
        if len(values)!=len(self.values): raise ValueError('Duplicate design parameter')
        if sum(len(part.features)+len(part.surface_dishes) for part in self.parts)>120: raise ValueError('CAD feature budget exceeded')
        if not {'width_mm','depth_mm','height_mm'}<=values.keys(): raise ValueError('Overall dimensions are required')
        if not 150<values['width_mm']<=5000 or not 80<values['depth_mm']<=3000 or not 100<values['height_mm']<=3000:
            raise ValueError('Overall dimensions are outside the supported range')
        if [part.part_id for part in self.parts]!=[f'F{i+1:03d}' for i in range(len(self.parts))]:
            raise ValueError('Use consecutive unique part identifiers starting at F001')
        assigned=[];item_ids=[];part_ids={p.part_id for p in self.parts}
        for item in self.separate_items:
            item_ids.append(item.item_id);assigned.extend(item.part_ids)
            if not set(item.part_ids)<=part_ids:raise ValueError('A separate furniture item references unknown parts')
            keys=[item.width_key,item.depth_key,item.height_key]
            if len(set(keys))!=3 or any(k not in values or not k.endswith('_mm') or k in ('width_mm','depth_mm','height_mm') or values[k]<=0 for k in keys):
                raise ValueError('Separate furniture items require their own positive width/depth/height parameters')
        if len(set(item_ids))!=len(item_ids) or len(set(assigned))!=len(assigned):
            raise ValueError('Separate furniture items and their parts must be unique')
        if assigned and not part_ids-set(assigned):raise ValueError('A primary furniture item must remain')
        for part in self.parts:
            if (part.geometry=='nurbs_panel') != (part.surface_nurbs is not None):
                raise ValueError('nurbs_panel requires a rational surface definition')
            if part.surface_nurbs:
                if part.surface_grid or part.surface_dishes or number(part.dish_depth,values):
                    raise ValueError('A NURBS panel cannot combine other dishing operations')
                for row in part.surface_nurbs.control_points:
                    for point in row:
                        for coordinate in point: number(coordinate,values)
                if part.surface_nurbs.weights:
                    if any(not .05<=number(w,values)<=20 for row in part.surface_nurbs.weights for w in row):
                        raise ValueError('NURBS rational weights must be within 0.05..20')
            if part.surface_grid:
                if part.geometry not in ('box','outline_panel','cylinder') or part.surface_dishes or number(part.dish_depth,values):
                    raise ValueError('A surface grid replaces other panel dishing operations')
                depths=[number(d,values) for row in part.surface_grid.depths for d in row]
                if min(depths)<0 or max(depths)>=number(part.size[2],values)*.65:
                    raise ValueError('Surface grid depths must retain at least 35 percent panel thickness')
            if part.geometry=='profile_sweep':
                sections=part.sweep_sections
                if len(sections)<2 or sections[0].fraction!=0 or sections[-1].fraction!=1:
                    raise ValueError('A profile sweep needs sections at both path ends')
                if any(a.fraction>=b.fraction for a,b in zip(sections,sections[1:])):
                    raise ValueError('Sweep section fractions must increase strictly')
                for section in sections:
                    if not all(.2<=number(d,values)<=10000 for d in (section.width,section.depth)):
                        raise ValueError('Sweep section dimensions are outside the supported range')
            elif part.sweep_sections: raise ValueError('Sweep sections require profile_sweep geometry')
            for dish in part.surface_dishes:
                if part.geometry not in ('box','outline_panel','cylinder'): raise ValueError('Surface dishes require a seat/panel surface')
                for scalar in (*dish.center,dish.radius_x,dish.radius_y,dish.radius_z,dish.depth): number(scalar,values)
                if min(number(v,values) for v in (dish.radius_x,dish.radius_y,dish.radius_z,dish.depth))<=0:
                    raise ValueError('Surface dish dimensions must be positive')
                if number(dish.depth,values)>=min(number(part.size[2],values)*.7,number(dish.radius_z,values)):
                    raise ValueError('Surface dish depth must leave a continuous seat/panel')
            for feature in part.features:
                for scalar in (*feature.position,*feature.size,feature.radius,feature.depth): number(scalar,values)
                if feature.operation in ('fillet','chamfer'):
                    invalid = number(feature.radius,values)<=0
                else:
                    invalid = (min(number(x,values) for x in feature.size)<=0 or
                               number(feature.radius,values)<=0 or number(feature.depth,values)<=0)
                if invalid:
                    raise ValueError('Feature dimensions must be positive')
            for point in [part.position,part.rotation_deg,part.size,*part.path,*part.outline]:
                for coordinate in point: number(coordinate,values)
            for dimension in part.size:
                if not .2<=number(dimension,values)<=10000: raise ValueError('Part size is outside the supported range')
            for scalar in (part.corner_radius,part.dish_depth,part.edge_radius):
                if number(scalar,values)<0: raise ValueError('Profile radius and dish depth cannot be negative')
            for section in part.sections:
                for scalar in (*section.center,section.width,section.depth,section.corner_radius): number(scalar,values)
                if min(number(section.width,values),number(section.depth,values))<.2: raise ValueError('Loft section is too small')
                radius=number(section.corner_radius,values)
                if radius<0 or (radius and (section.profile!='rectangle' or radius>=min(number(section.width,values),number(section.depth,values))/2)):
                    raise ValueError('Loft corner radius must fit its rectangular section')
            if part.rounded_ends and part.geometry not in ('round_sweep','ellipse_sweep'):
                raise ValueError('Rounded ends require a round or oval swept member')
            if part.geometry=='outline_panel' and len(part.outline)<3: raise ValueError('An outline panel needs its photographed contour')
            if part.geometry in ('round_sweep','ellipse_sweep','rect_sweep','profile_sweep') and len(part.path)<2: raise ValueError('A curved member needs a path')
            if part.geometry=='loft' and len(part.sections)<2: raise ValueError('A loft needs at least two sections')
            for points in (part.outline if part.geometry=='outline_panel' else [],part.path):
                if points and part.curve_mode=='bezier' and (len(points)<4 or (len(points)-1)%3):
                    raise ValueError('Cubic Bezier chains require 3n+1 control points')
        return self
