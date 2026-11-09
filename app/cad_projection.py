"""Camera projections and visibility from native OpenCascade B-Rep, never triangles."""
import cv2
import numpy as np
import cadquery as cq
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Lin
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from app.photo_schema import number


def project_points(points, camera):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    rotation, _ = cv2.Rodrigues(np.asarray(camera.rotation_vector, dtype=float))
    xyz = points @ rotation.T + camera.translation_mm
    if camera.projection == 'perspective':
        if np.any(xyz[:, 2] <= 1e-6):
            raise ValueError('A measured CAD feature is behind the perspective camera')
        pixels, _ = cv2.projectPoints(points, np.array(camera.rotation_vector, dtype=float),
            np.array(camera.translation_mm, dtype=float), np.array(camera.intrinsic_matrix, dtype=float),
            np.array(camera.distortion, dtype=float) if camera.distortion else None)
        return pixels.reshape(-1, 2)
    k = np.asarray(camera.intrinsic_matrix)
    return xyz[:, :2] * [k[0, 0], k[1, 1]] + [k[0, 2], k[1, 2]]


def local_to_world(points, part, values):
    result = np.asarray(points, dtype=float).reshape(-1, 3)
    for axis, angle in zip(np.eye(3), part.rotation_deg):
        r, _ = cv2.Rodrigues(axis * np.deg2rad(number(angle, values)))
        result = result @ r.T
    return result + [number(x, values) for x in part.position]


def landmark_point(mark, plan, build):
    part = next(p for p in plan.parts if p.part_id == mark.part_id)
    values = {v.key: v.value for v in plan.values}
    if mark.anchor == 'size_corner':
        return local_to_world([[number(d, values) * fraction for d, fraction in zip(part.size, mark.local_point)]], part, values)[0]
    if mark.anchor == 'section_center':
        point = [number(x, values) for x in part.sections[mark.point_index].center]
        return local_to_world([point], part, values)[0]
    if mark.anchor == 'local_point':
        return local_to_world([mark.local_point], part, values)[0]
    if mark.anchor in ('path_point', 'outline_point'):
        point = (part.path if mark.anchor == 'path_point' else part.outline)[mark.point_index]
        xyz = [number(x, values) for x in point]
        return local_to_world([xyz if len(xyz) == 3 else [*xyz, 0]], part, values)[0]
    shape = build.shapes[mark.part_id].val()
    # Mass centers and exact native bounds do not depend on a preview mesh.
    point = list(shape.Center().toTuple())
    if mark.anchor != 'center':
        from app.geometry import exact_bounds
        box = exact_bounds(shape)
        point[2] = box.zmax if mark.anchor == 'top' else box.zmin
    return np.asarray(point)


def curve_points(observation, plan, build, samples=48):
    part = next(p for p in plan.parts if p.part_id == observation.part_id)
    if observation.source == 'edge':
        edge = build.shapes[part.part_id].val().Edges()[observation.edge_index]
        return np.array([p.toTuple() for p in edge.positions(np.linspace(0, 1, samples))])
    from app.photo_cad import curve
    values = {v.key: v.value for v in plan.values}
    source = part.path if observation.source == 'path' else part.outline
    points = [[number(x, values) for x in point] for point in source]
    if observation.source == 'outline':
        points = [[*point, 0] for point in points]
    wire = curve(points, part.curve_mode, closed=observation.source == 'outline')
    result = [p.toTuple() for edge in wire.Edges()
              for p in edge.positions(np.linspace(0, 1, max(8, samples // len(wire.Edges()))))]
    return local_to_world(result, part, values)


class NativeProjection:
    def __init__(self, build, camera):
        self.camera = camera
        self.shape = cq.Compound.makeCompound([s.val() for s in build.shapes.values()])
        self.rotation, _ = cv2.Rodrigues(np.asarray(camera.rotation_vector, dtype=float))
        self.eye = -self.rotation.T @ np.array(camera.translation_mm)
        self.rays = IntCurvesFace_ShapeIntersector()
        self.rays.Load(self.shape.wrapped, 1e-6)

    def ray(self, pixel):
        camera = self.camera
        k = np.asarray(camera.intrinsic_matrix, dtype=float)
        if camera.projection == 'perspective':
            xy = cv2.undistortPoints(np.asarray(pixel, dtype=float).reshape(1, 1, 2), k,
                np.array(camera.distortion, dtype=float) if camera.distortion else None)[0, 0]
            return self.eye, self.rotation.T @ np.array([*xy, 1.])
        xy = (np.asarray(pixel) - [k[0, 2], k[1, 2]]) / [k[0, 0], k[1, 1]]
        origin = self.rotation.T @ (np.array([*xy, -100000.]) - camera.translation_mm)
        return origin, self.rotation.T @ np.array([0., 0., 1.])

    def hit_distance(self, pixel):
        origin, direction = self.ray(pixel)
        self.rays.Perform(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 0, 1000000.)
        if not self.rays.IsDone():
            raise ValueError('Native CAD visibility intersection failed')
        return min((self.rays.WParameter(i) for i in range(1, self.rays.NbPnt() + 1)), default=np.inf)

    def visible(self, points):
        pixels = project_points(points, self.camera)
        result = []
        for point, pixel in zip(points, pixels):
            origin, _ = self.ray(pixel)
            result.append(np.linalg.norm(point - origin) <= self.hit_distance(pixel) + .05)
        return np.asarray(result)

    def silhouette(self, check_budget=lambda: None, max_samples=512):
        """HLR visible native curves, classified as boundaries by exact ray hits.

        HLR's sharp visible edges include internal seams. Offset native rays
        distinguish outside boundaries and holes from those internal edges.
        Curve sampling is a pixel comparison discretization, not a CAD mesh.
        """
        camera = self.camera
        right = self.rotation.T[:, 0]
        toward_eye = -self.rotation.T[:, 2]
        origin = self.eye + self.rotation.T[:, 2] if camera.projection == 'perspective' else self.eye
        axes = gp_Ax2(gp_Pnt(*origin), gp_Dir(*toward_eye), gp_Dir(*right))
        algo = HLRBRep_Algo()
        algo.Add(self.shape.wrapped)
        algo.Projector(HLRAlgo_Projector(axes, 1.) if camera.projection == 'perspective' else HLRAlgo_Projector(axes))
        algo.Update()
        check_budget()
        algo.Hide()
        check_budget()
        output = HLRBRep_HLRToShape(algo)
        edges = []
        for native in (output.VCompound(), output.OutLineVCompound()):
            if not native.IsNull():
                edges.extend(cq.Shape.cast(native).Edges())
        if not edges or len(edges) > 2000:
            raise ValueError('Native HLR edge inventory is empty or exceeds its budget')
        # Allocate by projected length, so tessellation topology cannot bias scores.
        lengths = np.array([max(edge.Length(), 1e-6) for edge in edges])
        pixels = []
        for edge, length in zip(edges, lengths):
            check_budget()
            count = max(3, min(100, int(max_samples * length / lengths.sum())))
            points = np.array([p.toTuple()[:2] for p in edge.positions(np.linspace(0, 1, count))])
            if camera.projection == 'perspective':
                points = project_points_hlr(points, camera)
            else:
                k = camera.intrinsic_matrix
                points = points * [k[0][0], -k[1][1]] + [k[0][2], k[1][2]]
            tangents = np.gradient(points, axis=0)
            normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            for point, normal in zip(points, normals):
                check_budget()
                if np.isfinite(self.hit_distance(point + normal * .75)) != np.isfinite(self.hit_distance(point - normal * .75)):
                    pixels.append(point)
        if len(pixels) < 3:
            raise ValueError('Native CAD silhouette could not be resolved')
        return np.asarray(pixels)


def project_points_hlr(points, camera):
    # HLR focus=1 projects to x/z and -y/z in the native projection plane.
    pixels, _ = cv2.projectPoints(np.column_stack([points[:, 0], -points[:, 1], np.ones(len(points))]),
        np.zeros(3), np.zeros(3), np.array(camera.intrinsic_matrix, dtype=float),
        np.array(camera.distortion, dtype=float) if camera.distortion else None)
    return pixels.reshape(-1, 2)
