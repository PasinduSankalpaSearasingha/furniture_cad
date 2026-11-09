"""Produce a clearly labelled CAD-derived demonstration reference; no AI or photograph claim."""
import json
from pathlib import Path
from app.ai.cad_preview import render_step_preview
from app.pipeline import file_hash


def main():
    source = Path('examples/generated/mixed_material_cabinet')
    output = Path('examples/ai_demo')
    output.mkdir(parents=True,exist_ok=True)
    path = output/'wooden_glass_cabinet_CAD_REFERENCE.png'
    path.write_bytes(render_step_preview(source))
    metadata = dict(kind='CAD_DERIVED_REVIEW_REFERENCE',source_step=str(source/'cabinet.step'),
        source_step_sha256=file_hash(source/'cabinet.step'),image_sha256=file_hash(path),
        source_revision=1,physically_measured=False,ai_generated=False,approved_for_manufacturing=False)
    (output/'reference_provenance.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(json.dumps(metadata,indent=2))


if __name__=='__main__': main()
