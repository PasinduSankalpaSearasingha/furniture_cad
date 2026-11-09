"""Shared orthographic cameras and in-memory depth/visibility/silhouettes."""
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import binary_erosion
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class VirtualCamera:
    azimuth_deg: float
    elevation_deg: float
    eye: tuple
    target: tuple
    span: float
    resolution: int = 96

    def basis(self):
        forward = np.asarray(self.target) - self.eye
        forward /= np.linalg.norm(forward)
        up = np.array([0., 0., 1.])
        if abs(np.dot(forward, up)) > .99:
            up = np.array([0., 1., 0.])
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        return right, np.cross(right, forward), forward

    def rays(self):
        right, up, forward = self.basis()
        coordinates = ((np.arange(self.resolution) + .5) / self.resolution - .5) * self.span
        x, y = np.meshgrid(coordinates, coordinates[::-1])
        origins = np.asarray(self.eye) + x.ravel()[:, None]*right + y.ravel()[:, None]*up
        return origins, np.broadcast_to(forward, origins.shape)

    def project(self, points):
        right, up, forward = self.basis()
        local = np.asarray(points) - self.target
        return np.column_stack((local @ right, local @ up)), local @ forward


def generate_cameras(bounds, count=36, *, resolution=96, top_bottom=False):
    if count not in (12, 24, 36, 48) or not 24 <= resolution <= 512:
        raise ValueError('Use 12, 24, 36 or 48 virtual views at 24..512 pixels')
    settings = {12: (4, [-30, 0, 30]), 24: (8, [-25, 0, 25]),
                36: (12, [-30, 0, 30]), 48: (12, [-45, -15, 15, 45])}
    azimuths, elevations = settings[count]
    center = np.mean(bounds, axis=0)
    diagonal = np.linalg.norm(np.asarray(bounds)[1] - bounds[0])
    orientations = [(a, e) for e in elevations for a in np.arange(azimuths) * 360 / azimuths]
    if top_bottom:
        orientations += [(0, 90), (0, -90)]
    cameras = []
    for azimuth, elevation in orientations:
        a, e = np.deg2rad([azimuth, elevation])
        direction = np.array([np.cos(e)*np.cos(a), np.cos(e)*np.sin(a), np.sin(e)])
        cameras.append(VirtualCamera(float(azimuth), float(elevation), tuple(center+direction*diagonal*2),
                                     tuple(center), diagonal*1.1, resolution))
    return cameras


def analyze_view(index, camera):
    origins, directions = camera.rays()
    hit = index.rays(origins, directions)
    mask = np.isfinite(hit['t_hit']).reshape(camera.resolution, camera.resolution)
    boundary = mask & ~binary_erosion(mask)
    visible_ids = np.unique(hit['primitive_ids'][mask.ravel()]).astype(int)
    pixels = np.column_stack(np.nonzero(boundary))
    return dict(mask=mask, boundary=pixels, depth=hit['t_hit'].reshape(mask.shape),
                normals=hit['primitive_normals'].reshape(*mask.shape, 3), visible_faces=visible_ids,
                visible_components=np.unique(index.data.face_components[visible_ids]))


def compare_views(reference, candidate, camera):
    a, b = reference['mask'], candidate['mask']
    union = np.count_nonzero(a | b)
    iou_loss = 1 - np.count_nonzero(a & b) / max(1, union)
    left, right = reference['boundary'], candidate['boundary']
    if len(left) and len(right):
        error = .5*(cKDTree(left).query(right)[0].mean() + cKDTree(right).query(left)[0].mean())
    else:
        error = camera.resolution if len(left) != len(right) else 0
    return dict(silhouette_iou_loss=float(iou_loss), boundary_mean_mm=float(error*camera.span/camera.resolution))


def multiview_report(reference_index, candidate_index, cameras, reference_views=None):
    views = reference_views or [analyze_view(reference_index, camera) for camera in cameras]
    covered = set()
    scores = []
    for camera, reference in zip(cameras, views):
        candidate = analyze_view(candidate_index, camera)
        score = compare_views(reference, candidate, camera)
        # Count a reference face only once across views when aggregating.
        fresh = set(reference['visible_faces']) - covered
        score.update(azimuth_deg=camera.azimuth_deg, elevation_deg=camera.elevation_deg, new_faces=len(fresh))
        score['new_area_mm2'] = float(reference_index.data.mesh.area_faces[list(fresh)].sum()) if fresh else 0.
        covered.update(reference['visible_faces'])
        scores.append(score)
    weights = np.array([s['new_area_mm2'] for s in scores], dtype=float)
    if not weights.sum():
        weights[:] = 1
    return dict(views=scores, count=len(cameras), covered_faces=len(covered),
                covered_area_fraction=float(reference_index.data.mesh.area_faces[list(covered)].sum()/reference_index.data.mesh.area),
                silhouette_iou_loss=float(np.average([s['silhouette_iou_loss'] for s in scores], weights=weights)),
                virtual_views_supply_new_physical_evidence=False)
