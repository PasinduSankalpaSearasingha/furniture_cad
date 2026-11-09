"""Read pinned upstream prompt constants as data, without importing its Python."""
import ast
import json
from functools import lru_cache
from pathlib import Path
from app.pipeline import file_hash

ROOT=Path(__file__).resolve().parents[1]/'third_party'/'cadquery_agent'


@lru_cache(maxsize=1)
def provenance():
    return json.loads((ROOT/'provenance.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=16)
def prompt(agent,constant='SYSTEM'):
    relative=f'cq_agent_v4/agents/{agent}.py';path=ROOT/'upstream'/relative
    if file_hash(path)!=provenance()['files'][relative]:raise ValueError('Upstream prompt integrity failed')
    tree=ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==constant for t in node.targets):
            text=ast.literal_eval(node.value)
            if not isinstance(text,str):raise ValueError('Invalid upstream prompt constant')
            # Upstream JSON examples/free-form responses conflict with our strict
            # contracts; retain the agent's task context, then apply local rules.
            text=text.split('RESPOND WITH')[0]
            return text.replace('Never say "unknown" — always give a best estimate with confidence level',
                'Mark missing physical evidence as unknown; any proposed size is an unverified estimate.')
    raise ValueError('Upstream agent prompt not found')


RULES='''\nAPPLICATION CONTRACT (takes precedence over the reference workflow):
Use only the provided structured schema and registered deterministic tool. Never output or execute Python.
The photograph describes appearance; explicit user measurements override inferred proportions.
Unsupplied measurements, invisible geometry, joinery and stock are unverified estimates.
Do not claim exact photo reconstruction or manufacturing approval. Preserve distinctive curves and part counts.
Every part must be one valid closed positive-volume solid. Use separate named components, not a fused mesh.
No model can approve itself as manufactured or structurally qualified.'''
