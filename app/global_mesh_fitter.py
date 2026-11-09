"""Whole-surface, camera-free fitting against one immutable triangle reference.

Both trial tessellations and rebuilt native CAD use the same bidirectional
closest-triangle objective. No provider client, voxel volume or camera is used.
"""
from dataclasses import dataclass
import time
import numpy as np
import trimesh
from .geometry_acceleration import SurfaceIndex, deviation_statistics
from .geometry_cache import GeometryCache, ResourceBudget, content_hash
from .mesh_analyzer import MeshData
from .mesh_sampling import SurfaceSamples, sample_surface
from .mesh_cad_schema import DenseOptimizationOptions


def inside_outside_status(data, face_limit=200_000):
    """Conservatively establish Open3D's SDF preconditions once per reference."""
    mesh = data.mesh
    if not mesh.is_watertight:
        return dict(eligible=False, reason='open or non-manifold reference mesh')
    if not mesh.is_winding_consistent or not np.isfinite(mesh.volume) or mesh.volume <= 0:
        return dict(eligible=False, reason='inconsistent or inverted surface orientation')
    if len(mesh.faces) > face_limit:
        return dict(eligible=False, reason='inside/outside validation exceeds bounded face budget')
    import open3d as o3d
    native = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(mesh.vertices), o3d.utility.Vector3iVector(mesh.faces))
    if not native.is_vertex_manifold() or not native.is_edge_manifold(allow_boundary_edges=False):
        return dict(eligible=False, reason='non-manifold reference topology')
    if native.is_self_intersecting():
        return dict(eligible=False, reason='self-intersections or intersecting shells')
    regions = mesh.split(only_watertight=False)
    if any(region.volume <= 0 for region in regions):
        return dict(eligible=False, reason='one or more shells have unreliable orientation')
    # Nested/overlapping shell bounds can encode a cavity or two intersecting
    # parts. Refuse ambiguous parity semantics rather than guessing a union.
    for index, left in enumerate(regions):
        for right in regions[index+1:]:
            if np.all(np.minimum(left.bounds[1],right.bounds[1])-np.maximum(left.bounds[0],right.bounds[0]) >= -1e-7):
                return dict(eligible=False, reason='nested, touching or overlapping component bounds')
    return dict(eligible=True, reason='closed, oriented, manifold, nonintersecting disjoint shells')


def soft_l1_residual(values, delta):
    """Transform residuals before area weighting so oversampling adds no bias."""
    values = np.asarray(values, dtype=np.float64)
    scaled = values/delta
    # Algebraically equivalent to 2*delta**2*(sqrt(1+scaled**2)-1),
    # with no cancellation for submillimetre distances near convergence.
    cost = 2*values**2/(np.sqrt(1+scaled**2)+1)
    return np.sign(values)*np.sqrt(cost)


@dataclass
class GlobalSurfaceEvaluation:
    residuals: np.ndarray
    score: float
    report: dict
    forward: dict
    reverse: dict


class GlobalMeshFitter:
    def __init__(self, reference, options=None, *, budget=None, cache=None):
        self.options = DenseOptimizationOptions.model_validate(options or {})
        self.budget = budget or ResourceBudget(self.options.memory_mb, workers=self.options.workers)
        self.cache = cache if self.options.geometry_cache else None
        # A copy prevents a registration or display transform from invalidating
        # a spatial index which is already in use.
        self.reference = MeshData(reference.mesh.copy(), reference.face_components.copy(), dict(reference.report))
        self.identity = self.reference.identity
        key = content_hash('global_reference_index_v1',self.identity,self.options.workers)
        self.reference_index = self.cache.get(key) if self.cache else None
        if self.reference_index is None:
            self.reference_index = SurfaceIndex(self.reference,threads=self.options.workers)
            if self.cache:
                self.cache.put(key,self.reference_index,size=int(self.reference.mesh.vertices.nbytes+self.reference.mesh.faces.nbytes)*4)
        semantics_key = content_hash('global_sdf_semantics_v1',self.identity)
        semantics = self.cache.get(semantics_key) if self.cache else None
        if semantics is None:
            semantics = inside_outside_status(self.reference) if self.options.sdf != 'false' else dict(eligible=False,reason='SDF disabled')
            if self.cache:
                self.cache.put(semantics_key,semantics)
        self.sdf_status = {**semantics, 'requested':self.options.sdf,
                           'enabled':semantics['eligible'] and self.options.sdf != 'false',
                           'sampling':'sparse query points; no dense voxel field'}
        self.query_seconds = 0.
        self.evaluations = 0
        self._samples = {}

    def samples(self, count, importance=None):
        key = content_hash(count,importance if importance is not None else [])
        if key not in self._samples:
            self._samples[key] = sample_surface(self.reference,count,budget=self.budget,cache=self.cache,
                adaptive=self.options.adaptive_sampling,importance=importance)
        return self._samples[key]

    def signed_or_unsigned(self, points):
        if self.sdf_status['enabled']:
            return self.reference_index.signed_distances(points,semantics_verified=True)
        return self.reference_index.closest(points)['distances']

    def evaluate(self, cad, cad_samples, reference_samples, *, include_report=True, normal_reference=None):
        started = time.perf_counter()
        options = self.options
        index = SurfaceIndex(cad,threads=self.budget.workers)
        forward = self.reference_index.closest(cad_samples.points)
        reverse = index.closest(reference_samples.points)
        a,b = forward['distances'],reverse['distances']
        signed = self.reference_index.signed_distances(cad_samples.points,semantics_verified=True) if self.sdf_status['enabled'] else a
        wa,wb = cad_samples.area_weights,reference_samples.area_weights
        pieces=[]
        losses={}
        def add(name, values, weights, coefficient, robust=True):
            residual = soft_l1_residual(values,options.robust_delta_mm) if robust else np.asarray(values)
            term = residual*np.sqrt(weights*coefficient)
            pieces.append(term)
            losses[name] = float(term@term)
        add('cad_to_reference',signed,wa,options.surface_weight*.5)
        if options.bidirectional:
            add('reference_to_cad',b,wb,options.surface_weight*.5)
        normals_reliable = bool(self.reference.mesh.is_winding_consistent and
                                (not self.reference.mesh.is_watertight or self.reference.mesh.volume > 0))
        if options.point_to_plane and normals_reliable:
            plane_a = np.einsum('ij,ij->i',cad_samples.points-forward['points'],forward['normals'])
            plane_b = np.einsum('ij,ij->i',reference_samples.points-reverse['points'],reverse['normals'])
            add('plane_cad',plane_a,wa,options.point_to_plane_weight*.5)
            if options.bidirectional:
                add('plane_reference',plane_b,wb,options.point_to_plane_weight*.5)
        # Freeze only normal correspondences during a numerical linearization.
        # Nearest-face identities jump at triangle boundaries; differentiating
        # those jumps creates spurious gradients for translations/dimensions.
        # Every real objective and native acceptance still rematches surfaces.
        matched_ref_normals=forward['normals'] if normal_reference is None else normal_reference.forward['normals']
        matched_cad_normals=(reverse['normals'] if normal_reference is None else
                             cad.mesh.face_normals[normal_reference.reverse['face_ids']])
        normal_a = np.clip(np.einsum('ij,ij->i',cad_samples.normals,matched_ref_normals),-1,1)
        normal_b = np.clip(np.einsum('ij,ij->i',reference_samples.normals,matched_cad_normals),-1,1)
        if not self.reference.mesh.is_watertight:
            normal_a,normal_b = abs(normal_a),abs(normal_b)
        if options.normal_loss and normals_reliable:
            add('normal_cad',np.sqrt(np.maximum(0,1-normal_a))*options.robust_delta_mm,wa,options.normal_weight*.5,False)
            if options.bidirectional:
                add('normal_reference',np.sqrt(np.maximum(0,1-normal_b))*options.robust_delta_mm,wb,options.normal_weight*.5,False)
        if options.component_aware:
            reference_labels = np.unique(self.reference.face_components)
            cad_labels = np.unique(cad.face_components)
            valid_a = np.isin(cad_samples.component_ids,reference_labels)
            valid_b = np.isin(reference_samples.component_ids,cad_labels) & (reference_samples.component_ids >= 0)
            component_a = np.zeros(len(a)); component_b = np.zeros(len(b))
            if valid_a.any():
                component_a[valid_a] = self.reference_index.closest(cad_samples.points[valid_a],cad_samples.component_ids[valid_a])['distances']
            if valid_b.any():
                component_b[valid_b] = index.closest(reference_samples.points[valid_b],reference_samples.component_ids[valid_b])['distances']
            add('component_cad',component_a,wa,options.component_weight*.5)
            if options.bidirectional:
                add('component_reference',component_b,wb,options.component_weight*.5)
        residuals = np.concatenate(pieces)
        report = {}
        if include_report:
            combined = np.concatenate((a,b)); weights = np.concatenate((wa,wb))*.5
            per_component = {}
            for component in sorted(set(cad_samples.component_ids)|set(reference_samples.component_ids)):
                ca,rb = cad_samples.component_ids == component,reference_samples.component_ids == component
                item={}
                if ca.any(): item['cad_to_reference'] = deviation_statistics(a[ca],wa[ca])
                if rb.any(): item['reference_to_cad'] = deviation_statistics(b[rb],wb[rb])
                item['correspondence'] = 'unassigned' if component < 0 else 'geometric region correspondence; not proof of a manufactured part'
                # Retain the previous per-component scalar keys for old reports.
                item.update(item.get('reference_to_cad',item.get('cad_to_reference',{})))
                per_component[str(int(component))]=item
            summary = deviation_statistics(combined,weights)
            report = dict(cad_to_reference=deviation_statistics(a,wa),reference_to_cad=deviation_statistics(b,wb),
                symmetric_mean_mm=summary['mean_mm'],rms_mm=summary['rms_mm'],p95_mm=summary['p95_mm'],
                maximum_observed_mm=summary['maximum_observed_mm'],components=per_component,
                normal_loss=float(.5*(np.sum((1-normal_a)*wa)+np.sum((1-normal_b)*wb))),
                normal_rms_angle_deg=float(np.sqrt(.5*(np.sum(np.rad2deg(np.arccos(normal_a))**2*wa)+np.sum(np.rad2deg(np.arccos(normal_b))**2*wb)))),
                normals_reliable=normals_reliable,normal_orientation='oriented' if self.reference.mesh.is_watertight else 'unoriented open surface',
                unmatched_reference_area_fraction=float(wb[b>options.unmatched_tolerance_mm].sum()),
                unmatched_cad_area_fraction=float(wa[a>options.unmatched_tolerance_mm].sum()),
                oversized_cad_area_fraction=(float(wa[signed>options.unmatched_tolerance_mm].sum())
                    if self.sdf_status['enabled'] else None),
                oversized_status='signed outside-reference samples' if self.sdf_status['enabled'] else 'unverified with unsigned distances',
                unmatched_tolerance_mm=options.unmatched_tolerance_mm,
                cad_points=len(a),reference_points=len(b),surface_coverage='entire triangle surface; no visibility filtering',
                distance_method='Open3D closest point on triangles in both directions',
                cad_tessellation_tolerance_mm=.25,objective_loss_components=losses,sdf=self.sdf_status)
        self.query_seconds += time.perf_counter()-started
        self.evaluations += 1
        return GlobalSurfaceEvaluation(residuals,float(residuals@residuals),report,forward,reverse)


class NativeMeshProxy:
    """Warp a cached B-Rep tessellation and its fixed barycentric surface points."""
    def __init__(self,plan,cad,samples):
        from .cad_geometry_proxy import ComponentProxy
        meshes,labels,vertex_labels,source_face_ids=[],[],[],[]
        for component in range(len(plan.parts)):
            ids = np.flatnonzero(cad.face_components == component)
            mesh = cad.mesh.submesh([ids],append=True,repair=False)
            meshes.append(mesh)
            source_face_ids.extend(ids.tolist())
            labels.extend([component]*len(mesh.faces)); vertex_labels.extend([component]*len(mesh.vertices))
        mesh = trimesh.util.concatenate(meshes)
        self.data = MeshData(mesh,np.asarray(labels,dtype=np.int32),dict(cad.report))
        from dataclasses import replace
        inverse_order = np.empty(len(source_face_ids),dtype=int)
        inverse_order[np.asarray(source_face_ids)] = np.arange(len(source_face_ids))
        self.samples = replace(samples,face_ids=inverse_order[samples.face_ids])
        self.base_area = mesh.area_faces.copy()
        self.barycentric = trimesh.triangles.points_to_barycentric(mesh.triangles[self.samples.face_ids],samples.points)
        points = np.asarray(mesh.vertices)
        vertex_samples = SurfaceSamples(points,np.asarray(mesh.vertex_normals),np.zeros(len(points),dtype=np.int32),
            np.asarray(vertex_labels,dtype=np.int32),np.full(len(points),1/max(len(points),1)),len(points))
        self.deformer = ComponentProxy(plan,vertex_samples)

    def evaluate(self,plan):
        points,_ = self.deformer.evaluate(plan)
        mesh = trimesh.Trimesh(vertices=points,faces=self.data.mesh.faces,process=False)
        if not np.isfinite(points).all() or np.any(mesh.area_faces < 1e-12):
            raise ValueError('Trial CAD proxy contains invalid or degenerate triangles')
        surface = np.einsum('ni,nij->nj',self.barycentric,mesh.triangles[self.samples.face_ids]).astype(np.float32)
        weights = self.samples.area_weights*(mesh.area_faces[self.samples.face_ids]/self.base_area[self.samples.face_ids])
        weights /= weights.sum()
        samples = SurfaceSamples(surface,mesh.face_normals[self.samples.face_ids].astype(np.float32),self.samples.face_ids,
            self.samples.component_ids,weights,self.samples.requested,self.samples.fallback)
        return MeshData(mesh,self.data.face_components,dict(self.data.report)),samples
