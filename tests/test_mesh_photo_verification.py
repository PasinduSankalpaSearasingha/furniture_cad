from pathlib import Path
from app.mesh_photo_verification import PhotoReference
from app.mesh_to_cad import build_native
from tests.test_engineering_cad import table_plan


def test_actual_reference_photo_is_compared_with_estimated_camera():
    photo = Path('tests/fixtures/cad_optimizer_real_photo.jpg').read_bytes()
    plan = table_plan()
    build = build_native(plan)
    reference = PhotoReference(photo, plan, build)
    score, report = reference.evaluate(plan, build)
    if reference.usable:
        assert score is not None and 0 <= score <= 1
        assert report['camera_physically_calibrated'] is False
        assert report['foreground_method'] == 'AUTOMATIC_GRABCUT'
    else:
        assert score is None and report['status'] == 'unverified'
    # A real photograph supplies visible appearance, not measured CAD truth.


def test_automatic_photo_evidence_is_explicitly_unreviewed():
    photo = Path('tests/fixtures/cad_optimizer_real_photo.jpg').read_bytes()
    plan = table_plan(); build = build_native(plan)
    reference = PhotoReference(photo, plan, build)
    if reference.usable:
        _, report = reference.evaluate(plan, build)
        assert report['mask_reviewed'] is False
