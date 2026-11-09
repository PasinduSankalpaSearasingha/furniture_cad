"""Create a source deliverable without credentials, databases, caches or environments."""
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('app','blender','frontend','tests','examples','docs','migrations','scripts','.github','third_party')
FILES = ('README.md','requirements.txt','requirements-browser.txt','pyproject.toml','uv.lock','alembic.ini','Dockerfile',
         'compose.yaml','.dockerignore','.gitignore','.env.example')


def main():
    paths = [ROOT/name for name in FILES]
    for name in DIRECTORIES:
        paths.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and not
                     {'__pycache__','.pytest_cache','node_modules'} & set(p.relative_to(ROOT).parts) and p.suffix != '.pyc')
    if any(p.is_symlink() or not p.resolve().is_relative_to(ROOT) for p in paths):
        raise SystemExit('Refusing symlink or external source file')
    output = ROOT/'dist'
    output.mkdir(exist_ok=True)
    target = output/'furniture_manufacturing_upgrade.zip'
    with ZipFile(target,'w',ZIP_DEFLATED) as archive:
        for path in sorted(paths): archive.write(path,arcname=path.relative_to(ROOT).as_posix())
    with ZipFile(target) as archive:
        if archive.testzip(): raise SystemExit('Source archive CRC validation failed')
        credentials={value for name,value in os.environ.items() if name.endswith('_API_KEY')}
        environment=ROOT/'.env'
        if environment.is_file():
            for line in environment.read_text(encoding='utf-8-sig').splitlines():
                name,separator,value=line.partition('=')
                if separator and name.strip().endswith('_API_KEY'):
                    credentials.add(value.strip().strip('\"\''))
        secrets=[value.encode() for value in credentials if len(value)>12]
        for member in archive.infolist():
            if Path(member.filename).name=='.env':raise SystemExit('Private environment file found in source archive')
            content=archive.read(member.filename)
            if any(secret in content for secret in secrets):raise SystemExit('Configured API credential found in source archive')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksums = [f'{digest}  {target.name}']
    bundles = [dict(archive=str(target),files=len(paths),bytes=target.stat().st_size,sha256=digest)]
    demo = ROOT/'examples/generated/collaborative_cabinet_demo'
    if demo.exists():
        demo_files = [p for p in demo.rglob('*') if p.is_file()]
        if any(p.is_symlink() or not p.resolve().is_relative_to(demo.resolve()) for p in demo_files):
            raise SystemExit('Refusing unsafe demonstration artifact')
        demo_zip = output/'collaborative_cabinet_demo.zip'
        with ZipFile(demo_zip,'w',ZIP_DEFLATED) as archive:
            for path in sorted(demo_files): archive.write(path,arcname=path.relative_to(demo).as_posix())
        with ZipFile(demo_zip) as archive:
            if archive.testzip(): raise SystemExit('Demo archive CRC validation failed')
        demo_digest = hashlib.sha256(demo_zip.read_bytes()).hexdigest()
        checksums.append(f'{demo_digest}  {demo_zip.name}')
        bundles.append(dict(archive=str(demo_zip),files=len(demo_files),bytes=demo_zip.stat().st_size,sha256=demo_digest))
    (output/'SHA256SUMS.txt').write_text('\n'.join(checksums)+'\n',encoding='utf-8')
    print(json.dumps(bundles,indent=2))


if __name__ == '__main__': main()
