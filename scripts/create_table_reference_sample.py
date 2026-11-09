"""Deliver the exact paired Table A reference drawing without AI generation.

Run with an isolated renderer: uv run --no-sync --with pypdfium2==4.30.0
python scripts/create_table_reference_sample.py
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pypdfium2 as pdfium
from pypdf import PdfReader


def main():
    root=Path(__file__).resolve().parents[1]
    source=root/'data/reference_samples/Momentro/Table/Table A/Fiske Staff Drawing - Dining Table-Layout1.pdf'
    reader=PdfReader(source)
    text=reader.pages[0].extract_text()
    if len(reader.pages)!=1 or not all(value in text for value in
        ('TOP VIEW','FRONT VIEW','SIDE VIEW','ISOMETRIC VIEW','2800','1100','750','2100','766')):
        raise ValueError('The paired table drawing does not match the expected reference')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output=root/'outputs'/('table-a-reference-sample-'+stamp+'-'+uuid4().hex[:6])
    output.mkdir(parents=True,exist_ok=False)
    target=output/'table_all_views_reference.pdf'
    shutil.copyfile(source,target)
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest(source)==digest(target)
    document=pdfium.PdfDocument(str(target))
    page=document[0]
    bitmap=page.render(scale=2.5)
    preview=bitmap.to_pil()
    preview.save(output/'table_all_views_reference.png')
    preview.close();bitmap.close();page.close();document.close()
    report=dict(source_type='EXISTING_PAIRED_SHOP_DRAWING',
        reference_drawing=str(source.relative_to(root)),
        reference_photo='data/reference_samples/Momentro/Table/Table A/Referance Image - 4.JPG',
        source_sha256=digest(source),delivered_pdf_sha256=digest(target),
        overall_dimensions_mm=dict(width=2800,depth=1100,height=750),top_thickness_mm=45,
        base_dimensions_from_drawing_mm=dict(length=2100,depth=766),
        views=['TOP','FRONT','SIDE','ISOMETRIC'],pages=1,
        newly_ai_generated=False,new_cad_model_created=False,paid_ai_calls=0)
    (output/'source.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),**report),indent=2))


if __name__=='__main__':main()
