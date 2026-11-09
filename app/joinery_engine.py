"""Explicit, evidence-referenced paired bore geometry. Not a structural calculation."""
import math
import cadquery as cq
from .models import JointSpec, ManufacturingOperation
from .geometry import exact_bounds


def apply_joints(joints: list[JointSpec], registry, shapes, operations):
    issues = []
    for joint in joints:
        def block(code, message):
            issues.append(dict(code=code, severity='BLOCKER', category='joinery',
                               message=message, part_ids=list(joint.part_ids)))
        profile = joint.profile
        if not profile.approved or not profile.evidence_reference:
            block('JOINT_APPROVAL_REQUIRED', f'{joint.joint_id}: factory-approved profile and evidence required')
            continue
        if profile.joint_type in ('dado','rabbet','mortise_tenon') and profile.cross_size_mm is not None:
            try:
                candidates,pending=prismatic_joint(joint,registry,shapes)
                for pid,result in candidates:
                    shapes[pid]=result
                    update_joint_envelope(registry[pid],result.val())
                operations.extend(pending)
            except ValueError as exc: block('JOINT_ALIGNMENT_OR_BREAKTHROUGH',f'{joint.joint_id}: {exc}')
            continue
        mechanical=profile.joint_type=='mechanical' and profile.mechanical_type=='confirmat' and profile.pilot_diameter_mm is not None
        if profile.joint_type != 'dowel' and not mechanical:
            block('JOINT_GEOMETRY_NOT_IMPLEMENTED', f'{profile.joint_type}: matching geometry is not implemented')
            continue
        if (profile.diameter_mm is None or profile.depths_mm is None or
                profile.clearance_mm is None or profile.tolerance_mm is None or
                any(d <= 0 for d in profile.depths_mm)):
            block('JOINT_PARAMETERS_REQUIRED', f'{joint.joint_id}: explicit diameter, depths, clearance and tolerance required')
            continue
        if joint.part_ids[0] == joint.part_ids[1] or any(p not in shapes for p in joint.part_ids):
            block('JOINT_PART_REFERENCE_INVALID', joint.joint_id)
            continue
        if any(registry[p].material not in profile.compatible_materials for p in joint.part_ids):
            block('JOINT_MATERIAL_INCOMPATIBLE', joint.joint_id)
            continue
        direction = joint.direction_xyz
        # This implementation intentionally supports only normal, cardinal bores.
        if sum(v != 0 for v in direction) != 1 or not math.isclose(sum(v*v for v in direction), 1, abs_tol=1e-9):
            block('JOINT_AXIS_UNSUPPORTED', joint.joint_id)
            continue
        axis = next(i for i, v in enumerate(direction) if v)
        candidates = []
        pending_operations = []
        for i, pid in enumerate(joint.part_ids):
            radius = ((profile.pilot_diameter_mm if mechanical and i==1 else profile.diameter_mm) + profile.clearance_mm) / 2
            inward = tuple(v * (1 if i == 0 else -1) for v in direction)
            bbox = exact_bounds(shapes[pid].val())
            bounds = ((bbox.xmin, bbox.xmax), (bbox.ymin, bbox.ymax), (bbox.zmin, bbox.zmax))
            interface = bounds[axis][0 if inward[axis] > 0 else 1]
            if not math.isclose(joint.origin_xyz_mm[axis], interface, abs_tol=1e-6):
                break
            depth = profile.depths_mm[i]
            # Blind holes must leave material at their far end. The approved design
            # must specify the actual residual wall; this only detects breakthrough.
            if depth >= bounds[axis][1] - bounds[axis][0]:
                break
            cutter = cq.Solid.makeCylinder(radius, depth, cq.Vector(*joint.origin_xyz_mm), cq.Vector(*inward))
            removed = shapes[pid].val().intersect(cutter).Volume()
            expected = math.pi * radius**2 * depth
            if not math.isclose(removed, expected, rel_tol=1e-6, abs_tol=1e-6):
                break
            result = shapes[pid].cut(cutter)
            if not result.val().isValid() or result.val().Volume() <= 0:
                break
            candidates.append((pid, result))
            pending_operations.append(ManufacturingOperation(
                        part_id=pid, operation='confirmat_clearance_hole' if mechanical and i==0 else 'confirmat_pilot_hole' if mechanical else 'blind_dowel_hole', axis=str(inward),
                origin_xyz_mm=joint.origin_xyz_mm, diameter_mm=2*radius, depth_mm=depth,
                status='PROFILE_APPROVED_GEOMETRY_ONLY', joint_id=joint.joint_id, profile_id=profile.profile_id))
        if len(candidates) != 2:
            block('JOINT_ALIGNMENT_OR_BREAKTHROUGH', f'{joint.joint_id}: mating interfaces or full blind bores do not fit')
            continue
        for pid, result in candidates:
            shapes[pid] = result
        operations.extend(pending_operations)
    return issues


def update_joint_envelope(part,solid):
    """Tenon stock comes from the modified solid, preserving explicit machining allowances."""
    allowance=[getattr(part,'blank_'+name+'_mm')-getattr(part,name+'_mm') for name in ('length','width','thickness')]
    bb=exact_bounds(solid)
    center=tuple((getattr(bb,axis+'min')+getattr(bb,axis+'max'))/2 for axis in 'xyz')
    plane=cq.Plane(origin=center,xDir=part.length_axis,normal=part.thickness_axis)
    local=exact_bounds(plane.toLocalCoords(solid))
    part.size_xyz_mm=(bb.xlen,bb.ylen,bb.zlen);part.center_xyz_mm=center
    for name,dimension,extra in zip(('length','width','thickness'),(local.xlen,local.ylen,local.zlen),allowance):
        setattr(part,name+'_mm',dimension);setattr(part,'blank_'+name+'_mm',dimension+extra)


def prismatic_joint(joint,registry,shapes):
    """Approved rectangular receiving pocket and matching integral tongue/tenon."""
    profile=joint.profile
    if joint.part_ids[0]==joint.part_ids[1] or any(pid not in shapes for pid in joint.part_ids):
        raise ValueError('Distinct mating components are required')
    if any(registry[pid].material not in profile.compatible_materials for pid in joint.part_ids):
        raise ValueError('Material is incompatible with the joint profile')
    direction=joint.direction_xyz
    if sum(v!=0 for v in direction)!=1 or not math.isclose(sum(v*v for v in direction),1,abs_tol=1e-9):
        raise ValueError('A cardinal inward axis is required')
    if profile.depths_mm is None or profile.clearance_mm is None or profile.tolerance_mm is None:
        raise ValueError('Explicit depths, clearance and tolerance are required')
    width,height=profile.cross_size_mm;depth,tenon_depth=profile.depths_mm;gap=profile.clearance_mm
    if not (0<width<=5000 and 0<height<=3000 and depth>0 and 0<tenon_depth<=depth-gap and min(width,height)>2*gap):
        raise ValueError('Pocket and mating tenon dimensions do not fit')
    axis=next(i for i,v in enumerate(direction) if v)
    first,second=[shapes[pid].val() for pid in joint.part_ids]
    first_bb,second_bb=exact_bounds(first),exact_bounds(second)
    bounds=lambda bb:[(getattr(bb,k+'min'),getattr(bb,k+'max')) for k in 'xyz']
    a,b=bounds(first_bb),bounds(second_bb)
    interface=a[axis][0 if direction[axis]>0 else 1]
    mating=b[axis][1 if direction[axis]>0 else 0]
    if not (math.isclose(joint.origin_xyz_mm[axis],interface,abs_tol=1e-6) and math.isclose(interface,mating,abs_tol=1e-6)):
        raise ValueError('Components must meet at the supplied interface')
    if depth>=a[axis][1]-a[axis][0]: raise ValueError('Receiving pocket would break through the component')
    plane=cq.Plane(origin=joint.origin_xyz_mm,xDir=(0,1,0) if axis==0 else (1,0,0),normal=direction)
    pocket=cq.Workplane(plane).rect(width,height).extrude(depth).val()
    tongue=cq.Workplane(plane).rect(width-2*gap,height-2*gap).extrude(tenon_depth).val()
    if not math.isclose(first.intersect(pocket).Volume(),pocket.Volume(),rel_tol=1e-6,abs_tol=1e-5):
        raise ValueError('Receiving pocket is not fully supported by the component')
    # A shallow root probe proves the tongue meets the second component across its full section.
    probe=cq.Workplane(plane).rect(width-2*gap,height-2*gap).extrude(-.01).val()
    if not math.isclose(second.intersect(probe).Volume(),probe.Volume(),rel_tol=1e-5,abs_tol=1e-5):
        raise ValueError('The tenon root is not fully supported by its component')
    results=[first.cut(pocket).clean(),second.fuse(tongue).clean()]
    if any(not solid.isValid() or len(solid.Solids())!=1 or solid.Volume()<=0 for solid in results):
        raise ValueError('Joint produced invalid geometry')
    if results[0].intersect(results[1]).Volume()>1e-5: raise ValueError('Joint leaves intersecting solids')
    pending=[ManufacturingOperation(part_id=pid,operation=profile.joint_type+'_pocket' if i==0 else 'integral_tenon',
        axis=str(direction),origin_xyz_mm=joint.origin_xyz_mm,diameter_mm=None,depth_mm=depth if i==0 else tenon_depth,
        feature_dimensions_mm=(width,height,depth) if i==0 else (width-2*gap,height-2*gap,tenon_depth),
        coordinate_system='ASSEMBLY_MM; section XY in plane normal to supplied axis',
        status='PROFILE_APPROVED_GEOMETRY_ONLY',joint_id=joint.joint_id,profile_id=profile.profile_id) for i,pid in enumerate(joint.part_ids)]
    return list(zip(joint.part_ids,[cq.Workplane('XY').newObject([solid]) for solid in results])),pending
