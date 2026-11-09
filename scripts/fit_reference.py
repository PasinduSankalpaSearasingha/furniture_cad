"""Offline reference fitting for an existing declarative photo plan (zero API calls)."""
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.photo_schema import PhotoDesign
from app.reference_fit_schema import ReferenceFitOptions,FitView
from app.ai.reference_fitting import fit_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path);parser.add_argument('image',type=Path)
    parser.add_argument('output',type=Path);parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--azimuth',type=float,default=-110);parser.add_argument('--elevation',type=float,default=15)
    args=parser.parse_args();raw=json.loads(args.plan.read_text(encoding='utf-8'))
    raw=raw.get('result',raw);raw=raw.get('specification',raw);raw=raw.get('photo_design',raw)
    plan=PhotoDesign.model_validate(raw)
    fitted,report,_,files=fit_plan(plan,{},'Local reference fitting',
        [(FitView(image_id=uuid4()),args.image.read_bytes())],ReferenceFitOptions(max_seconds=args.seconds),
        {'reference_view':dict(azimuth_deg=args.azimuth,elevation_deg=args.elevation)},lambda s:print(s,flush=True))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'plan.json').write_text(fitted.model_dump_json(indent=2),encoding='utf-8')
    (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for name,pixels in files:(args.output/name).write_bytes(pixels)
    print(json.dumps({k:v for k,v in report.items() if k not in ('views',)},indent=2))


if __name__=='__main__':main()
