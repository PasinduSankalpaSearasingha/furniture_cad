"""GLB preview from actual mm solids, meshed before conversion to metres.

Meshing scaled OCCT spline surfaces can amplify their stored tolerances and take
minutes. Tessellating in the source unit first avoids that and preserves STEP.
"""
import json
import struct
import math


def export_component_stl(build,path,tolerance_mm=.1):
    """Binary STL with each part's real mesh retained as a component shell."""
    count=0
    with path.open('wb') as stream:
        stream.write(b'Forma REVIEW component shells; millimetres; see mesh_export_review.json'.ljust(80,b' '))
        stream.write(struct.pack('<I',0))
        for shape in build.shapes.values():
            from app.cad_meshing import absolute_triangles
            vertices,triangles=absolute_triangles(shape.val(),tolerance_mm,.1,max_triangles=5_000_000-count)
            count+=len(triangles)
            if count>5_000_000: raise ValueError('STL mesh exceeds the supported review budget')
            for triangle in triangles:
                points=[vertices[index] for index in triangle]
                u=[points[1][i]-points[0][i] for i in range(3)]
                v=[points[2][i]-points[0][i] for i in range(3)]
                normal=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
                length=math.sqrt(sum(c*c for c in normal)) or 1
                stream.write(struct.pack('<12fH',*(c/length for c in normal),
                    *(c for point in points for c in point),0))
        if count==0: raise ValueError('CAD shapes did not produce STL triangles')
        stream.seek(80);stream.write(struct.pack('<I',count))


def remove_degenerate_stl_facets(path):
    """Drop only zero-area facets after binary STL float32 quantization.

    OCCT can emit a repeated-vertex triangle at a spherical pole. Removing it
    adds no geometry and avoids a zero-length mesh edge on sculpted seats.
    """
    data=path.read_bytes()
    count=struct.unpack_from('<I',data,80)[0]
    if len(data)!=84+50*count: raise ValueError('Expected a binary STL mesh')
    kept=[]
    for offset in range(84,len(data),50):
        values=struct.unpack_from('<12f',data,offset)
        a,b,c=values[3:6],values[6:9],values[9:12]
        u,v=[b[i]-a[i] for i in range(3)],[c[i]-a[i] for i in range(3)]
        cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        if any(cross): kept.append(data[offset:offset+50])
    removed=count-len(kept)
    if removed: path.write_bytes(data[:80]+struct.pack('<I',len(kept))+b''.join(kept))
    return removed


def export_glb(build,path,tolerance_mm):
    from app.ai.geometry_preview import tessellation
    binary=bytearray()
    document=dict(asset=dict(version='2.0',generator='Forma CAD-derived review preview'),scene=0,
        scenes=[dict(nodes=[0])],nodes=[dict(name='Furniture_REVIEW',children=[])],meshes=[],materials=[],
        buffers=[],bufferViews=[],accessors=[],extras=dict(source_units='mm',units='metres',
            tessellation_tolerance_mm=tolerance_mm,approved_for_manufacturing=False))
    for part in build.registry:
        details={}
        vertices,triangles,normals=tessellation(build.shapes[part.part_id].val(),tolerance_mm,report=details)
        if len(triangles)==0 or len(triangles)>500000: raise ValueError('Preview mesh exceeds the supported triangle budget')
        # Right-handed conversion: X right, Y up, Z toward the viewer.
        points=[(v[0]*.001,v[2]*.001,-v[1]*.001) for v in vertices]
        packed=[points[index] for triangle in triangles for index in triangle]
        offset=len(binary)
        binary.extend(struct.pack('<'+'f'*(3*len(packed)),*(c for point in packed for c in point)))
        view=len(document['bufferViews']);accessor=len(document['accessors'])
        document['bufferViews'].append(dict(buffer=0,byteOffset=offset,byteLength=len(binary)-offset,target=34962))
        document['accessors'].append(dict(bufferView=view,componentType=5126,count=len(packed),type='VEC3',
            min=[min(v[i] for v in points) for i in range(3)],max=[max(v[i] for v in points) for i in range(3)]))
        normal_offset=len(binary)
        packed_normals=[(normals[index][0],normals[index][2],-normals[index][1]) for triangle in triangles for index in triangle]
        binary.extend(struct.pack('<'+'f'*(3*len(packed_normals)),*(c for point in packed_normals for c in point)))
        normal_view=len(document['bufferViews']);normal_accessor=len(document['accessors'])
        document['bufferViews'].append(dict(buffer=0,byteOffset=normal_offset,byteLength=len(binary)-normal_offset,target=34962))
        document['accessors'].append(dict(bufferView=normal_view,componentType=5126,count=len(packed_normals),type='VEC3'))
        rgba=build.assembly.objects[part.part_id].color.toTuple()
        material=len(document['materials'])
        document['materials'].append(dict(name=part.material.value,pbrMetallicRoughness=dict(
            baseColorFactor=list(rgba),metallicFactor=0,roughnessFactor=.75),doubleSided=True))
        mesh=len(document['meshes']);node=len(document['nodes'])
        document['meshes'].append(dict(name=part.part_id,extras=dict(cad_mesh=details),primitives=[dict(attributes=dict(POSITION=accessor,NORMAL=normal_accessor),material=material,mode=4)]))
        document['nodes'].append(dict(name=part.part_id,mesh=mesh))
        document['nodes'][0]['children'].append(node)
    document['buffers']=[dict(byteLength=len(binary))]
    encoded=json.dumps(document,separators=(',',':')).encode('utf-8')
    encoded+=b' '*((-len(encoded))%4)
    binary.extend(b'\0'*((-len(binary))%4))
    header=struct.pack('<III',0x46546c67,2,12+8+len(encoded)+8+len(binary))
    path.write_bytes(header+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+
        struct.pack('<II',len(binary),0x004e4942)+binary)
