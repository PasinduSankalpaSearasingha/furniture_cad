import csv
import json
from zipfile import ZipFile
import cadquery as cq
import ezdxf
import pytest
import trimesh
from openpyxl import load_workbook
from pypdf import PdfReader
from app.models import CabinetSpec
from app.pipeline import build_package, verify_package, canonical_hash
from types import SimpleNamespace
from .test_cabinet import spec


@pytest.fixture(scope='module')
def package(tmp_path_factory):
    output = tmp_path_factory.mktemp('exports')/'package'
    s = spec(include_glazed_door=True,joint_type='dowel_demo')
    result = build_package(s,output)
    return s,output,result


def test_reopen_step_glb_stl_and_preserve_component_dimensions(package):
    s,out,result = package
    model = cq.importers.importStep(str(out/'cabinet.step'))
    solids = model.solids().vals()
    assert len(solids)==10
    assert all(solid.isValid() and solid.Volume()>0 for solid in solids)
    bb = model.val().BoundingBox()
    assert (bb.xlen,bb.ylen,bb.zlen)==pytest.approx((700,475,1800),abs=1e-5)
    glb = trimesh.load(out/'cabinet.glb',force='scene')
    assert all(p in glb.graph.nodes for p in ('P001','P002','P003','P004','P005','D001','D002','D003','D004','G001'))
    # CadQuery glTF is metres and Y-up, whereas STEP/registry are mm and Z-up.
    assert sorted(glb.extents)==pytest.approx(sorted([0.7,0.475,1.8]),abs=1e-5)
    stl = trimesh.load(out/'cabinet.stl')
    assert stl.is_watertight
    assert stl.extents==pytest.approx((700,475,1800),abs=1e-4)


def test_dxf_dimensions_and_pdf_pages_match_registry(package):
    s,out,result = package
    registry = json.loads((out/'parts_registry.json').read_text())
    manifest = json.loads((out/'drawing_manifest.json').read_text())
    for drawing in manifest['drawings']:
        doc = ezdxf.readfile(out/drawing['file'])
        assert doc.header['$INSUNITS']==4
        assert not doc.audit().errors
        values = sorted(d.get_measurement() for d in doc.modelspace().query('DIMENSION'))
        assert values==pytest.approx(sorted([drawing['width_mm'],drawing['height_mm']]))
    for p in registry:
        drawing = next(d for d in manifest['drawings'] if d['drawing_number']=='PT-'+p['part_id'])
        assert (drawing['width_mm'],drawing['height_mm'])==pytest.approx((p['length_mm'],p['width_mm']))
    reader = PdfReader(out/'drawings_REVIEW.pdf')
    assert len(reader.pages)==11
    assert all('REVIEW' in page.extract_text() for page in reader.pages)


def test_schedules_and_archive_integrity(package):
    s,out,result = package
    wb = load_workbook(out/'schedules_REVIEW.xlsx',read_only=True)
    assert wb['Cut list'].max_row==10
    assert wb['Glass'].max_row==2
    wb.close()
    with (out/'cut_list_REVIEW.csv').open(encoding='utf-8-sig',newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows)==9
    assert {r['part_id'] for r in rows}=={'P001','P002','P003','P004','P005','D001','D002','D003','D004'}
    job = SimpleNamespace(package_hash=result['package_hash'],manifest_hash=result['manifest_hash'])
    verify_package(out,job,canonical_hash(s.model_dump(mode='json')))
    original = (out/'specification.json').read_bytes()
    try:
        (out/'specification.json').write_text('{}')
        with pytest.raises(ValueError,match='hash mismatch'): verify_package(out,job,canonical_hash(s.model_dump(mode='json')))
    finally: (out/'specification.json').write_bytes(original)
