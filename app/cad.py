from dataclasses import dataclass, field
from pathlib import Path
import cadquery as cq
from .models import CabinetSpec, Material, Part, ManufacturingOperation
from .registry import PartsRegistry

@dataclass
class BuildResult:
    assembly: cq.Assembly
    parts: list[Part]
    shapes: dict[str, cq.Workplane]
    operations: list[ManufacturingOperation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    registry: PartsRegistry = field(default_factory=PartsRegistry)
    local_shapes: dict[str, cq.Workplane] = field(default_factory=dict)
    joint_issues: list[dict] = field(default_factory=list)
    mesh_tolerance_mm: float | None = None
    furniture_items: list[dict] = field(default_factory=list)
    construction_adjustments: list[dict] = field(default_factory=list)


def create_cabinet(spec: CabinetSpec) -> BuildResult:
    """Concept carcass with optional conceptual glazed door and dowel-hole demo.

    Coordinates: X left->right, Y front->back, Z bottom->top, all millimetres.
    IMPORTANT: This is not a fully engineered manufacturing model.
    """
    if spec.display_layout is not None:
        from .display_cad import create_display_cabinet
        return create_display_cabinet(spec)
    W,D,H=spec.width_mm,spec.depth_mm,spec.height_mm
    t,b=spec.panel_thickness_mm,spec.back_thickness_mm
    parts: list[Part]=[]
    shapes={}
    operations=[]
    registry = PartsRegistry()
    local_shapes = {}
    profiles = {p.material_id: p for p in spec.material_profiles}

    def put(pid,label,material,category,xyz,center,blank,grain='none',note='',
            role='carcass', length_axis=(1,0,0), thickness_axis=(0,0,1)):
        profile = profiles.get(spec.material_assignments.get(role))
        allowance = profile.blank_allowance_mm if profile and profile.blank_allowance_mm is not None else (0,0,0)
        part=Part(part_id=pid,label=label,material=material,category=category,
            blank_length_mm=blank[0]+allowance[0],blank_width_mm=blank[1]+allowance[1],
            blank_thickness_mm=blank[2]+allowance[2],
            length_mm=blank[0],width_mm=blank[1],thickness_mm=blank[2],
            size_xyz_mm=xyz,center_xyz_mm=center,
            grain_axis=profile.grain_direction if profile and profile.grain_direction else grain,note=note,
            material_id=profile.material_id if profile else 'UNSPECIFIED',revision=spec.revision,
            blank_verified=bool(profile and profile.approved and profile.blank_allowance_mm is not None),
            edge_treatment=profile.edge_treatment if profile else None,
            length_axis=length_axis,thickness_axis=thickness_axis)
        parts.append(part)
        registry.add(part)
        local_shapes[pid] = cq.Workplane('XY').box(*blank)
        shapes[pid] = rectangular_component(part)

    mat=Material(spec.carcass_material)
    put('P001','Left side',mat,'panel',(t,D,H),(t/2,D/2,H/2),(H,D,t),'length',length_axis=(0,0,1),thickness_axis=(-1,0,0))
    put('P002','Right side',mat,'panel',(t,D,H),(W-t/2,D/2,H/2),(H,D,t),'length',length_axis=(0,0,1),thickness_axis=(-1,0,0))
    put('P003','Top panel',mat,'panel',(W-2*t,D,t),(W/2,D/2,H-t/2),(W-2*t,D,t),'length')
    put('P004','Bottom panel',mat,'panel',(W-2*t,D,t),(W/2,D/2,t/2),(W-2*t,D,t),'length')
    put('P005','Inset back (illustrative)',mat,'panel',(W-2*t,b,H-2*t),
        (W/2,D-b/2,H/2),(H-2*t,W-2*t,b),'length',
        'Back fastening/grooves not designed.',role='back',length_axis=(0,0,1),thickness_axis=(0,1,0))

    if spec.joint_type == 'dowel_demo':
        # Illustrates side-to-bottom dowel-aligned blind holes only.
        # Not a verified joinery standard. Both sides of each interface receive holes.
        radius=3.0; side_depth=8.0; bottom_depth=12.0
        if side_depth >= t or radius >= t/2:
            raise ValueError('Unsafe demonstration joint dimensions')
        for y in (0.2*D,0.5*D,0.8*D):
            z=t/2
            for side, side_x, side_direction, bottom_x, bottom_direction in [
                ('P001',t,(-1,0,0),t,(1,0,0)),
                ('P002',W-t,(1,0,0),W-t,(-1,0,0))
            ]:
                for pid,x,axis,depth in ((side,side_x,side_direction,side_depth),
                                        ('P004',bottom_x,bottom_direction,bottom_depth)):
                    cyl=cq.Solid.makeCylinder(radius,depth,cq.Vector(x,y,z),cq.Vector(*axis))
                    shapes[pid]=shapes[pid].cut(cyl)
                    operations.append(ManufacturingOperation(
                        part_id=pid,operation='blind_dowel_hole',
                        axis={(-1,0,0):'-X',(1,0,0):'+X'}[axis],
                        origin_xyz_mm=(x,y,z),diameter_mm=2*radius,depth_mm=depth))

    if spec.include_glazed_door:
        gap=spec.door_perimeter_gap_mm
        fw=spec.door_frame_width_mm
        ft=spec.door_frame_depth_mm
        dw=W-2*t-2*gap
        dh=H-2*t-2*gap
        x0=t+gap
        z0=t+gap
        y=-ft/2-(spec.door_standoff_mm if spec.door_standoff_mm is not None else 1)
        fm=Material(spec.door_frame_wood)
        put('D001','Left door stile',fm,'frame',(fw,ft,dh),(x0+fw/2,y,z0+dh/2),(dh,fw,ft),'length',role='door_frame',length_axis=(0,0,1),thickness_axis=(0,1,0))
        put('D002','Right door stile',fm,'frame',(fw,ft,dh),(x0+dw-fw/2,y,z0+dh/2),(dh,fw,ft),'length',role='door_frame',length_axis=(0,0,1),thickness_axis=(0,1,0))
        put('D003','Top door rail',fm,'frame',(dw-2*fw,ft,fw),(x0+dw/2,y,z0+dh-fw/2),(dw-2*fw,fw,ft),'length',role='door_frame',thickness_axis=(0,-1,0))
        put('D004','Bottom door rail',fm,'frame',(dw-2*fw,ft,fw),(x0+dw/2,y,z0+fw/2),(dw-2*fw,fw,ft),'length',role='door_frame',thickness_axis=(0,-1,0))
        # Visual-only pane; must not be released without verified glazing retention.
        clearance=spec.glass_edge_clearance_mm if spec.glass_edge_clearance_mm is not None else 1.0
        put('G001','Conceptual glass insert',Material.glass,'glass',
            (dw-2*fw-2*clearance,spec.glass_thickness_mm,dh-2*fw-2*clearance),
            (x0+dw/2,y,z0+dh/2),
            (dh-2*fw-2*clearance,dw-2*fw-2*clearance,spec.glass_thickness_mm),
            'none','Visual only: retention, glass safety, edges, and fittings unverified.',
            role='glass',length_axis=(0,0,1),thickness_axis=(0,1,0))

    from .joinery_engine import apply_joints
    joint_issues = apply_joints(spec.joints, registry, shapes, operations)

    assembly=cq.Assembly(name='Cabinet_Concept_Not_For_Production')
    for p in parts:
        rgb=cq.Color(0.68,0.46,0.27) if p.material==Material.solid_wood else (
            cq.Color(0.72,0.61,0.42) if p.material==Material.plywood else (
                cq.Color(0.68,0.66,0.62) if p.material==Material.mdf else cq.Color(0.66,0.84,0.91,0.45)))
        assembly.add(shapes[p.part_id],name=p.part_id,color=rgb)

    warnings=['Shop drawings, fittings, tolerances and assembly strength still need engineering review.',
              'Outputs are review drafts, NOT approved machine instructions.']
    if spec.include_glazed_door:
        warnings.append('Glazed door has no verified hinge, retention groove, seals, or supported glazing system.')
    if spec.joint_type == 'dowel_demo':
        warnings.append('Dowel holes are illustrative only; verify all locations, sizes and connections.')
    if not spec.measured_dimensions_confirmed:
        warnings.append('Actual furniture dimensions have not been confirmed from physical measurements.')
    # Local machining solids are derived from the final assembly solids, after cuts.
    for p in registry:
        plane = cq.Plane(origin=p.center_xyz_mm, xDir=p.length_axis, normal=p.thickness_axis)
        local_shapes[p.part_id] = cq.Workplane('XY').newObject([plane.toLocalCoords(shapes[p.part_id].val())])
    return BuildResult(assembly,parts,shapes,operations,warnings,registry,local_shapes,joint_issues)


def rectangular_component(part: Part) -> cq.Workplane:
    """Part-local X=length, Y=width, Z=thickness; right-handed assembly transform."""
    plane = cq.Plane(origin=part.center_xyz_mm, xDir=part.length_axis, normal=part.thickness_axis)
    return cq.Workplane(plane).box(part.length_mm, part.width_mm, part.thickness_mm)


# Explicit template allowlist; no generated code or arbitrary class loading.
FURNITURE_TEMPLATES = {'cabinet': create_cabinet}
from .furniture_cad import create_furniture
FURNITURE_TEMPLATES.update({family:create_furniture for family in
    ('chair','stool','bench','table','storage','bed','sofa','screen','rack')})
from .photo_cad import create_photo_furniture
FURNITURE_TEMPLATES['photo']=create_photo_furniture


def export_cad(build: BuildResult, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    build.assembly.export(str(output/'cabinet.step'))
    if build.mesh_tolerance_mm is not None:
        from .review_mesh import export_glb
        export_glb(build,output/'cabinet.glb',build.mesh_tolerance_mm)
    # glTF coordinates are metres. CadQuery 2.6's exporter does not convert
    # our millimetre-sized solids, so give it a separate scaled assembly.
    else:
        preview = cq.Assembly(name='Cabinet_REVIEW_units_metres')
        for part in build.registry:
            preview.add(build.shapes[part.part_id].val().scale(0.001),name=part.part_id,
                        color=build.assembly.objects[part.part_id].color)
        preview.export(str(output/'cabinet.glb'),tolerance=1e-5,angularTolerance=0.1)
    # STL carries no assembly hierarchy. Union touching solids so coincident
    # component interfaces do not create nonmanifold merged mesh edges.
    solids = [s.val() for s in build.shapes.values()]
    stl_mode='BOOLEAN_UNION'
    try:
        envelope = solids[0].fuse(*solids[1:]).clean() if len(solids)>1 else solids[0]
        if not envelope.Solids() or not envelope.isValid(): raise ValueError('Union did not produce valid solids')
        cq.exporters.export(envelope, str(output/'cabinet.stl'), tolerance=0.1, angularTolerance=0.1)
        if not (output/'cabinet.stl').exists() or (output/'cabinet.stl').stat().st_size<=84:
            raise ValueError('The CAD kernel did not write an STL mesh')
    except Exception:
        if build.mesh_tolerance_mm is None: raise
        from .review_mesh import export_component_stl
        export_component_stl(build,output/'cabinet.stl')
        stl_mode='COMPONENT_SHELLS'
        build.warnings.append('STL contains component shells because the CAD kernel could not export the solid union. Use STEP for the authoritative component geometry; a watertight printable union is not verified.')
    if build.mesh_tolerance_mm is not None:
        import json
        from .review_mesh import remove_degenerate_stl_facets
        removed=remove_degenerate_stl_facets(output/'cabinet.stl')
        if removed: build.warnings.append(f'STL export omitted {removed} zero-area facet(s) after float32 conversion; STEP geometry is unchanged.')
        (output/'mesh_export_review.json').write_text(json.dumps(dict(stl_mode=stl_mode,
            stl_units='mm',source='cabinet.step',zero_area_facets_omitted=removed,
            printable_union_verified=False,approved_for_manufacturing=False),indent=2),encoding='utf-8')
