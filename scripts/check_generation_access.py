"""Read-only model-access check. Makes no generation requests and prints no keys."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openai import OpenAI,APIStatusError,APIConnectionError,APITimeoutError
from app.config import Settings
from app.ai.openai_client import provider_diagnostics


def main():
    import os
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',action='append',help='Check a specific model without changing the configured model')
    args=parser.parse_args()
    config=Settings()
    roles=('vision','design','review')
    if not config.ai_enabled or not os.getenv('OPENAI_API_KEY') or any(not config.ai_models.get(role) for role in roles):
        print(json.dumps(dict(status='CONFIGURATION_REQUIRED',generation_requests=0)));return 1
    with OpenAI(timeout=15,max_retries=0) as client:
        for model in dict.fromkeys(args.model or [config.ai_models[role] for role in roles]):
            try:client.models.retrieve(model)
            except APIStatusError as error:
                print(json.dumps(dict(status='ACCESS_REJECTED',model=model,provider=provider_diagnostics(error),generation_requests=0)));return 1
            except (APIConnectionError,APITimeoutError):
                print(json.dumps(dict(status='CONNECTION_UNAVAILABLE',generation_requests=0)));return 2
    print(json.dumps(dict(status='MODEL_ACCESS_CONFIRMED',generation_requests=0,notes='Does not verify generation billing, quota or photo accuracy.')));return 0


if __name__=='__main__':raise SystemExit(main())
