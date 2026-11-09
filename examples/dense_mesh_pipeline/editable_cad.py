# Editable native CAD; run from the project root using .venv.
import json
from pathlib import Path
from app.photo_schema import PhotoDesign
from app.mesh_to_cad import build_native
plan = PhotoDesign.model_validate_json(Path(__file__).with_name('feature_specification.json').read_text(encoding='utf-8'))
build = build_native(plan)
build.assembly.save(str(Path(__file__).with_name('edited_assembly.step')))
