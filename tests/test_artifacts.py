"""Generated-file discovery, preview contents and private access."""
import json
import xml.etree.ElementTree as ET
from io import BytesIO
from PIL import Image
from .test_api import workspace


def test_manifest_lists_every_generated_file(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    path = f'/api/v1/jobs/{jid}/files'
    assert client.get(path).status_code == 401
    assert client.get(path,headers=h['outsider']).status_code == 404
    result = client.get(path,headers=h['maker'])
    assert result.status_code == 200,result.text
    files = result.json()['files']
    directory = app.state.settings.storage/jid
    assert {file['name'] for file in files} == {p.name for p in directory.iterdir() if p.is_file()}
    assert {'cad_preview.png','cabinet.step','cabinet.glb','manifest.json','design_review_package.zip'} <= {f['name'] for f in files}
    assert all(file['bytes']>0 and len(file['sha256'])==64 for file in files)
    assert all(file['revision']==1 for file in files)


def test_cad_image_pdf_and_glb_can_be_viewed_inline(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    base = f'/api/v1/jobs/{jid}/files'
    image = client.get(base+'/cad_preview.png/preview',headers=h['maker'])
    assert image.status_code == 200
    assert image.headers['content-type'].startswith('image/png')
    with Image.open(BytesIO(image.content)) as opened:
        assert opened.width>500 and opened.height>500
    pdf = client.get(base+'/drawings_REVIEW.pdf/preview',headers=h['maker'])
    assert pdf.content[:4] == b'%PDF'
    assert pdf.headers['content-disposition'].startswith('inline')
    glb = client.get(base+'/cabinet.glb?inline=true',headers=h['maker'])
    assert glb.content[:4] == b'glTF'
    assert glb.headers['content-type'].startswith('model/gltf-binary')
    assert glb.headers['content-disposition'].startswith('inline')


def test_real_dxf_renders_as_svg(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    names=client.get(f'/api/v1/jobs/{jid}/files',headers=h['maker']).json()['files']
    name=next(file['name'] for file in names if file['name'].endswith('.dxf'))
    response=client.get(f'/api/v1/jobs/{jid}/files/{name}/preview',headers=h['maker'])
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('image/svg+xml')
    root=ET.fromstring(response.content)
    assert root.tag.endswith('svg')
    assert len(list(root.iter()))>10


def test_spreadsheet_csv_reports_and_archive_preview(workspace):
    client,app,h,ids,pid,rid,jid = workspace
    base=f'/api/v1/jobs/{jid}/files'
    workbook=client.get(base+'/schedules_REVIEW.xlsx/preview',headers=h['maker']).json()
    assert workbook['kind']=='workbook'
    assert len(workbook['sheets'])==4
    assert len(workbook['rows'])==9
    glass=client.get(base+'/schedules_REVIEW.xlsx/preview?sheet=Glass',headers=h['maker']).json()
    assert glass['sheet']=='Glass' and len(glass['rows'])==1
    assert client.get(base+'/schedules_REVIEW.xlsx/preview?sheet=unknown',headers=h['maker']).status_code==422
    csv=client.get(base+'/cut_list_REVIEW.csv/preview',headers=h['maker']).json()
    assert 'part_id' in csv['columns'] and len(csv['rows'])==9
    report=client.get(base+'/validation_report.json/preview',headers=h['maker']).json()
    assert json.loads(report['text'])['approved_for_manufacturing'] is False
    notes=client.get(base+'/assembly_review.md/preview',headers=h['maker']).json()
    assert notes['kind']=='text' and notes['text']
    archive=client.get(base+'/design_review_package.zip/preview',headers=h['maker']).json()
    assert 'manifest.json' in {row[0] for row in archive['rows']}


def test_previews_retain_access_and_hash_checks(workspace):
    client,app,h,ids,pid,rid,jid=workspace
    url=f'/api/v1/jobs/{jid}/files/validation_report.json/preview'
    assert client.get(url).status_code==401
    assert client.get(url,headers=h['outsider']).status_code==404
    assert client.get(f'/api/v1/jobs/{jid}/files/not_registered.json/preview',headers=h['maker']).status_code==404
    path=app.state.settings.storage/jid/'validation_report.json'
    original=path.read_bytes()
    try:
        path.write_text('{}',encoding='utf-8')
        assert client.get(url,headers=h['maker']).status_code==409
        assert client.get(f'/api/v1/jobs/{jid}/files',headers=h['maker']).status_code==409
    finally: path.write_bytes(original)


def test_gallery_uses_real_previews_and_private_component_geometry(workspace):
    client,app,h,ids,pid,rid,jid=workspace
    files=client.get(f'/api/v1/jobs/{jid}/files',headers=h['maker']).json()['files']
    by_name={item['name']:item for item in files}
    assert by_name['cabinet.step']['gallery_preview_url'].endswith('/cad_preview.png')
    assert by_name['front_elevation_REVIEW.dxf']['gallery_preview_url'].endswith('.dxf/preview')
    assert by_name['drawings_REVIEW.pdf']['gallery_preview_url'].endswith('/front_elevation_REVIEW.dxf/preview')
    assert 'complete PDF' in by_name['drawings_REVIEW.pdf']['preview_note']
    path=f'/api/v1/jobs/{jid}/components'
    assert client.get(path).status_code==401
    assert client.get(path,headers=h['outsider']).status_code==404
    response=client.get(path,headers=h['maker'])
    assert response.status_code==200,response.text
    components=response.json()['components']
    registry=json.loads((app.state.settings.storage/jid/'parts_registry.json').read_text())
    assert {item['component_id'] for item in components}=={item['part_id'] for item in registry}
    assert all(item['model_url'].endswith('/cabinet.glb') for item in components)
    assert all(item['download_name'].endswith('.dxf') for item in components)
    assert len({item['preview_key'] for item in components})==len(registry)


def test_component_previews_refuse_tampered_packages(workspace):
    client,app,h,ids,pid,rid,jid=workspace
    registry=app.state.settings.storage/jid/'parts_registry.json'
    original=registry.read_bytes()
    try:
        registry.write_text('[]',encoding='utf-8')
        assert client.get(f'/api/v1/jobs/{jid}/components',headers=h['maker']).status_code==409
    finally: registry.write_bytes(original)
