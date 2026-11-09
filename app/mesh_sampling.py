"""Deterministic stratified, area-aware surface samples with unbiased weights."""
from dataclasses import dataclass
import numpy as np
from .geometry_cache import ResourceBudget, content_hash

STAGES = {'economy': (25_000, 12), 'balanced': (100_000, 24),
          'quality': (300_000, 36), 'extended': (750_000, 48)}


@dataclass
class SurfaceSamples:
    points: np.ndarray
    normals: np.ndarray
    face_ids: np.ndarray
    component_ids: np.ndarray
    area_weights: np.ndarray
    requested: int
    fallback: bool = False

    @property
    def nbytes(self):
        return sum(a.nbytes for a in (self.points, self.normals, self.face_ids, self.component_ids, self.area_weights))


def sample_surface(data, count, *, seed=42, adaptive=True, importance=None, budget=None, cache=None):
    if not 100 <= count <= 1_000_000:
        raise ValueError('Surface point count must be within 100..1,000,000')
    budget = budget or ResourceBudget()
    actual = budget.sample_count(count)
    key = content_hash(data.identity, count, actual, seed, adaptive, importance if importance is not None else [])
    if cache:
        existing = cache.get(key) or cache.load_arrays(key)
        if isinstance(existing, SurfaceSamples):
            return existing
        if existing is not None:
            return SurfaceSamples(**existing, requested=count, fallback=actual < count)
    mesh = data.mesh
    area = mesh.area_faces
    labels = data.face_components
    unique, inverse = np.unique(labels, return_inverse=True)
    component_area = np.bincount(inverse, weights=area)
    # Half area-weighted, half equal component mass: small supports retain a quota.
    mass = .5 * component_area / component_area.sum() + .5 / len(unique)
    curved = np.asarray(mesh.metadata.get('curvature', np.zeros(len(area))))
    modifier = 1 + 3 * curved / np.pi if adaptive else np.ones(len(area))
    if importance is not None:
        importance = np.asarray(importance)
        if importance.shape != area.shape or not np.isfinite(importance).all() or (importance < 0).any():
            raise ValueError('Importance must be a finite nonnegative weight per triangle')
        modifier *= 1 + np.minimum(importance, 10)
    probability = np.zeros(len(area))
    for index in range(len(unique)):
        selected = inverse == index
        local = area[selected] * modifier[selected]
        probability[selected] = mass[index] * local / local.sum()
    rng = np.random.default_rng(seed)
    # Stratified inverse-CDF sampling is spatially balanced across face area and
    # avoids the quadratic rejection cost of dense Poisson-disk oversampling.
    ids = np.searchsorted(np.cumsum(probability), (np.arange(actual) + rng.random(actual)) / actual)
    ids = np.minimum(ids, len(area) - 1)
    try:
        bary = rng.random((actual, 2))
        root = np.sqrt(bary[:, 0])
        coefficients = np.column_stack((1 - root, root * (1 - bary[:, 1]), root * bary[:, 1]))
        points = np.einsum('ni,nij->nj', coefficients, mesh.triangles[ids]).astype(np.float32)
        weights = (area[ids] / probability[ids] / actual).astype(np.float64)
        weights /= weights.sum()
        samples = SurfaceSamples(points, mesh.face_normals[ids].astype(np.float32), ids.astype(np.int32),
                                 labels[ids].astype(np.int32), weights, count, actual < count)
    except MemoryError:
        if actual <= 1000:
            raise
        smaller = ResourceBudget(budget.memory_mb, max(100, actual // 2), budget.workers)
        samples = sample_surface(data, count, seed=seed, adaptive=adaptive, importance=importance, budget=smaller, cache=cache)
    if cache:
        cache.put(key, samples, samples.nbytes)
        try:
            cache.save_arrays(key, **{name: getattr(samples, name) for name in
                ('points', 'normals', 'face_ids', 'component_ids', 'area_weights')})
        except (OSError, MemoryError):
            pass  # Optional cache failure must not discard valid surface samples.
    return samples
