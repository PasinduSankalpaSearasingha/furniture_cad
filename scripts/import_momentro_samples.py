"""Import the user's paired reference images and drawings; never execute archive files.

Run with the development environment (pypdf): python -m scripts.import_momentro_samples Momentro.zip
The dimensions below were transcribed from the paired shop drawings, not inferred
from photographs or reference-product marketing dimensions.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageOps
from pypdf import PdfReader
from app.ai.images import normalized_png, ImageInputError
from app.sample_dimensions import ROOT


SAMPLES = [
    ('bed', 'Bed', 'Bed', (2500, 2010, 1200), 2,
     ['Drawing length 2010 mm is mapped to overall depth. Bed height: 590 mm; overall headboard height: 1200 mm.']),
    ('bench', 'Bench', 'Bench', (1350, 430, 450), 1,
     ['Dimensions come from the Fiske shop drawing, rather than the reference presentation PDF.']),
    ('shoe-cabinet', 'Shoe cabinet', 'Shoe Cabinet', (2100, 545, 858), 1,
     ['Drawing lists heights 840/858 mm. Default: 858 mm including the 18 mm top; enter 840 mm for the carcass without the client top.']),
    ('sideboard', 'Sideboard', 'Sideboard', (3000, 500, 700), 1,
     ['Uses the overall dimension line in the shop drawing.']),
    ('sofa', 'Sofa', 'Sofa', (3000, 1450, 880), 1,
     ['Shop drawing seat heights: 440/380 mm. The reference image includes other product sizes; the paired shop drawing supplies the target size.']),
    ('table-a', 'Dining table A', 'Table/Table A', (2800, 1100, 750), 1, []),
    ('table-b', 'Dining table B', 'Table/Table B', (2200, 950, 750), 1,
     ['Overall dimensions include the client stone top. The drawing title gives BASE dimensions 2100 x 850 x 732 mm; these must not replace the overall dimensions.']),
]


def _import_samples(archive_path, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    samples = []
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        for sample_id, label, folder, dimensions, page, notes in SAMPLES:
            prefix = f'Momentro/{folder}/'
            files = [name for name in names if name.startswith(prefix) and not name.endswith('/')]
            drawing_name = next(name for name in files if Path(name).suffix.lower() == '.pdf'
                                and 'drawing' in Path(name).name.lower())
            drawing_bytes = archive.read(drawing_name)
            drawing_file = f'{sample_id}.pdf'
            (destination / drawing_file).write_bytes(drawing_bytes)
            sample = dict(id=sample_id, label=label, dimensions=dict(zip(('width_mm', 'depth_mm', 'height_mm'), dimensions)),
                drawing=dict(filename=Path(drawing_name).name, file=drawing_file, page=page,
                             sha256=hashlib.sha256(drawing_bytes).hexdigest()), notes=notes, references=[])

            def reference(content, source_name, crop=None):
                try: encoded, _, _ = normalized_png(content, 20 * 1024 * 1024)
                except ImageInputError: encoded = None
                with Image.open(BytesIO(content)) as source:
                    if not crop: source.draft('RGB', (2400, 2400))
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    if crop: image = image.crop(crop)
                    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    image.save(output, format='JPEG', quality=92)
                data = output.getvalue()
                normalized, _, _ = normalized_png(data, 20 * 1024 * 1024)
                filename = f'{sample_id}-{len(sample["references"]) + 1}.jpg'
                (destination / filename).write_bytes(data)
                sample['references'].append(dict(file=filename, original_filename=source_name,
                    sha256=hashlib.sha256(data).hexdigest(), normalized_sha256=sorted({
                        hashlib.sha256(normalized).hexdigest(), *([] if crop or encoded is None else [hashlib.sha256(encoded).hexdigest()])})))

            for name in files:
                if Path(name).suffix.lower() in ('.jpg', '.jpeg', '.png'):
                    reference(archive.read(name), Path(name).name)
            if sample_id == 'bench':
                reference_pdf = next(name for name in files if 'TNS_Bench' in name)
                picture = list(PdfReader(BytesIO(archive.read(reference_pdf))).pages[1].images)[0]
                reference(picture.data, Path(reference_pdf).name + ' page 2')
                # Include both individual photographs as well as the full page.
                for crop in ((98, 426, 1053, 1096), (1095, 426, 2050, 1096)):
                    reference(picture.data, Path(reference_pdf).name + ' page 2 photo', crop)
            if sample_id == 'shoe-cabinet':
                sample['variants'] = [dict(label='With client top', height_mm=858), dict(label='Without top', height_mm=840)]
            samples.append(sample)
    (destination / 'catalogue.json').write_text(json.dumps(dict(version=1, source='Momentro.zip', samples=samples), indent=2), encoding='utf-8')
    print(f'Imported {len(samples)} paired samples into {destination}')


def import_samples(archive_path, destination=ROOT):
    # The supplied Atlante originals are about 50 MP. This offline importer
    # reduces those known JPEGs; restore the normal raster guard afterwards.
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 64_000_000
        _import_samples(archive_path, destination)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    import_samples(parser.parse_args().archive)
