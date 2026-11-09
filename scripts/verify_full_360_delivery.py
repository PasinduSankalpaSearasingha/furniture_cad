"""Verify local completion, original CAD preservation and saved-project integrity."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cadquery as cq
import httpx
from app.engineering_cad import verify_solid_step
from app.pipeline import file_hash


def read_powershell_baseline(path):
    # Windows PowerShell 5 decodes charset-less JSON as Latin-1. Restore UTF-8
    # strings in these pre-restart receipts without changing the original files.
    def restore(value):
        if isinstance(value,dict):return {key:restore(item) for key,item in value.items()}
        if isinstance(value,list):return [restore(item) for item in value]
        if isinstance(value,str):
            try:return value.encode('latin-1').decode('utf-8')
            except UnicodeError:pass
        return value
    return restore(json.loads(path.read_text(encoding='utf-8-sig')))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    args=parser.parse_args();output=Path('outputs/full-360-repair')
    with httpx.Client(base_url=args.url,timeout=180) as client:
        def read(path):
            response=client.get(path);response.raise_for_status();return response.json()
        completed=read('/api/v1/agents/workflows/'+args.workflow)
        assert completed['status']=='REVIEW_REQUIRED',completed.get('error')
        result=completed['result'];evidence=result['engineering_cad']['workflow']
        source=read('/api/v1/agents/workflows/'+evidence['completion_of'])
        baseline=read_powershell_baseline(output/'source-workflow-before.json')
        assert source==baseline,'Original workflow changed'
        protected=read_powershell_baseline(output/'protected-project-before.json')
        assert read('/api/projects/'+protected['id'])==protected,'Protected project changed'
        source_hashes=json.loads((output/'source-artifact-hashes-before.json').read_text(encoding='utf-8-sig'))
        assert all(file_hash(Path(item['Path']))==item['Hash'].lower() for item in source_hashes)
    assert completed['usage']==dict(input_tokens=0,output_tokens=0,generated_images=0)
    proof=result['engineering_cad']['cad_export_agreement']
    assert proof['geometry_checks']['full_360']['rear_closed']
    assert proof['geometry_checks']['assembly_connected'] and proof['geometry_checks']['interference_check_passed']
    assert not result['engineering_cad']['visual_review']['visual']['accepted']
    assert {'front','rear','left','right','top','bottom'}<=proof['view_files'].keys()
    before=Path('outputs')/source['job_id'];after=Path('outputs')/completed['job_id']
    old_parts=json.loads((before/'parts_registry.json').read_text(encoding='utf-8'))
    checked=[]
    for part in old_parts:
        original=cq.importers.importStep(str(before/part['files']['step'])).val()
        _,agreement=verify_solid_step(original,after/part['files']['step'])
        checked.append(dict(part_id=part['part_id'],sampled_surface_max_gap_mm=agreement['sampled_surface_max_gap_mm']))
    dimensions={key:result['specification'][key] for key in ('width_mm','depth_mm','height_mm')}
    assert all(value==source['result']['specification'][key] for key,value in dimensions.items())
    assert all(result['value_provenance'][key]==source['result']['value_provenance'][key] for key in dimensions)
    report=dict(workflow_id=completed['id'],project_id=completed['project_id'],job_id=completed['job_id'],
        source_workflow_id=source['id'],original_part_count=len(old_parts),completed_part_count=proof['part_count'],
        added_part_count=proof['part_count']-len(old_parts),dimensions_mm=dimensions,
        rear_coverage=proof['geometry_checks']['full_360']['rear_coverage'],original_parts_checked=checked,
        original_workflow_unchanged=True,original_artifact_hashes_unchanged=True,protected_project_unchanged=True,
        dimension_sources_unchanged=True,ai_usage=completed['usage'],hidden_construction_verified=False,
        approved_for_manufacturing=False,delivery_directory=result['delivery']['directory'])
    (output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
