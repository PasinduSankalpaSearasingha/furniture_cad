"""Run ONLY in the project's .venv, never inside FreeCAD's OCCT process."""
import json
from pathlib import Path
import sys
from .photo_schema import PhotoDesign
from .mesh_to_cad import build_native


def regenerate(request, output):
    import cadquery as cq
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    plan=PhotoDesign.model_validate(request['design'])
    for value in plan.values:
        if value.key in request.get('values',{}):value.value=request['values'][value.key]
    for part in plan.parts:
        if part.part_id in request.get('constructions',{}):
            replacement=type(part).model_validate_json(request['constructions'][part.part_id])
            if replacement.part_id!=part.part_id:raise ValueError('Part IDs cannot change during regeneration')
            plan.parts[plan.parts.index(part)]=replacement
    plan=PhotoDesign.model_validate(plan.model_dump())
    build=build_native(plan)
    for pid,shape in build.shapes.items():
        cq.exporters.export(shape,str(output/(pid+'.brep')))
        cq.exporters.export(build.local_shapes[pid],str(output/(pid+'.local.brep')))
    build.assembly.save(str(output/'assembly.step'))
    (output/'feature_specification.json').write_text(plan.model_dump_json(indent=2),encoding='utf-8')
    return build


if __name__=='__main__':
    regenerate(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')),sys.argv[2])
