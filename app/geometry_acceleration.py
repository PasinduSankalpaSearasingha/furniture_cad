"""CPU closest-triangle queries and ray intersections; KD trees are approximate."""
import numpy as np
from scipy.spatial import cKDTree


class SurfaceIndex:
    def __init__(self, data, *, chunk_size=32768, threads=4, component_aware=True):
        import open3d as o3d
        if not 256 <= chunk_size <= 65536 or not 1 <= threads <= 32:
            raise ValueError('Invalid spatial query resource limits')
        self.data, self.chunk_size, self.threads = data, chunk_size, threads
        self.scene = o3d.t.geometry.RaycastingScene(nthreads=threads)
        self.scene.add_triangles(o3d.core.Tensor(np.asarray(data.mesh.vertices, dtype=np.float32)),
                                 o3d.core.Tensor(np.asarray(data.mesh.faces, dtype=np.uint32)))
        self.components = {}
        self.component_aware = component_aware

    def closest(self, points, components=None):
        import open3d as o3d
        points = np.asarray(points, dtype=np.float32).reshape(-1, 3)
        if not np.isfinite(points).all():
            raise ValueError('Nonfinite query coordinates')
        if components is not None:
            labels = np.asarray(components)
            if len(labels) != len(points):
                raise ValueError('Component query count mismatch')
            out = dict(points=np.empty_like(points), normals=np.empty_like(points),
                       distances=np.empty(len(points)), face_ids=np.empty(len(points), dtype=np.int64))
            for component in np.unique(labels):
                selected = labels == component
                ids = np.flatnonzero(self.data.face_components == component)
                if not len(ids):
                    raise ValueError(f'No reference surface for component {component}')
                if component not in self.components:
                    from .mesh_analyzer import MeshData
                    mesh = self.data.mesh.submesh([ids], append=True, repair=False)
                    self.components[component] = (SurfaceIndex(MeshData(mesh, np.zeros(len(ids), dtype=np.int32)),
                        chunk_size=self.chunk_size, threads=self.threads, component_aware=False), ids)
                index, mapping = self.components[component]
                result = index.closest(points[selected])
                for key in ('points', 'normals', 'distances'):
                    out[key][selected] = result[key]
                out['face_ids'][selected] = mapping[result['face_ids']]
            return out
        chunks = []
        for offset in range(0, len(points), self.chunk_size):
            result = self.scene.compute_closest_points(o3d.core.Tensor(points[offset:offset+self.chunk_size]), nthreads=self.threads)
            chunks.append({key: result[key].numpy() for key in ('points', 'primitive_normals', 'primitive_ids')})
        if not chunks:
            return dict(points=points.copy(), normals=points.copy(), distances=np.empty(0), face_ids=np.empty(0, dtype=int))
        closest = np.concatenate([r['points'] for r in chunks])
        return dict(points=closest, normals=np.concatenate([r['primitive_normals'] for r in chunks]),
            face_ids=np.concatenate([r['primitive_ids'] for r in chunks]).astype(np.int64),
            distances=np.linalg.norm(points - closest, axis=1))

    def rays(self, origins, directions):
        import open3d as o3d
        rays = np.column_stack((origins, directions)).astype(np.float32)
        outputs = []
        for offset in range(0, len(rays), self.chunk_size):
            result = self.scene.cast_rays(o3d.core.Tensor(rays[offset:offset+self.chunk_size]), nthreads=self.threads)
            outputs.append({key: result[key].numpy() for key in ('t_hit', 'primitive_ids', 'primitive_normals')})
        return {key: np.concatenate([r[key] for r in outputs]) for key in outputs[0]}

    def signed_distances(self, points, *, semantics_verified=False):
        """Sparse CPU SDF queries; callers must first establish closed semantics."""
        import open3d as o3d
        if not semantics_verified:
            raise ValueError('Signed distances require verified inside/outside semantics')
        points = np.asarray(points, dtype=np.float32).reshape(-1, 3)
        if not np.isfinite(points).all():
            raise ValueError('Nonfinite signed-distance coordinates')
        result = np.empty(len(points), dtype=np.float32)
        for offset in range(0, len(points), self.chunk_size):
            result[offset:offset+self.chunk_size] = self.scene.compute_signed_distance(
                o3d.core.Tensor(points[offset:offset+self.chunk_size]), nthreads=self.threads, nsamples=3).numpy()
        return result

    def visible(self, points, eye, tolerance=.1):
        direction = np.asarray(points) - eye
        length = np.linalg.norm(direction, axis=1)
        result = self.rays(np.broadcast_to(eye, direction.shape), direction / np.maximum(length[:, None], 1e-12))
        return np.abs(result['t_hit'] - length) <= tolerance


class PointIndex:
    """Point-cloud approximation, never reported as exact surface distance."""
    def __init__(self, points, workers=4):
        self.tree, self.workers = cKDTree(points), workers

    def nearest(self, points):
        return self.tree.query(points, workers=self.workers)


def deviation_statistics(distances, weights=None):
    distances = np.asarray(distances, dtype=float)
    if not len(distances) or not np.isfinite(distances).all():
        raise ValueError('Deviation requires nonempty finite distances')
    weights = np.ones(len(distances)) / len(distances) if weights is None else np.asarray(weights) / np.sum(weights)
    order = np.argsort(distances)
    cdf = np.cumsum(weights[order])
    quantile = lambda q: float(distances[order[min(np.searchsorted(cdf, q), len(order)-1)]])
    return dict(mean_mm=float(np.sum(distances * weights)), rms_mm=float(np.sqrt(np.sum(distances**2*weights))), median_mm=quantile(.5),
                p95_mm=quantile(.95), maximum_observed_mm=float(distances.max()))
