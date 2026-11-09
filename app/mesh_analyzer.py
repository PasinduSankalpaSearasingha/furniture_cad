"""Read GLB scene instances and OBJ without changing the original artifact."""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import trimesh
from .geometry_cache import content_hash


@dataclass
class MeshData:
    mesh: trimesh.Trimesh
    face_components: np.ndarray
    report: dict = field(default_factory=dict)

    @property
    def identity(self):
        return content_hash(np.asarray(self.mesh.vertices), np.asarray(self.mesh.faces), self.face_components)


def analyze_mesh(mesh, face_components=None,*,track_source=False):
    mesh = mesh.copy()
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if not len(vertices) or not len(faces):
        raise ValueError('Mesh has no triangle surface')
    if not np.isfinite(vertices).all() or np.any(faces < 0) or np.any(faces >= len(vertices)):
        raise ValueError('Mesh contains invalid coordinates or triangle indices')
    if len(faces) > 2_000_000 or len(vertices) > 2_000_000:
        raise ValueError('Mesh exceeds the two-million vertex/face processing limit')
    areas = mesh.area_faces
    valid = np.isfinite(areas) & (areas > 1e-12)
    degenerate = np.flatnonzero(~valid)
    if not valid.any():
        raise ValueError('All triangles are degenerate')
    labels = np.zeros(len(faces), dtype=np.int32) if face_components is None else np.asarray(face_components, dtype=np.int32)
    if len(labels) != len(faces):
        raise ValueError('Face component count does not match mesh')
    mesh.update_faces(valid)
    mesh.remove_unreferenced_vertices()
    # Weld only identical vertices on the working copy, preserving triangle order.
    mesh.merge_vertices()
    # Keep source IDs through degenerate removal and vertex welding. Repeated
    # normalization/registration composes the map rather than resetting it.
    metadata=mesh.metadata
    if track_source or '_cad_source_face_ids' in metadata:
        previous_faces=np.asarray(metadata['_cad_source_face_ids'] if '_cad_source_face_ids' in metadata else np.arange(len(faces)),dtype=np.int32)
        previous_pairs=np.asarray(metadata['_cad_source_vertex_pairs'] if '_cad_source_vertex_pairs' in metadata else np.column_stack((np.arange(len(vertices)),np.arange(len(vertices)))),dtype=np.int32)
        old_to_new=np.full(len(vertices),-1,dtype=np.int32)
        old_to_new[faces[valid].ravel()]=np.asarray(mesh.faces).ravel()
        pairs=np.column_stack((old_to_new[previous_pairs[:,0]],previous_pairs[:,1]))
        metadata['_cad_source_face_ids']=previous_faces[valid]
        metadata['_cad_source_vertex_pairs']=np.unique(pairs[pairs[:,0]>=0],axis=0)
        metadata.setdefault('_cad_source_face_count',len(faces))
        metadata.setdefault('_cad_source_vertex_count',len(vertices))
    counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
    adjacency = mesh.face_adjacency
    angles = mesh.face_adjacency_angles
    curvature = np.zeros(len(mesh.faces))
    if len(adjacency):
        np.maximum.at(curvature, adjacency[:, 0], angles)
        np.maximum.at(curvature, adjacency[:, 1], angles)
    groups = trimesh.graph.connected_components(adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)
    report = dict(vertices=len(mesh.vertices), triangles=len(mesh.faces), removed_degenerate_faces=degenerate.tolist(),
        watertight=bool(mesh.is_watertight), winding_consistent=bool(mesh.is_winding_consistent),
        boundary_edges=int(np.sum(counts == 1)), nonmanifold_edges=int(np.sum(counts > 2)),
        connected_regions=len(groups), area=float(mesh.area), bounds=mesh.bounds.tolist(),
        sharp_edge_count=int(np.sum(angles > np.deg2rad(35))),
        curvature_method='maximum adjacent-face dihedral angle', physical_accuracy_verified=False,
        uncertain_regions=['Hidden surfaces and physical proportions are generated estimates'])
    if not mesh.is_watertight:
        report['uncertain_regions'].append('Open surface boundaries')
    if not mesh.is_winding_consistent:
        report['uncertain_regions'].append('Inconsistent face winding; normal loss is unreliable')
    mesh.metadata['curvature'] = curvature
    return MeshData(mesh, labels[valid], report)


def load_mesh(path, max_bytes=256 * 1024**2):
    path = Path(path)
    if path.suffix.lower() not in ('.glb', '.obj'):
        raise ValueError('Only GLB and OBJ reference meshes are supported')
    if not 0 < path.stat().st_size <= max_bytes:
        raise ValueError('Mesh file exceeds its byte budget or is empty')
    # Resolver=None prevents OBJ material paths from reading unrelated local files.
    with path.open('rb') as stream:
        loaded = trimesh.load(stream, file_type=path.suffix[1:], force='scene', process=False, resolver=None)
    meshes, labels, names, ranges = [], [], [], []
    face_offset=vertex_offset=0
    for node in loaded.graph.nodes_geometry:
        transform, geometry_name = loaded.graph[node]
        geometry = loaded.geometry[geometry_name]
        if not isinstance(geometry, trimesh.Trimesh) or not len(geometry.faces):
            continue
        geometry = geometry.copy()
        geometry.apply_transform(transform)
        ranges.append(dict(scene_node=str(node),geometry_name=str(geometry_name),source_face_offset=face_offset,
            source_face_count=len(geometry.faces),source_vertex_offset=vertex_offset,source_vertex_count=len(geometry.vertices),
            scene_transform=np.asarray(transform).tolist()))
        face_offset+=len(geometry.faces);vertex_offset+=len(geometry.vertices)
        labels.append(np.full(len(geometry.faces), len(meshes), dtype=np.int32))
        meshes.append(geometry)
        names.append(str(node))
    if not meshes:
        raise ValueError('Scene contains no triangle meshes')
    combined=trimesh.util.concatenate(meshes)
    combined.metadata={key:value for key,value in combined.metadata.items() if not key.startswith('_cad_')}
    result = analyze_mesh(combined, np.concatenate(labels),track_source=True)
    result.report.update(scene_nodes=names,scene_index_ranges=ranges,source_sha256=content_hash(path.read_bytes()),
        source_face_count=face_offset,source_vertex_count=vertex_offset)
    return result
