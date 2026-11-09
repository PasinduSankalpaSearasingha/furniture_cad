"""Stable CAD identity, native frames, mesh evidence and reviewable boundaries."""
import numpy as np
from .geometry_acceleration import SurfaceIndex


def part_registry(build,plan,reference,native,*,recognition=None,tolerance_mm=2):
    nearest=SurfaceIndex(native).closest(reference.mesh.triangles_center)
    labels=native.face_components[nearest['face_ids']]
    partids=list(build.shapes)
    hypotheses={h.part_id:h for h in recognition.hypotheses} if recognition else {}
    descriptions={p.part_id:p for p in plan.parts};parts=[]
    face_map=np.asarray(reference.mesh.metadata.get('_cad_source_face_ids',np.arange(len(reference.mesh.faces))))
    vertex_pairs=np.asarray(reference.mesh.metadata.get('_cad_source_vertex_pairs',np.column_stack((np.arange(len(reference.mesh.vertices)),np.arange(len(reference.mesh.vertices))))))
    for index,part in enumerate(build.parts):
        pid=part.part_id;definition=descriptions[pid]
        ids=np.flatnonzero((labels==partids.index(pid)) & (nearest['distances']<=tolerance_mm))
        vertex_ids=np.unique(reference.mesh.faces[ids])
        x=np.array(part.length_axis);z=np.array(part.thickness_axis);y=np.cross(z,x)
        matrix=np.eye(4);matrix[:3,:3]=np.column_stack((x,y,z));matrix[:3,3]=part.center_xyz_mm
        hypothesis=hypotheses.get(pid)
        parts.append(dict(part_id=pid,label=part.label,material=part.material.value,material_confirmed=False,
            category=hypothesis.category if hypothesis else 'unknown',geometry=definition.geometry,
            face_ids=face_map[ids].tolist(),working_face_ids=ids.tolist(),
            vertex_ids=np.unique(vertex_pairs[np.isin(vertex_pairs[:,0],vertex_ids),1]).tolist(),mesh_assignment_tolerance_mm=tolerance_mm,
            local_to_world=matrix.tolist(),world_to_local=np.linalg.inv(matrix).tolist(),
            local_envelope_mm=[part.length_mm,part.width_mm,part.thickness_mm],world_envelope_mm=list(part.size_xyz_mm),
            center_xyz_mm=list(part.center_xyz_mm),source_evidence=definition.evidence,
            confidence=None,confidence_status='uncalibrated',manufactured_part_verified=False,
            boundary_status='inferred_review_required',review_status='pending',
            uncertainties=hypothesis.uncertainties if hypothesis else ['Component identity and boundaries need designer review'],
            region_ids=hypothesis.region_ids if hypothesis else [],
            files=dict(step=f'cad/parts/{pid}/part.step',freecad=f'cad/parts/{pid}/part.FCStd',
                       preview=f'cad/parts/{pid}/part.glb',parameters=f'cad/parts/{pid}/parameters.json',
                       pdf=f'drawings/parts/{pid}/six_views.pdf')))
    unassigned=np.flatnonzero(nearest['distances']>tolerance_mm).tolist()
    return dict(version=1,parts=parts,unassigned_face_ids=face_map[unassigned].tolist(),unassigned_working_face_ids=unassigned,
                index_space='original scene mesh; see mesh_analysis.json scene_index_ranges and reference/mesh_index_map.npz',
                mesh_face_count=len(reference.mesh.faces),source_face_count=reference.mesh.metadata.get('_cad_source_face_count',len(reference.mesh.faces)),
                source_vertex_count=reference.mesh.metadata.get('_cad_source_vertex_count',len(reference.mesh.vertices)),
                mesh_vertex_count=len(reference.mesh.vertices),units='mm',status='draft',
                uncertainties=[*plan.assumptions,*plan.unrepresented_features],
                calibration='No calibrated semantic confidence scores are available',
                relationships=[dict(left=p.part_id,right=q.part_id,kind='contact_candidate',verified=False)
                    for i,p in enumerate(build.parts) for q in build.parts[i+1:]
                    if build.shapes[p.part_id].val().distance(build.shapes[q.part_id].val())<.1])
