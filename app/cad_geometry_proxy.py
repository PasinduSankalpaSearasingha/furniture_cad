"""Mathematical CAD trial proxies; authoritative solids remain in photo_cad."""
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.interpolate import RectBivariateSpline
from .photo_schema import number


def rational_surface_points(controls,weights,uv):
    from scipy.special import comb
    nu,nv=controls.shape[:2]
    u,v=np.asarray(uv).T
    bu=np.column_stack([comb(nu-1,i)*u**i*(1-u)**(nu-1-i) for i in range(nu)])
    bv=np.column_stack([comb(nv-1,j)*v**j*(1-v)**(nv-1-j) for j in range(nv)])
    basis=np.einsum('ni,nj->nij',bu,bv)*weights
    return np.einsum('nij,ijc->nc',basis,controls)/basis.sum((1,2))[:,None]


def resolved_surface(surface,values):
    controls=np.array([[[number(v,values) for v in point] for point in row] for row in surface.control_points])
    weights=(np.array([[number(v,values) for v in row] for row in surface.weights])
             if surface.weights is not None else np.ones(controls.shape[:2]))
    return controls,weights


def curve_samples(points,mode,closed=False):
    points=np.asarray(points,dtype=float)
    if closed and not np.allclose(points[0],points[-1]) and mode != 'bezier':
        points=np.vstack((points,points[0]))
    if mode == 'bezier':
        t=np.linspace(0,1,65)[:,None]
        curves=[(1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t**2*c+t**3*d
                for a,b,c,d in (points[i:i+4] for i in range(0,len(points)-1,3))]
        return np.concatenate(curves)
    result=[]
    for a,b in zip(points,points[1:]):
        result.append(a+(b-a)*np.linspace(0,1,65)[:,None])
    return np.concatenate(result)


def resolved_frame(part, values):
    rotation = Rotation.from_euler('xyz', [number(v, values) for v in part.rotation_deg], degrees=True).as_matrix()
    center = np.array([number(v, values) for v in part.position])
    size = np.array([number(v, values) for v in part.size])
    return rotation, center, size


class ComponentProxy:
    """Cached local samples; affine primitives and section/path displacements.

    These are trial approximations. Only rebuilt, checked B-Reps may be accepted.
    """
    def __init__(self, plan, samples):
        self.plan, self.samples = plan, samples
        self.values = {v.key: v.value for v in plan.values}
        self.local = {}
        self.bindings = {}
        self.nurbs_bindings = {}
        for index, part in enumerate(plan.parts):
            selected = samples.component_ids == index
            rotation, center, size = resolved_frame(part, self.values)
            local = (samples.points[selected] - center) @ rotation
            normals = samples.normals[selected] @ rotation
            self.local[index] = selected, local, normals, size
            if part.surface_nurbs:
                controls,weights=resolved_surface(part.surface_nurbs,self.values)
                u,v=np.meshgrid(np.linspace(0,1,65),np.linspace(0,1,65))
                uv=np.column_stack((u.ravel(),v.ravel()))
                grid=rational_surface_points(controls,weights,uv)
                _,nearest=cKDTree(grid).query(local)
                self.nurbs_bindings[index]=(grid[nearest],uv[nearest])
            for name in ('path','outline'):
                controls=getattr(part,name)
                if controls:
                    original=np.array([[number(v,self.values) for v in p] for p in controls])
                    sampled=curve_samples(original,part.curve_mode,closed=name=='outline')
                    _,nearest=cKDTree(sampled).query(local[:,:sampled.shape[1]])
                    self.bindings[index,name]=(sampled,nearest)

    def evaluate(self, plan):
        values = {v.key: v.value for v in plan.values}
        points = np.empty_like(self.samples.points, dtype=float)
        normals = np.empty_like(points)
        for index, (before, after) in enumerate(zip(self.plan.parts, plan.parts)):
            selected, local, local_normals, size = self.local[index]
            rotation, center, new_size = resolved_frame(after, values)
            ratio = new_size / size
            if before.geometry in ('loft','profile_sweep','nurbs_panel'):
                ratio=np.ones(3)
            elif before.geometry == 'outline_panel':
                ratio[:2]=1
            elif before.path:
                ratio[2]=1
                if before.geometry=='round_sweep': ratio[1]=ratio[0]
            moved = local * ratio
            if before.path and len(before.path) == len(after.path):
                original,nearest=self.bindings[index,'path']
                changed = np.array([[number(v, values) for v in p] for p in after.path])
                changed=curve_samples(changed,after.curve_mode)
                moved=changed[nearest]+(local-original[nearest])*ratio
                if before.geometry=='profile_sweep':
                    fraction=nearest/max(len(original)-1,1)
                    old_w=np.interp(fraction,[s.fraction for s in before.sweep_sections],[number(s.width,self.values) for s in before.sweep_sections])
                    new_w=np.interp(fraction,[s.fraction for s in after.sweep_sections],[number(s.width,values) for s in after.sweep_sections])
                    old_d=np.interp(fraction,[s.fraction for s in before.sweep_sections],[number(s.depth,self.values) for s in before.sweep_sections])
                    new_d=np.interp(fraction,[s.fraction for s in after.sweep_sections],[number(s.depth,values) for s in after.sweep_sections])
                    offset=local-original[nearest]
                    moved=changed[nearest]+offset*np.column_stack((new_w/old_w,new_d/old_d,np.ones(len(offset))))
            if before.sections and len(before.sections) == len(after.sections):
                original = np.array([[number(v, self.values) for v in p.center] for p in before.sections])
                changed = np.array([[number(v, values) for v in p.center] for p in after.sections])
                _, nearest = cKDTree(original).query(local)
                moved += changed[nearest] - original[nearest]
                widths_before = np.array([number(p.width, self.values) for p in before.sections])
                widths_after = np.array([number(p.width, values) for p in after.sections])
                depths_before = np.array([number(p.depth, self.values) for p in before.sections])
                depths_after = np.array([number(p.depth, values) for p in after.sections])
                moved[:, 0] *= (widths_after/widths_before)[nearest]
                moved[:, 1] *= (depths_after/depths_before)[nearest]
            if before.outline and len(before.outline) == len(after.outline):
                original,nearest=self.bindings[index,'outline']
                changed = np.array([[number(v, values) for v in p] for p in after.outline])
                changed=curve_samples(changed,after.curve_mode,closed=True)
                moved[:, :2] += changed[nearest]-original[nearest]
            if before.surface_grid and after.surface_grid:
                original = np.array([[number(v, self.values) for v in row] for row in before.surface_grid.depths])
                changed = np.array([[number(v, values) for v in row] for row in after.surface_grid.depths])
                ny, nx = original.shape
                spline = RectBivariateSpline(np.linspace(-size[1]/2, size[1]/2, ny),
                    np.linspace(-size[0]/2, size[0]/2, nx), changed-original, kx=min(3, ny-1), ky=min(3, nx-1))
                # Blend the dish displacement down through the stock thickness;
                # the lower panel remains anchored in this trial approximation.
                blend = np.clip((local[:, 2]+size[2]/2)/size[2], 0, 1)
                moved[:, 2] -= spline.ev(local[:, 1], local[:, 0])*blend
            if before.surface_nurbs and after.surface_nurbs:
                original,uv=self.nurbs_bindings[index]
                controls,weights=resolved_surface(after.surface_nurbs,values)
                changed=rational_surface_points(controls,weights,uv)
                moved=local+changed-original+(local-original)*(new_size[2]/size[2]-1)
            points[selected] = moved @ rotation.T + center
            normal = (local_normals / ratio) @ rotation.T
            normals[selected] = normal / np.maximum(np.linalg.norm(normal, axis=1)[:, None], 1e-12)
        return points, normals


