"""Independent, read-only audit of the actual completed live demonstration."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from zipfile import ZipFile
import cadquery as cq
from app.geometry import exact_bounds
from app.pipeline import file_hash


def main():
    root=Path('outputs/integrated-studio-demo')
    original=json.loads((root/'cad_workflow.json').read_text(encoding='utf-8'))
    edited=json.loads((root/'modification_workflow.json').read_text(encoding='utf-8'))
    evidence=[]
    for workflow in (original,edited):
        assert workflow['status']=='REVIEW_REQUIRED'
        directory=Path('outputs')/workflow['job_id']
        shape=cq.importers.importStep(str(directory/'cabinet.step'))
        solids=shape.solids().vals();bounds=exact_bounds(shape.val())
        assert len(solids)==7 and all(s.isValid() and s.Volume()>0 for s in solids)
        assert all(abs(a-b)<1e-5 for a,b in zip((bounds.xlen,bounds.ylen,bounds.zlen),(1200,600,750)))
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        for name,digest in manifest['files'].items(): assert file_hash(directory/name)==digest
        with ZipFile(directory/'design_review_package.zip') as archive: assert archive.testzip() is None
        render=workflow['result']['blender'];output=Path('outputs')/render['artifact_directory']
        for name,digest in render['files'].items(): assert file_hash(output/name)==digest
        with ZipFile(output/'blender_render_package.zip') as archive: assert archive.testzip() is None
        values={v['key']:v['value'] for v in workflow['result']['specification']['photo_design']['values']}
        evidence.append(dict(revision_id=workflow['revision_id'],job_id=workflow['job_id'],solids=len(solids),
            dimensions_mm=[bounds.xlen,bounds.ylen,bounds.zlen],tabletop_thickness_mm=values['tabletop_thickness_mm'],
            render_count=len(render['images']),engine=render['report']['engine'],device=render['report']['device'],
            cad_package_sha256=file_hash(directory/'design_review_package.zip'),approved_for_manufacturing=False))
    assert evidence[0]['tabletop_thickness_mm']==25 and evidence[1]['tabletop_thickness_mm']==30
    (root/'independent_verification.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__': main()
