"""Offline native-drawing check using an existing synthetic review package."""
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.techdraw import enhance_drawings

if __name__=='__main__':
    source=Path(sys.argv[1]).resolve()
    root=Path(__file__).resolve().parents[2]/'outputs'
    if not source.is_relative_to(root):raise ValueError('Evaluation must use a package under outputs')
    # Saved packages are immutable. Evaluate only on a fresh input snapshot.
    output=root/'cad-export-evaluation'/('techdraw-check-'+uuid4().hex)
    output.mkdir(parents=True,exist_ok=False)
    for filename in ('specification.json','parts_registry.json','geometry_verification.json','all_dimensions_REVIEW.json'):
        shutil.copyfile(source/filename,output/filename)
    for part in json.loads((output/'parts_registry.json').read_text()):
        filename=f'part_{part["part_id"]}_REVIEW.step'
        if not __import__('re').fullmatch(r'part_F\d{3}_REVIEW.step',filename):raise ValueError('Invalid component name')
        shutil.copyfile(source/filename,output/filename)
    spec=SimpleNamespace(**json.loads((output/'specification.json').read_text()))
    parts=json.loads((output/'parts_registry.json').read_text())
    proof=json.loads((output/'geometry_verification.json').read_text())
    dimensions=json.loads((output/'all_dimensions_REVIEW.json').read_text())['overall_dimensions_mm']
    config=SimpleNamespace(techdraw_docker_image='fiske-techdraw:0.20.2',techdraw_timeout_seconds=180)
    result=enhance_drawings(config,spec,parts,proof['parts'],dimensions,output)
    print(json.dumps(dict(output=str(output),**result),indent=2))
    sys.exit(0 if result['techdraw_status']=='COMPLETED' else 1)
