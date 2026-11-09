"""Bounded local loft retries that preserve every declared section.

Valid existing lofts keep their original construction. A failed bounded loft
can use a different native interpolation or shape-preserving intermediate
profiles. No AI, parameter edits, dimension scaling or faceted replacement is
involved. Every repaired solid is checked against the original section wires.
"""
import math
import time
import cadquery as cq

SECTION_TOLERANCE_MM=.005
MAX_LOCAL_ATTEMPTS=5
MAX_LOCAL_REPAIR_SECONDS=20


def profile_wire(section):
    x,y,z=section['center'];width,depth=section['width'],section['depth']
    work=cq.Workplane('XY',origin=(x,y,z))
    wire=(work.ellipse(width/2,depth/2) if section['profile']=='ellipse' else work.rect(width,depth)).val()
    radius=section['corner_radius']
    return wire.fillet2D(radius,wire.Vertices()) if radius else wire


def _check_solid(shape):
    solid=shape.val()
    if not solid.isValid() or len(solid.Solids())!=1 or solid.Volume(1e-10)<=1e-5:
        raise ValueError('Local loft construction did not form one valid positive solid')
    if any(not shell.Closed() for shell in solid.Shells()):raise ValueError('Local loft construction has an open shell')


def _native_loft(wires,degree,parameterization):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.Approx import Approx_Centripetal,Approx_ChordLength
    from OCP.GeomAbs import GeomAbs_C1
    builder=BRepOffsetAPI_ThruSections(True,False,1e-6)
    builder.SetMaxDegree(degree)
    builder.SetParType(Approx_Centripetal if parameterization=='centripetal' else Approx_ChordLength)
    builder.SetContinuity(GeomAbs_C1);builder.SetSmoothing(False)
    builder.SetMutableInput(False);builder.CheckCompatibility(True)
    for wire in wires:builder.AddWire(wire.wrapped)
    builder.Build()
    if not builder.IsDone():raise ValueError('The native loft interpolation did not complete')
    return cq.Workplane('XY').newObject([cq.Shape.cast(builder.Shape())])


def _intermediate_profiles(sections,subdivisions):
    from scipy.interpolate import PchipInterpolator
    import numpy as np
    if len({section['profile'] for section in sections})!=1:
        raise ValueError('Shape-preserving loft repair needs a consistent section profile type')
    if len({section['corner_radius']>0 for section in sections})!=1:
        raise ValueError('Shape-preserving loft repair needs consistent rounded-corner topology')
    zs=[section['center'][2] for section in sections]
    if any(b-a<=1e-6 for a,b in zip(zs,zs[1:])):
        raise ValueError('Loft sections must be ordered at strictly increasing local Z heights')
    rows=[]
    for section in sections:
        x,y,_=section['center'];w,d=section['width'],section['depth']
        rows.append([x-w/2,x+w/2,y-d/2,y+d/2,2*section['corner_radius']/min(w,d)])
    # Interpolate the four profile bounds, rather than centre/size separately,
    # to prevent ringing outside the declared profile envelope. Radius ratios
    # keep intermediate rounded corners valid without changing original radii.
    interpolation=PchipInterpolator(zs,np.asarray(rows),axis=0)
    result=[]
    for index,(z0,z1) in enumerate(zip(zs,zs[1:])):
        for step in range(subdivisions):
            if step==0:
                result.append(profile_wire(sections[index]));continue
            z=z0+(z1-z0)*step/subdivisions
            left,right,bottom,top,ratio=map(float,interpolation(z))
            width,depth=right-left,top-bottom
            if not all(math.isfinite(v) for v in (left,right,bottom,top,ratio)) or min(width,depth)<.2:
                raise ValueError('Intermediate loft profiles would be degenerate')
            result.append(profile_wire(dict(center=((left+right)/2,(bottom+top)/2,z),width=width,depth=depth,
                profile=sections[0]['profile'],corner_radius=ratio*min(width,depth)/2)))
    result.append(profile_wire(sections[-1]))
    return result


def _section_fidelity(shape,original_wires):
    """Bidirectional samples of actual plane cuts, including both end caps."""
    solid=shape.val().Solids()[0];shell=solid.outerShell();largest=0.0
    for target in original_wires:
        points,_=target.sample(24)
        for point in points:
            largest=max(largest,cq.Vertex.makeVertex(*point.toTuple()).distance(shell))
        height=target.Vertices()[0].Center().z
        section=cq.Workplane('XY').newObject([solid]).section(height).val()
        loops=section.Wires()
        if len(loops)!=1:raise ValueError('A repaired loft did not retain one closed profile at each declared section')
        actual=loops[0]
        for origin,destination in ((target,actual),(actual,target)):
            points,_=origin.sample(32)
            for point in points:
                largest=max(largest,cq.Vertex.makeVertex(*point.toTuple()).distance(destination))
        if not math.isfinite(largest) or largest>SECTION_TOLERANCE_MM:
            raise ValueError('A repaired loft changed a declared section profile')
    return largest


def build_bounded_loft(wires,sections,part_id,bounder):
    """Keep the successful original path; try at most five local alternatives."""
    try:
        shape=cq.Workplane('XY').newObject([cq.Solid.makeLoft(wires,ruled=False)])
        _check_solid(shape)
        bounded,report=bounder(shape,wires,part_id)
        _check_solid(bounded)
        return bounded,[report] if report else []
    except Exception as original_error:
        original_message=str(original_error)[:500]
    failures=[];started=time.monotonic()
    attempts=[('NATIVE_CENTRIPETAL_CUBIC',3,'centripetal',0),
        ('NATIVE_CENTRIPETAL_QUADRATIC',2,'centripetal',0),
        ('SHAPE_PRESERVING_CUBIC_4',3,'chord',4),
        ('SHAPE_PRESERVING_CUBIC_8',3,'chord',8),
        ('SHAPE_PRESERVING_QUADRATIC_8',2,'centripetal',8)]
    for attempt,(method,degree,parameterization,subdivisions) in enumerate(attempts[:MAX_LOCAL_ATTEMPTS],1):
        if time.monotonic()-started>=MAX_LOCAL_REPAIR_SECONDS:break
        try:
            current=(_intermediate_profiles(sections,subdivisions) if subdivisions else
                [profile_wire(section) for section in sections])
            shape=_native_loft(current,degree,parameterization);_check_solid(shape)
            # Always bound against the original declaration, never the added
            # intermediate wires. Existing 2% span/volume limits stay intact.
            shape,clipping=bounder(shape,wires,part_id);_check_solid(shape)
            gap=_section_fidelity(shape,wires)
            report=dict(kind='LOCAL_LOFT_REBUILD',part_id=part_id,method=method,local_attempts=attempt,
                original_failure=original_message,original_section_count=len(wires),constructed_section_count=len(current),
                maximum_sampled_section_gap_mm=gap,section_tolerance_mm=SECTION_TOLERANCE_MM,
                section_dimensions_changed=False,parameter_values_changed=False,original_corner_radii_retained=True,
                additional_ai_requests=0,requires_visual_review=True,
                description=f'{part_id}: rebuilt the failed smooth loft locally using {method.lower().replace("_"," ")}; declared sections, radii and parameter values were retained. Review the interpolated surface.')
            return shape,[report,*([clipping] if clipping else [])]
        except Exception as exc:
            failures.append(dict(method=method,message=str(exc)[:300]))
    detail=failures[-1]['message'] if failures else original_message
    raise ValueError(f'Local loft repair exhausted {len(failures)} checked constructions; {detail}. '
        'Revise the section layout or profile topology; no dimensions were changed. Original error: '+original_message)
