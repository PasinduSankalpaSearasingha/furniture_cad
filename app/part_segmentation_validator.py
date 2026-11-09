"""Validate untrusted face proposals without treating regions as manufactured parts."""
import numpy as np
import trimesh
from .ai_part_detector import CATEGORIES


def validate_regions(mesh, proposals):
    regions = proposals.get('regions',[])
    if len(regions)>120:raise ValueError('Too many proposed mesh regions')
    used=set();result=[]
    for index, region in enumerate(regions):
        ids=region.get('face_ids',[])
        if not ids or any(type(i) is not int or not 0<=i<len(mesh.faces) for i in ids):
            raise ValueError('Region face IDs must address original mesh triangles')
        if len(set(ids))!=len(ids) or used.intersection(ids):raise ValueError('Proposed regions overlap')
        used.update(ids)
        ids=np.array(sorted(ids),dtype=int)
        vertices=np.unique(mesh.faces[ids])
        adjacent=mesh.face_adjacency[np.isin(mesh.face_adjacency,ids).all(axis=1)]
        islands=trimesh.graph.connected_components(adjacent,nodes=ids,min_len=1)
        if len(islands)!=1:raise ValueError('A manufactured-part region proposal must be connected')
        category=region.get('category','unknown')
        if category not in CATEGORIES:raise ValueError('Unknown furniture category')
        result.append(dict(id=index,face_ids=ids.tolist(),vertex_ids=vertices.tolist(),category=category,
            bounds_mm=mesh.vertices[vertices].min(0).tolist()+mesh.vertices[vertices].max(0).tolist(),
            evidence=str(region.get('evidence','Local segmentation proposal'))[:1000],confidence=None,
            confidence_status='uncalibrated',boundary_status='inferred_review_required',manufactured_part_verified=False))
    return dict(regions=result,unassigned_face_ids=sorted(set(range(len(mesh.faces)))-used),review_required=True)
