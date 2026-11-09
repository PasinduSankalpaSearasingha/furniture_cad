"""Build photographed components from a bounded declarative shape plan."""
import math
import cadquery as cq
from .photo_schema import number
from .models import Part, Material, ManufacturingOperation
from .geometry import exact_bounds
from .registry import PartsRegistry
from .geometry_repairs import EdgeRoundingError
from .furniture_items import item_contracts


def curve(points,mode,closed=False):
    vectors=[cq.Vector(*point) for point in points]
    if mode=='bezier':
        edges=[cq.Edge.makeBezier(vectors[i:i+4]) for i in range(0,len(vectors)-1,3)]
    else: edges=[cq.Edge.makeLine(a,b) for a,b in zip(vectors,vectors[1:])]
    if closed and (vectors[-1]-vectors[0]).Length>1e-6: edges.append(cq.Edge.makeLine(vectors[-1],vectors[0]))
    return cq.Wire.assembleEdges(edges)


def world_up_sweep(section,path):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.gp import gp_Dir
    builder=BRepOffsetAPI_MakePipeShell(path.wrapped)
    builder.SetMode(gp_Dir(0,0,1))
    builder.SetTolerance(1e-6,1e-6,1e-6)
    builder.Add(section.val().wrapped,False,False)
    builder.Build()
    if not builder.IsDone() or not builder.MakeSolid():raise ValueError('World-up sweep could not form a closed solid; revise its path or section')
    return cq.Workplane('XY').newObject([cq.Shape.cast(builder.Shape())])


def bound_loft(shape,wires,part_id):
    bounds=[exact_bounds(wire) for wire in wires]
    spans=[(min(getattr(b,a+'min') for b in bounds),max(getattr(b,a+'max') for b in bounds)) for a in 'xyz']
    before=shape.val();actual=exact_bounds(before)
    overshoot=max(max(lo-getattr(actual,a+'min'),getattr(actual,a+'max')-hi,0) for a,(lo,hi) in zip('xyz',spans))
    if overshoot<1e-5:return shape,None
    if any(max(lo-getattr(actual,a+'min'),getattr(actual,a+'max')-hi,0)>max(.01,(hi-lo)*.02) for a,(lo,hi) in zip('xyz',spans)):
        raise ValueError('Loft interpolation extends too far outside its section envelope; revise its sections')
    box=cq.Workplane('XY').box(*(hi-lo for lo,hi in spans)).translate(tuple((hi+lo)/2 for lo,hi in spans))
    bounded=shape.intersect(box)
    result=bounded.val();original=before.Volume(1e-10);removed=original-result.Volume(1e-10)
    integration_tolerance=max(1e-3,original*1e-10)
    if not result.isValid() or len(result.Solids())!=1 or removed < -integration_tolerance or removed>original*.02:
        raise ValueError('Loft interpolation exceeds its section envelope; revise sections instead of relying on overshoot')
    return bounded,dict(kind='BOUNDED_LOFT_ENVELOPE',part_id=part_id,maximum_interpolation_overshoot_mm=overshoot,
        removed_volume_mm3=max(0,removed),volume_integration_tolerance_mm3=integration_tolerance,
        section_dimensions_changed=False,requires_visual_review=True,
        description=f'{part_id}: small loft interpolation overshoot was trimmed to its declared section envelope; supplied section dimensions and placement were retained.')


def create_photo_furniture(spec, *, geometry_cache=None):
    from .cad import BuildResult
    design=spec.photo_design
    values={v.key:v.value for v in design.values}
    n=lambda value:number(value,values)
    point=lambda values:tuple(n(v) for v in values)
    registry,shapes,local=PartsRegistry(),{},{}
    assembly=cq.Assembly(name='Photo_derived_furniture_REVIEW')
    trims=[];operations=[];adjustments=[]
    items=item_contracts(design)
    item_for={pid:item['item_id'] for item in items for pid in item['part_ids']}
    prefix=[]
    for item in design.parts:
        cache_key=None
        if geometry_cache is not None:
            from .cad_parameters import references
            from .pipeline import canonical_hash
            data=item.model_dump(mode='json')
            cache_key=canonical_hash(dict(part=data,values={key:values[key] for key in references(data) if key in values},
                preceding=prefix,items=item_for,revision=spec.revision))
            prefix.append(cache_key)
            cached=geometry_cache.get(cache_key)
            if cached is not None:
                part,placed,local_solid,part_operations,part_adjustments,part_trims=cached
                registry.add(part.model_copy(deep=True));shapes[item.part_id]=placed;local[item.part_id]=local_solid
                operations.extend(part_operations);adjustments.extend(part_adjustments);trims.extend(part_trims)
                assembly.add(placed,name=item.part_id,color=cq.Color(*item.color_rgb,.35 if item.material=='glass' else 1))
                continue
        operation_start,adjustment_start,trim_start=len(operations),len(adjustments),len(trims)
        try:
            sx,sy,sz=point(item.size)
            if item.geometry=='box':
                shape=cq.Workplane('XY').box(sx,sy,sz)
                radius=n(item.corner_radius)
                if radius:
                    if radius>=min(sx,sy)/2: raise ValueError('Corner radius must fit inside the photographed part')
                    shape=shape.edges('|Z').fillet(radius)
            elif item.geometry=='cylinder':
                shape=cq.Workplane('XY').ellipse(sx/2,sy/2).extrude(sz).translate((0,0,-sz/2))
            elif item.geometry=='ellipsoid':
                shape=cq.Workplane('XY').sphere(1)
                matrix=cq.Matrix([[sx/2,0,0,0],[0,sy/2,0,0],[0,0,sz/2,0],[0,0,0,1]])
                shape=cq.Workplane('XY').newObject([shape.val().transformGeometry(matrix)])
            elif item.geometry=='outline_panel':
                wire=curve([(n(x),n(y),0) for x,y in item.outline],item.curve_mode,closed=True)
                solid=cq.Solid.extrudeLinear(wire,[],cq.Vector(0,0,sz)).translate((0,0,-sz/2))
                shape=cq.Workplane('XY').newObject([solid])
            elif item.geometry=='nurbs_panel':
                from .mesh_to_cad import nurbs_patch
                controls=[[point(p) for p in row] for row in item.surface_nurbs.control_points]
                weights=([[n(w) for w in row] for row in item.surface_nurbs.weights]
                         if item.surface_nurbs.weights is not None else None)
                face=nurbs_patch(controls,weights)
                shape=cq.Workplane('XY').newObject([face.thicken(-sz)])
            elif item.geometry=='loft':
                wires=[];section_data=[]
                for section in item.sections:
                    x,y,z=point(section.center)
                    work=cq.Workplane('XY',origin=(x,y,z))
                    work=work.ellipse(n(section.width)/2,n(section.depth)/2) if section.profile=='ellipse' else work.rect(n(section.width),n(section.depth))
                    wire=work.val()
                    if n(section.corner_radius): wire=wire.fillet2D(n(section.corner_radius),wire.Vertices())
                    wires.append(wire)
                    section_data.append(dict(center=(x,y,z),width=n(section.width),depth=n(section.depth),
                        profile=section.profile,corner_radius=n(section.corner_radius)))
                if item.loft_mode=='BOUNDED':
                    from .loft_repair import build_bounded_loft
                    shape,reports=build_bounded_loft(wires,section_data,item.part_id,bound_loft)
                    adjustments.extend(reports)
                else:shape=cq.Workplane('XY').newObject([cq.Solid.makeLoft(wires,ruled=False)])
            elif item.geometry=='profile_sweep':
                path=curve([point(v) for v in item.path],item.curve_mode)
                profiles=[]
                for section in item.sweep_sections:
                    tangent=path.tangentAt(section.fraction)
                    horizontal=cq.Vector(0,0,1).cross(tangent)
                    if horizontal.Length<1e-6: horizontal=cq.Vector(1,0,0).cross(tangent)
                    plane=cq.Plane(origin=path.positionAt(section.fraction),xDir=horizontal.normalized(),normal=tangent)
                    profiles.append(cq.Workplane(plane).ellipse(n(section.width)/2,n(section.depth)/2).val())
                shape=cq.Workplane('XY').newObject([cq.Solid.sweep_multi(profiles,path,True,True)])
            else:
                points=[point(v) for v in item.path]
                path=curve(points,item.curve_mode)
                tangent=path.tangentAt(0)
                if item.sweep_frame=='WORLD_UP':
                    horizontal=cq.Vector(0,0,1).cross(tangent)
                    if horizontal.Length<1e-6:horizontal=cq.Vector(1,0,0).cross(tangent)
                    plane=cq.Plane(origin=points[0],xDir=horizontal.normalized(),normal=tangent)
                else:plane=cq.Plane(origin=points[0],normal=tangent)
                section=cq.Workplane(plane)
                section=section.circle(sx/2) if item.geometry=='round_sweep' else (
                    section.ellipse(sx/2,sy/2) if item.geometry=='ellipse_sweep' else section.rect(sx,sy))
                planar=max(p[2] for p in points)-min(p[2] for p in points)<1e-6
                shape=(world_up_sweep(section,path) if item.sweep_frame=='WORLD_UP' and planar else
                    section.sweep(cq.Workplane('XY').newObject([path]),isFrenet=item.sweep_frame=='LEGACY'))
                if item.rounded_ends:
                    # Round the actual terminal rims; preserve the swept section's
                    # transported orientation and avoid intersecting auxiliary caps.
                    terminals=[face for face in shape.val().Faces() if face.geomType()=='PLANE']
                    edges=[edge for face in terminals for edge in face.Edges()]
                    if len(terminals)!=2: raise ValueError('Rounded sweep ends require two valid terminal faces')
                    minor=sx if item.geometry=='round_sweep' else sy
                    radius=min(sx,minor)*.16
                    shape=shape.newObject(edges).fillet(radius)
            if item.surface_grid:
                rows=item.surface_grid.depths;ny,nx=len(rows),len(rows[0])
                panel_bounds=exact_bounds(shape.val()) if item.panel_frame=='BOUNDS' else None
                x0,y0=(panel_bounds.xmin,panel_bounds.ymin) if panel_bounds else (-sx/2,-sy/2)
                grid_width,grid_depth=(panel_bounds.xlen,panel_bounds.ylen) if panel_bounds else (sx,sy)
                points=[[cq.Vector(x0+grid_width*x/(nx-1),y0+grid_depth*y/(ny-1),sz/2-n(rows[y][x]))
                         for y in range(ny)] for x in range(nx)]
                top=cq.Face.makeSplineApprox(points,tol=.005,minDeg=2,maxDeg=3)
                # Offset a continuous top downward, then clip to the original panel:
                # the bottom stays flat and the original plan contour is retained.
                direction=-1 if top.normalAt().z>=0 else 1
                body=(cq.Solid.extrudeLinear(top,cq.Vector(0,0,-sz*2)) if item.panel_frame=='BOUNDS' else
                    top.thicken(direction*sz*2))
                shape=shape.intersect(body).clean()
                if not shape.val().Solids(): raise ValueError(f'{item.part_id}: sculpted surface does not form a panel')
            depth=n(item.dish_depth)
            if depth:
                if item.geometry not in ('box','outline_panel','cylinder') or depth>=sz*.7:
                    raise ValueError('Seat dish depth must fit inside a panel')
                radius=max(sx,sy)*1.8
                panel_bounds=exact_bounds(shape.val()) if item.panel_frame=='BOUNDS' else None
                center=((panel_bounds.xmin+panel_bounds.xmax)/2,(panel_bounds.ymin+panel_bounds.ymax)/2) if panel_bounds else (0,0)
                cutter=cq.Workplane('XY').sphere(radius).translate((*center,sz/2+radius-depth))
                shape=shape.cut(cutter)
            for dish in item.surface_dishes:
                rx,ry,rz=(n(dish.radius_x),n(dish.radius_y),n(dish.radius_z))
                x,y=(n(v) for v in dish.center)
                cutter=cq.Workplane('XY').sphere(1).val().transformGeometry(cq.Matrix([
                    [rx,0,0,0],[0,ry,0,0],[0,0,rz,0],[0,0,0,1]])).translate((x,y,sz/2+rz-n(dish.depth)))
                previous_volume=shape.val().Volume()
                shape=shape.cut(cutter).clean()
                if previous_volume-shape.val().Volume()<1e-5: raise ValueError(f'{item.part_id}: surface dish does not intersect the part')
            if n(item.edge_radius):
                radius=n(item.edge_radius)
                try:shape=shape.edges().fillet(radius)
                except Exception as exc:
                    raise EdgeRoundingError(item.part_id,f'{item.part_id} {item.label}: edge_radius={radius:g} mm failed on {item.geometry}. '
                        'Revise this edge radius or use 0. For smooth lofts, use section corner_radius or elliptical sections '
                        'to represent rounding; avoid all-edge fillets on every loft component. No dimensions were changed.') from exc
            if item.features:
                from .cad_operations import apply_features
                try:shape=apply_features(shape,item.features,values)
                except Exception as exc:
                    raise ValueError(f'{item.part_id} {item.label}: explicit CAD feature failed: {exc}') from exc
                for feature in item.features:
                    operations.append(ManufacturingOperation(part_id=item.part_id,operation=feature.operation,
                        axis=feature.axis,origin_xyz_mm=point(feature.position),
                        diameter_mm=2*n(feature.radius) if feature.operation in ('hole','dowel','confirmat') else None,
                        depth_mm=n(feature.depth),status='UNVERIFIED_FEATURE_GEOMETRY_ONLY',
                        coordinate_system='COMPONENT_LOCAL_MM_BEFORE_ASSEMBLY_TRANSFORM',
                        feature_dimensions_mm=point(feature.size),evidence=feature.evidence))
            raw=shape.val()
            # Record the part frame before its placement, including arbitrary photo-derived rotation.
            rotation=point(item.rotation_deg)
            origin=point(item.position)
            for direction,angle in zip(((1,0,0),(0,1,0),(0,0,1)),rotation):
                if angle: raw=raw.rotate((0,0,0),direction,angle)
            raw=raw.translate(origin)
            contact_parts=[]
            for previous_id,previous in shapes.items():
                if item_for[item.part_id]!=item_for[previous_id]:continue
                a,b=exact_bounds(raw),exact_bounds(previous.val())
                if all(min(getattr(a,k+'max'),getattr(b,k+'max'))-max(getattr(a,k+'min'),getattr(b,k+'min'))>1e-7 for k in ('x','y','z')):
                    if raw.intersect(previous.val()).Volume()>1e-5:
                        raw=raw.cut(previous.val()).clean();trims.append(item.part_id);contact_parts.append(previous_id)
            if not raw.isValid() or raw.Volume()<=0 or len(raw.Solids())!=1:
                detail=(f' After trimming contacts with {", ".join(contact_parts)}, {len(raw.Solids())} solid pieces remain. '
                    'Make the member terminate at the contact surface; avoid extending an upright through a rail or a leg through a seat.'
                    if contact_parts else ' Its original loft/sweep is invalid; revise section positions or the path to avoid self-intersection.')
                raise ValueError(f'{item.part_id} {item.label}: component is disconnected, self-intersecting or consumed by another part.'+detail)
            # Subsequent contact cuts can make AddOptimal include portions of
            # an already clipped loft's underlying spline surface again.
            from .geometry import trimmed_bounds
            measure=(trimmed_bounds if any(r.get('kind')=='BOUNDED_LOFT_ENVELOPE' and
                r.get('part_id')==item.part_id for r in adjustments) else exact_bounds)
            bounds=measure(raw)
            center=tuple((getattr(bounds,k+'min')+getattr(bounds,k+'max'))/2 for k in ('x','y','z'))
            # Inverse rotation in a stable local part frame, then choose its enclosing stock axes.
            axes=[]
            for unit in ((1,0,0),(0,1,0),(0,0,1)):
                vector=cq.Vector(*unit)
                # Apply only rotations to a vertex so OCCT supplies the same transform as the solid.
                vertex=cq.Vertex.makeVertex(*unit)
                for direction,angle in zip(((1,0,0),(0,1,0),(0,0,1)),rotation):
                    if angle: vertex=vertex.rotate((0,0,0),direction,angle)
                axes.append(vertex.Center().toTuple())
            plane=cq.Plane(origin=center,xDir=axes[0],normal=axes[2])
            local_solid=plane.toLocalCoords(raw)
            lb=measure(local_solid)
            blank=(lb.xlen,lb.ylen,lb.zlen)
            material=Material(item.material)
            part=Part(part_id=item.part_id,label=item.label,material=material,
                category='glass' if material==Material.glass else 'panel' if item.geometry in ('box','outline_panel','cylinder') else 'frame',
                blank_length_mm=blank[0],blank_width_mm=blank[1],blank_thickness_mm=blank[2],
                length_mm=blank[0],width_mm=blank[1],thickness_mm=blank[2],size_xyz_mm=(bounds.xlen,bounds.ylen,bounds.zlen),
                center_xyz_mm=center,revision=spec.revision,length_axis=axes[0],thickness_axis=axes[2],
                manufacturing_status='PHOTO_ESTIMATED_REVIEW',blank_verified=False,color_rgb=item.color_rgb,
                note=item.evidence+'; photo-derived estimate; blank is an enclosing envelope; connections unengineered.')
            registry.add(part)
            shapes[item.part_id]=cq.Workplane('XY').newObject([raw])
            local[item.part_id]=cq.Workplane('XY').newObject([local_solid])
            assembly.add(shapes[item.part_id],name=item.part_id,color=cq.Color(*item.color_rgb,.35 if material==Material.glass else 1))
            if cache_key is not None:
                if len(geometry_cache)>=128:geometry_cache.pop(next(iter(geometry_cache)))
                geometry_cache[cache_key]=(part.model_copy(deep=True),shapes[item.part_id],local[item.part_id],
                    operations[operation_start:],adjustments[adjustment_start:],trims[trim_start:])
        except EdgeRoundingError:
            raise
        except ValueError as exc:
            if str(exc).startswith(item.part_id+' '):raise
            raise ValueError(f'{item.part_id} {item.label}: {exc}') from exc
        except Exception as exc:
            if type(exc).__module__.startswith('OCP'):
                raise ValueError(f'{item.part_id} {item.label}: CAD kernel could not build {item.geometry}: {exc}. Revise the profile, section orientation or end rounding; dimensions were retained.') from exc
            raise
    warnings=['Photo-derived approximate geometry; an unscaled single photograph cannot establish exact dimensions or hidden surfaces.',
        'Supplied parameters are retained; inferred profiles, construction, materials and all blank envelopes require review.',
        *design.assumptions,*design.unrepresented_features]
    if trims: warnings.append('Visual mating contacts trimmed, without engineering joinery: '+', '.join(dict.fromkeys(trims)))
    warnings.extend(change['description'] for change in adjustments)
    return BuildResult(assembly,list(registry),shapes,operations=operations,warnings=warnings,registry=registry,local_shapes=local,mesh_tolerance_mm=.25,
        furniture_items=items,construction_adjustments=adjustments)
