r"""Run the app's CadQuery builder on sample JSON, without AI or saved projects.

From the project root:
    .venv\Scripts\python.exe scripts/generate_cadquery_stool.py

Edit examples/cadquery_stool_input.json to change dimensions or part geometry.
Use --output with a new directory for subsequent runs; existing files are kept.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cadquery as cq
from app.engineering_cad import geometry_checks, render_preview
from app.geometry import exact_bounds
from app.models import CabinetSpec
from app.photo_cad import create_photo_furniture
from app.photo_schema import PhotoDesign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'examples/cadquery_stool_input.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/cadquery_stool_sample')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output directory already exists; choose a new --output directory.')

    design = PhotoDesign.model_validate(json.loads(args.input.read_text(encoding='utf-8')))
    values = {item.key: item.value for item in design.values}
    spec = CabinetSpec(
        project_name='CadQuery sample stool', family='photo', photo_design=design,
        dimension_provenance='unverified', carcass_material='solid_wood',
        panel_thickness_mm=values['top_thickness_mm'],
        **{key: values[key] for key in ('width_mm', 'depth_mm', 'height_mm')},
    )
    build = create_photo_furniture(spec)
    checks = geometry_checks(build)
    if not checks['assembly_connected']:
        raise ValueError('Sample parts must form one connected assembly.')

    args.output.mkdir(parents=True, exist_ok=False)
    step_path = args.output / 'stool.step'
    build.assembly.export(str(step_path))
    reopened = cq.importers.importStep(str(step_path))
    solids = reopened.solids().vals()
    if len(solids) != len(design.parts) or any(not solid.isValid() or solid.Volume() <= 0 for solid in solids):
        raise ValueError('Reopened STEP must retain one valid positive-volume solid per part.')
    bounds = exact_bounds(cq.Compound.makeCompound(solids))
    dimensions = dict(zip(('width_mm', 'depth_mm', 'height_mm'), (bounds.xlen, bounds.ylen, bounds.zlen)))
    if any(abs(dimensions[key] - values[key]) > 0.01 for key in dimensions):
        raise ValueError('Reopened STEP dimensions differ from the input by more than 0.01 mm.')

    (args.output / 'stool.png').write_bytes(render_preview(build, -110, 18))
    report = dict(
        input=str(args.input.resolve()), step=str(step_path.resolve()),
        reopened_step_solid_count=len(solids), actual_dimensions_mm=dimensions,
        geometry_checks=checks, approved_for_manufacturing=False, ai_requests=0,
    )
    (args.output / 'checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
