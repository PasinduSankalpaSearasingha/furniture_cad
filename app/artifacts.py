"""Manifest-based file discovery and bounded previews of generated documents."""
import csv
import json
from itertools import islice
from pathlib import Path
from urllib.parse import quote
from zipfile import ZipFile
from openpyxl import load_workbook

TYPES = {
    '.fcstd':('binary','CAD','application/x-extension-fcstd'),
    '.svg':('image','Drawings','image/svg+xml'),
    '.step':('model','CAD','model/step'), '.stl':('model','CAD','model/stl'),
    '.glb':('model','CAD','model/gltf-binary'), '.png':('image','Images','image/png'),
    '.pdf':('pdf','Drawings','application/pdf'), '.dxf':('drawing','Drawings','application/dxf'),
    '.xlsx':('workbook','Schedules','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '.csv':('table','Schedules','text/csv'), '.json':('json','Data','application/json'),
    '.md':('text','Documents','text/plain'), '.zip':('archive','Packages','application/zip'),
}


def file_info(directory, job, revision, name, digest):
    kind, category, media_type = TYPES.get(Path(name).suffix.lower(),('binary','Other','application/octet-stream'))
    base = f'/api/v1/jobs/{job.id}/files/{quote(name,safe="")}'
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    primary=manifest.get('primary_model','cabinet.glb')
    thumbnail = f'/api/v1/jobs/{job.id}/files/cad_preview.png' if (directory/'cad_preview.png').is_file() else None
    part_stem=Path(name).stem
    if part_stem.startswith('part_') and part_stem.endswith('_REVIEW') and (directory/(part_stem+'.glb')).is_file():
        primary=part_stem+'.glb'
        thumbnail=f'/api/v1/jobs/{job.id}/files/{part_stem.removesuffix("_REVIEW")}_preview.png'
    gallery = thumbnail if kind=='model' else base+'/preview' if kind in ('image','drawing') else None
    pdf_drawing=part_stem+'.dxf' if part_stem.startswith('part_') else 'front_elevation_REVIEW.dxf'
    if kind=='pdf' and (directory/pdf_drawing).is_file():
        gallery = f'/api/v1/jobs/{job.id}/files/{pdf_drawing}/preview'
    return dict(name=name,bytes=(directory/name).stat().st_size,sha256=digest,
        kind=kind,category=category,media_type=media_type,job_id=job.id,revision=revision.number,
        download_url=base,preview_url=base+'/preview',
        model_url=f'/api/v1/jobs/{job.id}/files/{primary}' if kind=='model' else None,
        thumbnail_url=thumbnail,gallery_preview_url=gallery,
        preview_note='Review projection; open to view the complete PDF' if kind=='pdf' else None)


def list_components(directory, job, revision):
    parts = json.loads((directory/'parts_registry.json').read_text(encoding='utf-8'))
    base = f'/api/v1/jobs/{job.id}/files'
    result = []
    for part in parts:
        drawing = f'part_{part["part_id"]}_REVIEW.dxf'
        whole=part.get('output_scope')=='WHOLE_FURNITURE'
        if not whole and not (directory/drawing).is_file(): continue
        if whole:drawing='all_dimensions_REVIEW.dxf'
        model=part.get('model_file','cabinet.glb');preview=part.get('preview_file')
        thumbnail=base+'/'+quote(preview,safe='') if preview else base+'/'+quote(drawing,safe='')+'/preview'
        result.append(dict(part,name=f'{part["part_id"]} - {part["label"]}',kind='model',category='Components',
            component_id=part['part_id'],job_id=job.id,
            preview_key=f'component:{job.id}:{part["part_id"]}',
            model_url=base+'/'+quote(model,safe=''),download_url=base+'/'+quote(drawing,safe=''),download_name=drawing,
            thumbnail_url=thumbnail,gallery_preview_url=thumbnail,preview_note=(
                'Closed solid part; preview derived from reopened STEP' if part.get('geometry_source')=='ENGINEERING_CAD' else
                'Estimated source surface; open to inspect the individual region') if preview else 'Part drawing; open to inspect the 3D component'))
    return result


def list_files(directory, job, revision):
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    entries = {**manifest['files'],'manifest.json':job.manifest_hash,'design_review_package.zip':job.package_hash}
    return [file_info(directory,job,revision,name,digest) for name,digest in sorted(entries.items())]


def document_preview(path, sheet=None):
    suffix = path.suffix.lower()
    if suffix=='.xlsx':
        book = load_workbook(path,read_only=True,data_only=True)
        try:
            selected = sheet or book.sheetnames[0]
            if selected not in book.sheetnames: raise ValueError('Worksheet not found')
            page = book[selected]
            rows = list(page.iter_rows(min_row=1,max_row=min(page.max_row or 1,201),max_col=min(page.max_column or 1,40),values_only=True))
            return dict(kind='workbook',sheets=book.sheetnames,sheet=selected,
                columns=[str(value or '') for value in rows[0]] if rows else [],rows=rows[1:],
                truncated=(page.max_row or 0)>201 or (page.max_column or 0)>40)
        finally: book.close()
    if suffix=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as stream:
            rows = list(islice(csv.reader(stream),202))
        return dict(kind='table',columns=rows[0][:40] if rows else [],
            rows=[row[:40] for row in rows[1:201]],truncated=len(rows)>201 or any(len(row)>40 for row in rows))
    if suffix in ('.json','.md'):
        source = path.read_text(encoding='utf-8')
        if suffix=='.json': source = json.dumps(json.loads(source),indent=2,ensure_ascii=False)
        return dict(kind='json' if suffix=='.json' else 'text',text=source[:1_000_000],truncated=len(source)>1_000_000)
    if suffix=='.zip':
        with ZipFile(path) as archive:
            return dict(kind='archive',columns=['File','Bytes'],rows=[[item.filename,item.file_size] for item in archive.infolist()[:500]],truncated=len(archive.infolist())>500)
    raise ValueError('No document preview for this file type')


def dxf_svg(path):
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg, config
    document = ezdxf.readfile(path)
    backend = svg.SVGBackend()
    settings = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                                    color_policy=config.ColorPolicy.BLACK)
    Frontend(RenderContext(document),backend,config=settings).draw_layout(document.modelspace())
    return backend.get_string(layout.Page(297,210,layout.Units.mm,margins=layout.Margins.all(10)))
