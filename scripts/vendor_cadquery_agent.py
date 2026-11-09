"""Fetch a pinned, reviewable upstream snapshot; never execute downloaded code."""
import hashlib
import json
from pathlib import Path
from urllib.request import Request,urlopen

REPOSITORY='naturecodec-stack/Agentic-AI-for-CadQuery'
COMMIT='702b90e0d26b630d7eba09f85d29bb446f6a1b96'
ROOT=Path(__file__).resolve().parents[1]/'third_party'/'cadquery_agent'


def get(url):
    with urlopen(Request(url,headers={'User-Agent':'Furniture-CAD-integration'}),timeout=60) as response:
        return response.read()


def main():
    tree=json.loads(get(f'https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1'))
    selected=[x['path'] for x in tree['tree'] if x['type']=='blob' and
        (x['path'] in ('README.md','LICENSE','LICENSE.md','cq_agent_v4/graph.py') or
         x['path'].startswith('cq_agent_v4/agents/') and x['path'].endswith('.py'))]
    files={}
    for name in selected:
        data=get(f'https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{name}')
        target=ROOT/'upstream'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        files[name]=hashlib.sha256(data).hexdigest()
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/'provenance.json').write_text(json.dumps(dict(repository='https://github.com/'+REPOSITORY,
        commit=COMMIT,declared_license='MIT (upstream README)',files=files,
        integration='Prompt constants extracted as data; upstream Python, subprocess executor and UI are never executed.'),indent=2),encoding='utf-8')
    print(json.dumps(dict(commit=COMMIT,files=len(files),directory=str(ROOT))))


if __name__=='__main__':main()
