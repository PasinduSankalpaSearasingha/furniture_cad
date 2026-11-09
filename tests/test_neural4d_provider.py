"""Official-contract mocked calls; paid submission failures must never retry."""
import asyncio
import json
from io import BytesIO
import httpx
from PIL import Image
import pytest
from app.integrations.neural4d_client import Neural4DClient, ProviderStore, ProviderError, SubmissionUncertain, public_download_url


def image_bytes():
    stream = BytesIO(); Image.new('RGB', (256, 256), 'white').save(stream, format='PNG'); return stream.getvalue()


def test_submission_polling_and_restart_cache_make_one_generation(tmp_path):
    calls = []
    def handler(request):
        name = request.url.path.rsplit('/', 1)[-1]
        calls.append(name)
        assert request.headers['authorization'] == 'Bearer test-token'
        if name == 'mattingImage':
            assert 'multipart/form-data' in request.headers['content-type']
            return httpx.Response(200, json={'requestId': 12})
        if name == 'getMattedResult':
            assert json.loads(request.content) == {'requestId': 12}
            return httpx.Response(200, json={'fileKeys': ['key-one'], 'mattedImageUrls': ['https://example.com/matte.png']})
        if name == 'generateModelWithImage':
            from email.parser import BytesParser
            from email.policy import default
            assert request.headers['content-type'].startswith('multipart/form-data;')
            message=BytesParser(policy=default).parsebytes(('Content-Type: '+request.headers['content-type']+'\r\n\r\n').encode()+request.content)
            fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in message.iter_parts()}
            assert fields['image']==image_bytes()
            assert fields['modelCount']==b'1' and fields['disablePbr']==b'1'
            assert fields['fileKey']==b'key-one'
            return httpx.Response(200, json={'uuids': ['model-one']})
        return httpx.Response(200, json={'codeStatus': 0, 'modelUrl': 'https://example.com/reference.glb'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for _ in range(2):
                async with Neural4DClient(ProviderStore(tmp_path/'jobs.db'), api_key='test-token', client=http) as client:
                    key, result = await client.advance(image_bytes())
                    assert result['uuid'] == 'model-one'
                    assert result['generation_attempts'] == 1
                    assert result['usage'][1]['documented_points'] == 80
    asyncio.run(run())
    assert calls.count('generateModelWithImage') == 1
    assert calls.count('mattingImage') == 1


def test_generation_rejection_retains_redacted_diagnostics_and_never_retries(tmp_path):
    store=ProviderStore(tmp_path/'jobs.db');store.update('bound',request_id=1,file_key='ready',state='MATTED')
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400,json={'errors':[dict(type='field',msg='Image file is required',path='image',
            location='body',value='sensitive-token')],'message':'sensitive-token'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(store,api_key='sensitive-token',client=http) as client:
                for _ in range(2):
                    with pytest.raises(ProviderError) as error:await client.advance(image_bytes(),cache_key='bound')
                    assert error.value.http_status==400
                    assert error.value.diagnostics['errors'][0]['msg']=='Image file is required'
    asyncio.run(run())
    record=store.get('bound')
    assert len(calls)==1 and record['state']=='GENERATION_REJECTED' and record['generation_attempts']==1
    assert 'sensitive-token' not in json.dumps(record)
    assert 'value' not in record['generation_error']['diagnostics']['errors'][0]


@pytest.mark.parametrize('failure', ['transport', 'server', 'malformed'])
def test_ambiguous_paid_submission_is_not_resubmitted(tmp_path, failure):
    count = 0
    def handler(request):
        nonlocal count
        name = request.url.path.rsplit('/', 1)[-1]
        if name == 'mattingImage':
            return httpx.Response(200, json={'requestId': 1})
        if name == 'getMattedResult':
            return httpx.Response(200, json={'fileKeys': ['key']})
        count += 1
        if failure == 'transport':
            raise httpx.ReadTimeout('unknown outcome')
        return httpx.Response(503, json={}) if failure == 'server' else httpx.Response(200, json={'uuids': []})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for _ in range(2):
                async with Neural4DClient(ProviderStore(tmp_path/'jobs.db'), api_key='fake', client=http) as client:
                    with pytest.raises(SubmissionUncertain):
                        await client.advance(image_bytes())
    asyncio.run(run())
    assert count == 1


def test_poll_failure_retries_read_only_requests_then_resumes_uuid(tmp_path):
    calls = []
    fail = True
    def handler(request):
        name = request.url.path.rsplit('/', 1)[-1]; calls.append(name)
        if name == 'mattingImage': return httpx.Response(200, json={'requestId': 1})
        if name == 'getMattedResult': return httpx.Response(200, json={'fileKeys': ['key']})
        if name == 'generateModelWithImage': return httpx.Response(200, json={'uuids': ['uuid']})
        if fail: return httpx.Response(503)
        return httpx.Response(200, json={'codeStatus': 0, 'modelUrl': 'https://example.com/mesh.glb'})
    async def run():
        nonlocal fail
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(ProviderStore(tmp_path/'jobs.db'), api_key='fake', client=http) as client:
                with pytest.raises(ProviderError): await client.advance(image_bytes())
                fail = False
                await client.advance(image_bytes())
    asyncio.run(run())
    assert calls.count('generateModelWithImage') == 1
    assert calls.count('retrieveModel') == 4


def test_obj_conversion_is_explicit_and_cannot_repeat_charged_request(tmp_path):
    store = ProviderStore(tmp_path/'jobs.db'); store.update('key', uuid='model', state='READY')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'statusType': 0, 'modelUrl': 'https://example.com/mesh.obj'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(store, api_key='fake', client=http) as client:
                with pytest.raises(ProviderError, match='explicit'):
                    await client.convert_to_obj('key')
                await client.convert_to_obj('key', allow_paid_conversion=True)
                await client.convert_to_obj('key', allow_paid_conversion=True)
    asyncio.run(run())
    assert len(calls) == 1


def test_download_does_not_forward_api_token_and_enforces_size(tmp_path):
    def handler(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=b'abcdef')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(ProviderStore(tmp_path/'jobs.db'), api_key='fake', client=http,
                                       url_validator=lambda url: url) as client:
                with pytest.raises(ProviderError, match='budget'):
                    await client.download('https://example.com/mesh.glb', tmp_path/'mesh.glb', max_bytes=3)
    asyncio.run(run())
    assert not (tmp_path/'mesh.glb').exists()
    assert not (tmp_path/'mesh.glb.part').exists()


def test_public_asset_validation_rejects_local_hosts():
    for url in ('http://example.com/a', 'https://127.0.0.1/a', 'https://[::1]/a', 'https://u:p@example.com/a'):
        with pytest.raises(ProviderError): public_download_url(url)


def test_saved_job_binding_survives_credential_rotation_without_regeneration(tmp_path):
    store = ProviderStore(tmp_path/'jobs.db')
    store.update('bound-original-account-key', request_id=1, file_key='key', uuid='persisted-model', state='GENERATING')
    calls = []
    def handler(request):
        calls.append(request.url.path.rsplit('/',1)[-1])
        return httpx.Response(200, json={'codeStatus':0, 'modelUrl':'https://example.com/mesh.glb'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(store, api_key='rotated-token', client=http) as client:
                _, record = await client.advance(image_bytes(), cache_key='bound-original-account-key')
                assert record['uuid'] == 'persisted-model'
    asyncio.run(run())
    assert calls == ['retrieveModel']


def test_successful_mesh_download_is_cached_across_jobs(tmp_path):
    import trimesh
    data = trimesh.Scene(trimesh.creation.box()).export(file_type='glb')
    store = ProviderStore(tmp_path/'jobs.db')
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, content=data)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with Neural4DClient(store, api_key='fake', client=http, url_validator=lambda url:url) as client:
                key = client.cache_key(image_bytes())
                store.update(key, model_url='https://example.com/mesh.glb', uuid='done')
                await client.ensure_mesh(image_bytes(), tmp_path/'job-one'/'mesh.glb')
                await client.ensure_mesh(image_bytes(), tmp_path/'job-two'/'mesh.glb')
    asyncio.run(run())
    assert calls == ['GET']
    assert (tmp_path/'job-one'/'mesh.glb').read_bytes() == (tmp_path/'job-two'/'mesh.glb').read_bytes()
