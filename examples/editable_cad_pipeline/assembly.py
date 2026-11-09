# Authoritative, editable native furniture CAD. Run with the project .venv.
import json, os, sys
from pathlib import Path
project=Path(os.environ.get('FURNITURE_CAD_PROJECT','D:\\Enfection\\furniture cad'))
sys.path.insert(0,str(project))
from app.photo_schema import PhotoDesign
from app.mesh_to_cad import build_native
folder=Path(__file__).resolve().parent
plan=PhotoDesign.model_validate_json((folder/'feature_specification.json').read_text(encoding='utf-8'))
parameters=json.loads((folder/'cad_parameters.json').read_text(encoding='utf-8'))
values={v['key']:v['value'] for v in parameters['values']}
for value in plan.values:
    if value.key in values:value.value=values[value.key]
plan=PhotoDesign.model_validate(plan.model_dump())
build=build_native(plan)
build.assembly.save(str(folder/'edited_assembly.step'))
