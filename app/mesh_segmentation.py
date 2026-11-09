"""Conservative geometric component hypotheses; connectivity is not joinery."""
from dataclasses import dataclass, field
import numpy as np
import trimesh
from scipy.spatial import QhullError


@dataclass
class ComponentRegion:
    component_id: int
    face_ids: np.ndarray
    label: str
    geometry: str
    center: np.ndarray
    size: np.ndarray
    rotation: np.ndarray
    confidence: float | None
    uncertainties: list = field(default_factory=list)


def segment_mesh(data, furniture_type='table', *, maximum=60,regions=None):
    mesh = data.mesh
    groups = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)
    # Preserve independent GLB nodes even if touching vertices were welded.
    if len(np.unique(data.face_components)) > 1:
        groups = [np.flatnonzero(data.face_components == label) for label in np.unique(data.face_components)]
    if regions is not None:groups=[np.array(r['face_ids'],dtype=int) for r in regions]
    components, unresolved = [], []
    total_height = np.ptp(mesh.bounds[:, 2])
    for ids in sorted(groups, key=lambda g: -mesh.area_faces[g].sum()):
        if len(components) >= maximum or len(ids) < 4:
            unresolved.extend(map(int, ids))
            continue
        region = mesh.submesh([ids], append=True, repair=False)
        try:
            box = region.bounding_box_oriented
            size = np.asarray(box.primitive.extents)
            rotation = np.asarray(box.primitive.transform)[:3, :3]
            center = np.asarray(box.primitive.transform)[:3, 3]
        except (ValueError,QhullError):
            # Open planar masks are evidence regions, never reliable solid boxes.
            points=region.vertices;_,_,axes=np.linalg.svd(points-points.mean(0),full_matrices=False)
            rotation=axes.T
            if np.linalg.det(rotation)<0:rotation[:,-1]*=-1
            local=points@rotation;size=np.maximum(np.ptp(local,axis=0),1e-6)
            center=((local.min(0)+local.max(0))/2)@rotation.T
        occupancy = abs(region.volume) / max(float(np.prod(size)), 1e-12) if region.is_watertight else 0
        # An OBB primitive is an initial editable hypothesis, never proof of a panel.
        geometry, confidence = ('box', None) if occupancy > .96 else ('unresolved', None)
        world_span = np.ptp(region.bounds, axis=0)
        thin = world_span[2] < max(world_span[:2])*.2
        high = center[2] > mesh.bounds[0, 2] + total_height*.6
        if furniture_type == 'cabinet':
            label = 'shelf' if thin else 'cabinet panel'
        elif thin:
            label = 'tabletop' if furniture_type == 'table' and high else 'seat' if furniture_type == 'chair' else 'support beam'
        elif world_span[2] > max(world_span[:2])*2:
            label = 'leg' if center[2] < mesh.bounds[0, 2]+total_height*.55 else 'backrest or upright'
        else:
            label = 'structural component'
        if regions is not None:
            proposed=next(r for r in regions if len(r['face_ids'])==len(ids) and r['face_ids'][0] in ids)
            label=proposed.get('category',label)
        uncertainty = ['Geometric region may contain multiple manufactured parts']
        if geometry == 'unresolved':
            uncertainty.append('No reliable primitive fit; requires an existing engineering plan or manual review')
            unresolved.extend(map(int, ids))
        components.append(ComponentRegion(len(components), np.asarray(ids), label, geometry, center, size, rotation,
                                           confidence, uncertainty))
    labels = np.full(len(mesh.faces), -1, dtype=np.int32)
    for component in components:
        labels[component.face_ids] = component.component_id
    data.face_components = labels
    unresolved.extend(np.flatnonzero(labels<0).tolist())
    graph = dict(components=[dict(id=c.component_id, label=c.label, candidate_geometry=c.geometry,
        estimated_size_mm=c.size.tolist(), confidence=c.confidence,confidence_status='uncalibrated', uncertainties=c.uncertainties,
        face_count=len(c.face_ids), fixed_measurements=[]) for c in components],
        relationships=[], unresolved_faces=sorted(set(unresolved)), review_required=True)
    # Spatial contact candidates and repeated-size/symmetry hints are evidence,
    # not inferred manufacturing joints.
    for i, left in enumerate(components):
        for right in components[i+1:]:
            a = mesh.triangles[left.face_ids].reshape(-1, 3)
            b = mesh.triangles[right.face_ids].reshape(-1, 3)
            gap = np.maximum(np.maximum(a.min(0)-b.max(0), b.min(0)-a.max(0)), 0)
            if np.linalg.norm(gap) < 1:
                graph['relationships'].append(dict(left=left.component_id, right=right.component_id, kind='possible_contact'))
            if np.allclose(np.sort(left.size), np.sort(right.size), rtol=.05, atol=.5):
                graph['relationships'].append(dict(left=left.component_id, right=right.component_id, kind='repeated_dimensions'))
    return components, graph
