"""Absolute-mm OCCT triangulation, read without CadQuery's relative remesher."""


class CADMeshError(ValueError):
    pass


class CADTriangleBudgetError(CADMeshError):
    pass


def mesh_absolute(shape,tolerance,angular=.1,clear=False):
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    if clear:BRepTools.Clean_s(shape.wrapped)
    mesher=BRepMesh_IncrementalMesh(shape.wrapped,tolerance,False,angular,False)
    if not mesher.IsDone():raise CADMeshError('Absolute-unit CAD tessellation failed')
    recorded=[]
    for face in shape.Faces():
        poly=BRep_Tool.Triangulation_s(face.wrapped,TopLoc_Location())
        if poly is None:raise CADMeshError('A closed CAD face is missing its preview mesh')
        recorded.append(poly.Deflection())
    return max(recorded,default=0.)


def absolute_triangles(shape,tolerance,angular=.12,max_triangles=500000,report=None):
    """Read the mesh directly: Shape.tessellate can replace it with a relative mesh."""
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopAbs import TopAbs_Orientation
    recorded=mesh_absolute(shape,tolerance,angular,clear=True)
    faces=[];count=0
    for face in shape.Faces():
        location=TopLoc_Location()
        poly=BRep_Tool.Triangulation_s(face.wrapped,location)
        if poly is None or not poly.NbTriangles():raise CADMeshError('A CAD face has no preview triangles')
        count+=poly.NbTriangles();faces.append((face,poly,location.Transformation()))
    if not count:raise CADMeshError('The CAD preview has no triangles')
    if count>max_triangles:raise CADTriangleBudgetError(f'CAD preview triangle budget exceeded ({count} > {max_triangles})')
    if report is not None:report.update(requested_tolerance_mm=tolerance,recorded_deflection_mm=recorded,
        angular_tolerance_rad=angular,triangle_count=count,mesh_mode='ABSOLUTE')
    vertices=[];triangles=[]
    for face,poly,transform in faces:
        offset=len(vertices)
        for index in range(1,poly.NbNodes()+1):
            point=poly.Node(index).Transformed(transform)
            vertices.append((point.X(),point.Y(),point.Z()))
        reverse=face.wrapped.Orientation()==TopAbs_Orientation.TopAbs_REVERSED
        for triangle in poly.Triangles():
            order=(1,3,2) if reverse else (1,2,3)
            triangles.append(tuple(triangle.Value(index)+offset-1 for index in order))
    return vertices,triangles


def absolute_mesh_bounds(shape,tolerance,angular=.08,max_triangles=2_000_000):
    """Measure mesh vertices face by face without constructing triangle arrays."""
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    recorded=mesh_absolute(shape,tolerance,angular,clear=True)
    faces=[];count=0
    for face in shape.Faces():
        location=TopLoc_Location();poly=BRep_Tool.Triangulation_s(face.wrapped,location)
        if poly is None or not poly.NbNodes():raise CADMeshError('A CAD face has no envelope mesh')
        count+=poly.NbTriangles();faces.append((poly,location.Transformation()))
    if not count:raise CADMeshError('The CAD envelope has no triangles')
    if count>max_triangles:raise CADTriangleBudgetError('Engineering envelope tessellation budget exceeded')
    minimum=np.full(3,np.inf);maximum=np.full(3,-np.inf)
    for poly,transform in faces:
        points=[]
        for index in range(1,poly.NbNodes()+1):
            point=poly.Node(index).Transformed(transform);points.append((point.X(),point.Y(),point.Z()))
        coordinates=np.asarray(points,dtype=float)
        minimum=np.minimum(minimum,coordinates.min(0));maximum=np.maximum(maximum,coordinates.max(0))
    return minimum,maximum,dict(recorded_deflection_mm=recorded,triangle_count=count)


def simplify_triangles(points,triangles,budget,error_mm,report=None):
    """Bounded CPU display-mesh reduction; never edits the source CAD solid."""
    import numpy as np
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray,vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkQuadricDecimation,vtkCleanPolyData,vtkPolyDataNormals,vtkImplicitPolyDataDistance
    from vtkmodules.util.numpy_support import numpy_to_vtk,numpy_to_vtkIdTypeArray,vtk_to_numpy
    points=np.asarray(points,dtype=np.float64);triangles=np.asarray(triangles,dtype=np.int64)
    if len(triangles)<=budget:return points,triangles
    vertices=vtkPoints();vertices.SetData(numpy_to_vtk(points,deep=True))
    faces=vtkCellArray();faces.SetData(
        numpy_to_vtkIdTypeArray(np.arange(0,3*(len(triangles)+1),3,dtype=np.int64),deep=True),
        numpy_to_vtkIdTypeArray(triangles.ravel(),deep=True))
    poly=vtkPolyData();poly.SetPoints(vertices);poly.SetPolys(faces)
    # OCCT repeats vertices across CAD faces. Weld coincident display vertices
    # before reduction so every CAD face is not an open mesh island.
    clean=vtkCleanPolyData();clean.SetInputData(poly);clean.PointMergingOn()
    clean.SetToleranceIsAbsolute(True);clean.SetAbsoluteTolerance(1e-7)
    clean.ConvertPolysToLinesOff();clean.ConvertLinesToPointsOff()
    decimate=vtkQuadricDecimation();decimate.SetInputConnection(clean.GetOutputPort())
    decimate.SetTargetReduction(1-budget/len(triangles))
    decimate.VolumePreservationOn();decimate.Update()
    sharp=vtkPolyDataNormals();sharp.SetInputConnection(decimate.GetOutputPort())
    sharp.SplittingOn();sharp.SetFeatureAngle(45);sharp.ConsistencyOn();sharp.Update()
    result=sharp.GetOutput()
    if result.GetPoints() is None or not result.GetNumberOfPolys():raise CADMeshError('Display mesh reduction returned no surface')
    reduced_points=vtk_to_numpy(result.GetPoints().GetData()).copy()
    reduced_faces=vtk_to_numpy(result.GetPolys().GetConnectivityArray()).reshape(-1,3).copy()
    if not len(reduced_faces) or len(reduced_faces)>budget:
        raise CADTriangleBudgetError(f'CAD display mesh cannot meet its triangle budget ({len(reduced_faces)} > {budget})')
    forward=vtkImplicitPolyDataDistance();forward.SetInput(result)
    reverse=vtkImplicitPolyDataDistance();reverse.SetInput(clean.GetOutput())
    sample=lambda values:values[np.linspace(0,len(values)-1,min(256,len(values)),dtype=int)]
    deviation=max([abs(forward.EvaluateFunction(point)) for point in sample(points)]+
        [abs(reverse.EvaluateFunction(point)) for point in sample(reduced_points)])
    if not np.isfinite(deviation) or deviation>error_mm:
        raise CADMeshError('The simplified display mesh exceeded its sampled surface-deviation allowance')
    if report is not None:report.update(decimation_method='VTK_QUADRIC_VOLUME_PRESERVING',
        sampled_surface_deviation_mm=deviation,sampled_deviation_allowance_mm=error_mm,
        deviation_sample_count=min(256,len(points))+min(256,len(reduced_points)))
    return reduced_points,reduced_faces
