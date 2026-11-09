"""Select views by new visible area, component coverage and current mismatch."""
import numpy as np
from .multiview_geometry import analyze_view


def select_views(index, cameras, *, maximum=None, coverage_gain=.005, mismatch=None, complete=False):
    analyses = [analyze_view(index, camera) for camera in cameras]
    if complete:
        return cameras, analyses
    area = index.data.mesh.area_faces
    importance = np.ones(len(area)) if mismatch is None else 1 + np.clip(mismatch, 0, 10)
    curvature = np.asarray(index.data.mesh.metadata.get('curvature', np.zeros(len(area))))
    importance *= 1 + curvature / np.pi
    remaining, covered, components, selected = set(range(len(cameras))), set(), set(), []
    while remaining and len(selected) < (maximum or len(cameras)):
        def gain(i):
            ids = np.array(list(set(analyses[i]['visible_faces']) - covered), dtype=int)
            novelty = len(set(analyses[i]['visible_components']) - components)
            return (area[ids]*importance[ids]).sum() / area.sum() + .05 * novelty
        best = max(sorted(remaining), key=gain)
        if len(selected) >= 4 and gain(best) < coverage_gain:
            break
        selected.append(best)
        remaining.remove(best)
        covered.update(analyses[best]['visible_faces'])
        components.update(analyses[best]['visible_components'])
    return [cameras[i] for i in selected], [analyses[i] for i in selected]
