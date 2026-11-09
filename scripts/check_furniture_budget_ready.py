"""Check furniture spending configuration and token counting; never generate AI output."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openai import OpenAI,APIError
from app.config import Settings
from app.photo_matching import reconstruction_models
from app.ai.furniture_budget import policy
from app.ai.openai_client import provider_diagnostics


def main():
    config=Settings();models=reconstruction_models(config);prices=policy(config)['rates']
    selected=sorted({models[role] for role in ('vision','design','review')})
    if not all(model in prices for model in selected):raise SystemExit('A selected furniture model needs verified pricing')
    client=OpenAI(max_retries=0,timeout=25)
    counts={}
    try:
        for model in selected:
            counts[model]=client.responses.input_tokens.count(model=model,input='Furniture budget readiness check.').input_tokens
    except APIError as exc:
        details=provider_diagnostics(exc) if hasattr(exc,'status_code') else {}
        print(json.dumps(dict(ready=False,error_type=type(exc).__name__,provider=details,generation_requests=0)))
        raise SystemExit(1)
    finally:client.close()
    print(json.dumps(dict(ready=True,budget_usd=config.furniture_budget_usd,models=models,
        input_token_counts=counts,generation_requests=0),indent=2))


if __name__=='__main__':main()
