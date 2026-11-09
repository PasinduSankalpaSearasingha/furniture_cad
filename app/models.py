from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .photo_schema import PhotoDesign


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Material(str, Enum):
    plywood = 'plywood'
    mdf = 'mdf'
    solid_wood = 'solid_wood'
    glass = 'glass'
    metal = 'metal'
    plastic = 'plastic'
    foam = 'foam'
    fabric = 'fabric'

class MaterialProfile(StrictModel):
    material_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    name: str = Field(min_length=1, max_length=120)
    material: Material
    actual_thickness_mm: float = Field(gt=0, le=100)
    nominal_thickness_mm: float | None = Field(default=None, gt=0)
    stock_size_mm: tuple[float, float] | None = None
    species: str | None = None
    moisture_percent: float | None = Field(default=None, ge=0, le=100)
    movement_review: str | None = None
    edge_treatment: str | None = None
    finish: str | None = None
    glass_type: str | None = None
    retention_reference: str | None = None
    safety_reference: str | None = None
    supplier_reference: str | None = None
    # Added to length, width, thickness, respectively. No implicit machining allowance.
    blank_allowance_mm: tuple[float, float, float] | None = None
    grain_direction: Literal['length', 'width', 'none'] | None = None
    approved: bool = False
    evidence_reference: str | None = None

    @model_validator(mode='after')
    def check_profile(self):
        if self.stock_size_mm and any(not 0 < v <= 100000 for v in self.stock_size_mm):
            raise ValueError('Stock dimensions must lie within the supported 0..100000 mm range')
        if self.blank_allowance_mm and any(not 0 <= v <= 1000 for v in self.blank_allowance_mm):
            raise ValueError('Blank allowances must lie within the supported 0..1000 mm range')
        if self.approved and not self.evidence_reference:
            raise ValueError('Approval requires a traceable evidence reference')
        return self


class HardwareProfile(StrictModel):
    hardware_id: str = Field(pattern=r'^[A-Za-z0-9_.-]+$', max_length=80)
    manufacturer: str = Field(min_length=1)
    supplier_reference: str = Field(min_length=1)
    product_code: str = Field(min_length=1)
    hardware_type: Literal['hinge', 'handle', 'shelf_support', 'connector', 'glass_retainer']
    compatible_materials: list[Material] = Field(min_length=1)
    quantity: int = Field(gt=0, le=10000)
    part_ids: list[str] = Field(min_length=1)
    installation_reference: str | None = None
    approved: bool = False
    evidence_reference: str | None = None


class JointProfile(StrictModel):
    profile_id: str = Field(min_length=1, max_length=80)
    joint_type: Literal['dowel', 'mortise_tenon', 'dado', 'rabbet', 'mechanical', 'door_frame']
    compatible_materials: list[Material] = Field(min_length=1)
    approved: bool = False
    evidence_reference: str | None = None
    diameter_mm: float | None = Field(default=None, gt=0, le=100)
    depths_mm: tuple[float, float] | None = None
    clearance_mm: float | None = Field(default=None, ge=0, le=10)
    tolerance_mm: float | None = Field(default=None, gt=0, le=5)
    cross_size_mm: tuple[float,float] | None = Field(default=None,exclude_if=lambda value:value is None)
    mechanical_type: Literal['confirmat'] | None = Field(default=None,exclude_if=lambda value:value is None)
    pilot_diameter_mm: float | None = Field(default=None,gt=0,le=100,exclude_if=lambda value:value is None)


class JointSpec(StrictModel):
    joint_id: str = Field(min_length=1, max_length=80)
    part_ids: tuple[str, str]
    profile: JointProfile
    # Assembly coordinates; direction points into the first component.
    origin_xyz_mm: tuple[float, float, float]
    direction_xyz: tuple[float, float, float]


class DisplayLayout(StrictModel):
    """Explicit proportions for an estimated, two-section photograph study."""
    style: Literal['two_section_decorative'] = 'two_section_decorative'
    lower_section_height_mm: float = Field(default=640,gt=300,le=1600)
    base_height_mm: float = Field(default=60,ge=20,le=150)
    cornice_height_mm: float = Field(default=70,ge=30,le=180)
    transition_height_mm: float = Field(default=30,ge=10,le=90)
    body_inset_mm: float = Field(default=32,ge=20,le=90)
    post_width_mm: float = Field(default=42,ge=25,le=90)
    ornament_strip_width_mm: float = Field(default=18,ge=8,le=35)
    include_ornaments: bool = True
    reference_filename: str = Field(default='',max_length=120)
    reference_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class FurnitureParameters(StrictModel):
    seat_height_mm: float = Field(default=450, ge=100, le=1100)
    seat_thickness_mm: float = Field(default=30, ge=12, le=150)
    leg_width_mm: float = Field(default=40, ge=15, le=150)
    leg_taper_ratio: float = Field(default=0.72, ge=0.35, le=1)
    leg_splay_mm: float = Field(default=12, ge=0, le=100)
    leg_curve_mm: float = Field(default=0, ge=0, le=60)
    backrest: bool = True
    back_slats: int = Field(default=3, ge=0, le=12)
    armrests: bool = False
    arm_height_mm: float = Field(default=660, ge=200, le=1100)
    rail_diameter_mm: float = Field(default=30, ge=12, le=80)
    curve_profile: Literal['straight', 'arched', 'wraparound', 'wave'] = 'straight'
    curve_depth_mm: float = Field(default=35, ge=0, le=200)
    seat_shape: Literal['rectangle', 'rounded', 'ellipse', 'sculpted'] = 'rounded'
    top_shape: Literal['rectangle', 'rounded', 'ellipse'] = 'rectangle'
    shelves: int = Field(default=2, ge=0, le=10)
    doors: int = Field(default=0, ge=0, le=4)
    drawers: int = Field(default=0, ge=0, le=8)
    compartments: int = Field(default=1, ge=1, le=4)
    headboard_height_mm: float = Field(default=850, ge=300, le=1600)
    slats: int = Field(default=10, ge=4, le=24)
    cushion_thickness_mm: float = Field(default=120, ge=40, le=250)
    seats: int = Field(default=2, ge=1, le=4)
    pedestal: bool = False


class CabinetSpec(StrictModel):
    project_name: str = Field(default='Display Cabinet', min_length=1, max_length=120)
    width_mm: float = Field(gt=150, le=5000)
    depth_mm: float = Field(gt=80, le=3000)
    height_mm: float = Field(gt=100, le=3000)
    carcass_material: Literal['plywood', 'mdf', 'solid_wood']
    panel_thickness_mm: float = Field(gt=11, le=50)
    back_thickness_mm: float = Field(default=6, gt=2, le=30)
    include_glazed_door: bool = False
    door_frame_wood: Literal['solid_wood', 'plywood', 'mdf'] = 'solid_wood'
    door_frame_width_mm: float = Field(default=55, gt=25, le=140)
    door_frame_depth_mm: float = Field(default=24, gt=10, le=60)
    glass_thickness_mm: float = Field(default=6, gt=2, le=20)
    door_perimeter_gap_mm: float = Field(default=3, ge=1, le=8)
    joint_type: Literal['none', 'dowel_demo'] = 'none'
    measured_dimensions_confirmed: bool = False
    revision: int = Field(default=1, ge=1)
    family: Literal['cabinet','chair','stool','bench','table','storage','bed','sofa','screen','rack','photo'] = 'cabinet'
    photo_design: PhotoDesign | None = Field(default=None,exclude_if=lambda value:value is None)
    furniture_template: str | None = Field(default=None, max_length=80, exclude_if=lambda value:value is None)
    furniture_parameters: FurnitureParameters | None = Field(default=None, exclude_if=lambda value:value is None)
    dimension_provenance: Literal['unverified', 'estimated', 'user_provided', 'physically_verified'] = 'unverified'
    measurement_reference: str | None = None
    material_profiles: list[MaterialProfile] = Field(default_factory=list, max_length=100)
    # Keys: carcass, back, door_frame, glass. IDs resolve to immutable profile snapshots.
    material_assignments: dict[str, str] = Field(default_factory=dict)
    hardware: list[HardwareProfile] = Field(default_factory=list, max_length=200)
    joints: list[JointSpec] = Field(default_factory=list, max_length=500)
    glass_edge_clearance_mm: float | None = Field(default=None, ge=0, le=20)
    door_standoff_mm: float | None = Field(default=None, ge=0, le=50)
    # Omit the absent extension so pre-existing basic specification hashes stay stable.
    display_layout: DisplayLayout | None = Field(default=None,exclude_if=lambda value:value is None)

    @model_validator(mode='after')
    def check_geometry(self):
        t=self.panel_thickness_mm
        if self.family=='photo':
            if self.photo_design is None or self.furniture_template or self.furniture_parameters or self.display_layout:
                raise ValueError('A photo-derived design needs its component plan')
            if self.measured_dimensions_confirmed or self.dimension_provenance=='physically_verified' or self.joints or self.include_glazed_door:
                raise ValueError('Photo reconstruction is an unverified review model')
            values={v.key:v.value for v in self.photo_design.values}
            for key in ('width_mm','depth_mm','height_mm'):
                if getattr(self,key)!=values[key]: raise ValueError('Overall dimensions differ from the photo plan')
            return self
        if self.photo_design is not None: raise ValueError('Photo geometry requires the photo design family')
        if self.family!='cabinet':
            from .furniture_catalog import template
            entry=template(self.furniture_template)
            if not entry or entry['family']!=self.family or self.furniture_parameters is None:
                raise ValueError('Select a known template and its furniture parameters')
            if self.measured_dimensions_confirmed or self.dimension_provenance=='physically_verified':
                raise ValueError('Furniture library models are estimated review studies')
            if self.display_layout or self.include_glazed_door or self.joints or self.joint_type!='none':
                raise ValueError('Cabinet glazing and machining options cannot be applied to furniture studies')
            if min(self.width_mm,self.depth_mm)<=4*t:
                raise ValueError('Furniture dimensions are too small for the selected stock thickness')
            p=self.furniture_parameters
            if self.family in ('chair','stool','bench'):
                if p.seat_height_mm>self.height_mm or p.seat_height_mm<=p.seat_thickness_mm+60:
                    raise ValueError('Seat height must fit between the legs and overall height')
                if p.armrests and not p.seat_height_mm+p.rail_diameter_mm < p.arm_height_mm < self.height_mm:
                    raise ValueError('Arm height must be between the seat and overall height')
                if p.leg_width_mm*3+2*p.leg_splay_mm >= min(self.width_mm,self.depth_mm):
                    raise ValueError('Leg widths and splay leave insufficient seat space')
                if self.height_mm-p.seat_height_mm < p.rail_diameter_mm and self.family=='chair':
                    raise ValueError('Chair back needs room above the seat')
            if self.family=='storage' and min(self.width_mm/self.furniture_parameters.compartments,self.height_mm/(p.shelves+2))<=2*t+10:
                raise ValueError('Too many compartments or shelves for these dimensions')
            if self.family=='storage' and p.drawers and (p.doors or p.shelves or p.compartments!=1):
                raise ValueError('Drawer studies require one compartment without doors or shelves')
            return self
        if self.furniture_template or self.furniture_parameters:
            raise ValueError('Use a furniture family for furniture library parameters')
        if not (300<self.width_mm<=3000 and 200<self.depth_mm<=1500 and 450<self.height_mm<=3000):
            raise ValueError('Cabinet dimensions are outside the supported range')
        if self.width_mm <= 2*t + 160 or self.height_mm <= 2*t + 200:
            raise ValueError('Insufficient inner width/height for panel thickness')
        if self.back_thickness_mm >= self.depth_mm / 3:
            raise ValueError('Back panel too thick for depth')
        if self.include_glazed_door:
            door_w=self.width_mm-2*t-2*self.door_perimeter_gap_mm
            door_h=self.height_mm-2*t-2*self.door_perimeter_gap_mm
            if door_w <= 2*self.door_frame_width_mm + 80 or door_h <= 2*self.door_frame_width_mm + 120:
                raise ValueError('Glazed door opening too small')
            c = self.glass_edge_clearance_mm if self.glass_edge_clearance_mm is not None else 1
            if min(door_w, door_h) <= 2*self.door_frame_width_mm + 2*c:
                raise ValueError('Glass clearance consumes opening')
        if self.joint_type == 'dowel_demo' and t < 16:
            raise ValueError('Dowel demonstration requires panel thickness >= 16 mm')
        ids = [m.material_id for m in self.material_profiles]
        if len(set(ids)) != len(ids):
            raise ValueError('Material IDs must be unique')
        if set(self.material_assignments) - {'carcass', 'back', 'door_frame', 'glass'}:
            raise ValueError('Unknown material assignment role')
        if set(self.material_assignments.values()) - set(ids):
            raise ValueError('Material assignment references missing profile')
        if len({j.joint_id for j in self.joints}) != len(self.joints):
            raise ValueError('Joint IDs must be unique')
        if self.joint_type == 'dowel_demo' and self.joints:
            raise ValueError('Do not mix demo bores with engineering joints')
        if self.display_layout:
            layout = self.display_layout
            if not self.include_glazed_door or self.joint_type!='none' or self.joints:
                raise ValueError('The image-study template requires glazed doors and no machining joints')
            if self.measured_dimensions_confirmed or self.dimension_provenance=='physically_verified':
                raise ValueError('The image-study template is an unverified review model')
            body_w = self.width_mm-2*layout.body_inset_mm
            body_d = self.depth_mm-2*layout.body_inset_mm
            gap,fw = self.door_perimeter_gap_mm,self.door_frame_width_mm
            lower_opening = layout.lower_section_height_mm-layout.base_height_mm-2*t-2*gap
            upper_opening = (self.height_mm-layout.cornice_height_mm-layout.lower_section_height_mm-
                             layout.transition_height_mm-2*layout.post_width_mm-2*gap)
            if min(body_w-2*t,body_w-2*layout.post_width_mm)-2*gap <= 2*fw+80:
                raise ValueError('Image-study door openings are too narrow')
            if min(lower_opening,upper_opening) <= 2*fw+120:
                raise ValueError('Image-study door openings are too short')
            if body_d <= 2*layout.post_width_mm+80:
                raise ValueError('Image-study side glazing opening is too shallow')
            if layout.body_inset_mm < self.door_frame_depth_mm+(self.door_standoff_mm or 1):
                raise ValueError('Body inset must contain the door within the stated overall depth')
        return self

class Part(StrictModel):
    part_id: str
    label: str
    material: Material
    category: Literal['panel','frame','glass']
    # Manufacturing blank dimensions, NOT inferred from world bounding box.
    blank_length_mm: float
    blank_width_mm: float
    blank_thickness_mm: float
    size_xyz_mm: tuple[float,float,float]
    center_xyz_mm: tuple[float,float,float]
    grain_axis: Literal['length', 'width', 'none'] = 'none'
    note: str = ''
    material_id: str = 'UNSPECIFIED'
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    thickness_mm: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    manufacturing_status: str = 'DRAFT'
    blank_verified: bool = False
    edge_treatment: str | None = None
    color_rgb: tuple[float,float,float] | None = Field(default=None,exclude_if=lambda value:value is None)
    length_axis: tuple[float, float, float] = (1, 0, 0)
    thickness_axis: tuple[float, float, float] = (0, 0, 1)

    @property
    def part_name(self):
        return self.label

class ManufacturingOperation(StrictModel):
    part_id: str
    operation: str
    axis: str
    origin_xyz_mm: tuple[float,float,float]
    diameter_mm: float | None
    depth_mm: float
    status: str = 'UNVERIFIED_DEMO'
    joint_id: str | None = None
    profile_id: str | None = None
    coordinate_system: str | None = Field(default=None,exclude_if=lambda value:value is None)
    feature_dimensions_mm: tuple[float,float,float] | None = Field(default=None,exclude_if=lambda value:value is None)
    evidence: str | None = Field(default=None,exclude_if=lambda value:value is None)
