"""Build an existing bounded photo plan in native background Blender, without AI calls."""
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.photo_schema import PhotoDesign
from app.reference_fit_schema import FitView
from app.agents.photo_agent import resolve_estimated_geometry
from app.rendering.modeling import build_model,context_for


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('plan',type=Path)
    parser.add_argument('image',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    raw=json.loads(args.plan.read_text(encoding='utf-8'));raw=raw.get('result',raw);raw=raw.get('specification',raw);raw=raw.get('photo_design',raw)
    plan=PhotoDesign.model_validate(raw);settings=Settings()
    plan,_,_,cad,_=resolve_estimated_geometry(plan,{},'Native Blender review')
    context=context_for(plan,cad,[(FitView(image_id=uuid4()),args.image.read_bytes())],{},settings)
    result=build_model(settings,plan,cad,args.output,context,lambda s:print(s,flush=True))
    print(json.dumps(dict(parts=result['report']['component_count'],cad_agreement=result['report']['cad_agreement'],
        views=result['report']['views']),indent=2))


if __name__=='__main__':main()
