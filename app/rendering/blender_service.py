"""No shell commands or model-generated scripts. All Blender inputs are server-owned."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from functools import lru_cache
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image
from app.ai.openai_client import AIServiceError
from app.pipeline import file_hash, safe_directory
from .config import RenderOptions, preset


def discover_blender(configured=''):
    if configured:
        path=Path(configured).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in ('.exe','') and not path.is_symlink(): return path
        raise AIServiceError('BLENDER_NOT_FOUND','BLENDER_EXECUTABLE does not point to an installed Blender executable')
    found=shutil.which('blender')
    if found: return Path(found).resolve()
    roots=[Path(os.getenv('ProgramFiles','C:/Program Files'))/'Blender Foundation',
           Path('/Applications/Blender.app/Contents/MacOS'),Path('/usr/bin'),Path('/usr/local/bin')]
    for root in roots:
        if root.is_dir():
            for candidate in sorted([*root.glob('Blender*/blender.exe'),root/'blender'],reverse=True):
                if candidate.is_file(): return candidate.resolve()
    raise AIServiceError('BLENDER_NOT_FOUND','Install Blender or set BLENDER_EXECUTABLE in the backend .env')


@lru_cache(maxsize=4)
def blender_health(configured=''):
    try:
        exe=discover_blender(configured)
        result=subprocess.run([str(exe),'--version'],shell=False,capture_output=True,text=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        version=result.stdout.splitlines()[0] if result.stdout else ''
        if result.returncode or not version.startswith('Blender '): raise ValueError('Version check failed')
        return dict(available=True,version=version,executable=str(exe),engine='CYCLES')
    except (AIServiceError,OSError,subprocess.TimeoutExpired,ValueError) as exc:
        return dict(available=False,code=getattr(exc,'code','BLENDER_HEALTH_FAILED'),message=str(exc)[:250])


def render_geometry(settings,source,output,options,source_metadata,progress=lambda stage,percent:None):
    """Source is a hash-verified CAD package; output is a new workflow directory."""
    options=RenderOptions.model_validate(options) if isinstance(options,dict) else options
    executable=discover_blender(settings.blender_executable)
    source=Path(source).resolve();output=Path(output).resolve()
    glb=safe_directory(source,'cabinet.glb');registry=safe_directory(source,'parts_registry.json')
    if not glb.is_file() or not registry.is_file(): raise AIServiceError('CAD_OUTPUT_REQUIRED','STEP/GLB package is required before rendering')
    output.mkdir(parents=True,exist_ok=False)
    parts=json.loads(registry.read_text(encoding='utf-8'))
    hdri=''
    if options.environment=='hdri':
        path=Path(settings.blender_hdri).resolve() if settings.blender_hdri else None
        if not path or not path.is_file() or path.suffix.lower() not in ('.hdr','.exr'):
            raise AIServiceError('HDRI_REQUIRED','Set BLENDER_HDRI_PATH to a licensed local HDR or EXR file')
        hdri=str(path)
    configuration=dict(input_glb=str(glb),output=str(output),parts=parts,options=options.model_dump(),
        **preset(options,settings),hdri=hdri)
    config_path=output/'render_config.json'
    config_path.write_text(json.dumps(configuration,indent=2),encoding='utf-8')
    script=Path(__file__).resolve().parents[2]/'blender'/'render_scene.py'
    command=[str(executable),'--background','--factory-startup','--disable-autoexec','--threads',str(settings.blender_threads),
             '--python-exit-code','1','--python',str(script),'--','--config',str(config_path)]
    progress('blender_cycles',45)
    try:
        with (output/'blender_stdout.log').open('w',encoding='utf-8') as stdout,(output/'blender_stderr.log').open('w',encoding='utf-8') as stderr:
            result=subprocess.run(command,shell=False,stdout=stdout,stderr=stderr,timeout=settings.blender_timeout,
                env={k:v for k,v in os.environ.items() if not k.endswith('_API_KEY')},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired as exc:
        raise AIServiceError('BLENDER_TIMEOUT','Blender exceeded its render time limit; choose Draft or fewer views',True) from exc
    except OSError as exc:
        raise AIServiceError('BLENDER_PROCESS_FAILED','Blender could not be started; inspect render diagnostics',True) from exc
    if result.returncode:
        raise AIServiceError('BLENDER_PROCESS_FAILED','Blender exited unsuccessfully; inspect the render job diagnostics',True)
    report_path=output/'blender_report.json'
    if not report_path.is_file(): raise AIServiceError('BLENDER_OUTPUT_INVALID','Blender did not produce its geometry validation report')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if not report.get('geometry_verified') or report.get('engine')!='CYCLES':
        raise AIServiceError('BLENDER_OUTPUT_INVALID','Blender geometry or engine validation failed')
    names=[f'render_{v}.png' for v in dict.fromkeys(options.views)]
    for name in names:
        with Image.open(safe_directory(output,name)) as im:
            if im.format!='PNG' or im.size!=(configuration['width'],configuration['height']):
                raise AIServiceError('BLENDER_OUTPUT_INVALID','A Blender render is missing or invalid')
            im.verify()
    blend=output/'furniture_scene.blend'
    if not blend.is_file() or blend.stat().st_size<1000:
        raise AIServiceError('BLENDER_OUTPUT_INVALID','Editable Blender scene was not saved')
    progress('validating_render_artifacts',85)
    artifacts={name:file_hash(output/name) for name in [*names,'furniture_scene.blend','blender_report.json']}
    manifest=dict(kind='BLENDER_RENDER',source=source_metadata,source_glb_sha256=file_hash(glb),
        options=options.model_dump(),files=artifacts,approved_for_manufacturing=False,report=report)
    (output/'render_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with ZipFile(output/'blender_render_package.zip','w',ZIP_DEFLATED) as archive:
        for name in [*artifacts,'render_manifest.json']: archive.write(output/name,name)
    return dict(**manifest,manifest_sha256=file_hash(output/'render_manifest.json'),
        package_sha256=file_hash(output/'blender_render_package.zip'))
