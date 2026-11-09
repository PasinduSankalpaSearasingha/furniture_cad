"""Exercise the built UI and real upload API with an offline, temporary workspace.

CAD dispatch is disabled, so this check never submits a paid model request.
"""
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import time

from PIL import Image
from playwright.sync_api import sync_playwright, expect
import uvicorn

from app.config import Settings
from app.database import AgentWorkflow
from app.main import create_app
from app.agents import orchestrator
from app.pipeline import canonical_hash
from app.sample_dimensions import ROOT


def main():
    output = Path('outputs/sample-dimensions-verification');output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('OPENAI_API_KEY', 'offline-browser-test-no-dispatch')
    with TemporaryDirectory(prefix='furniture-sample-ui-') as temporary:
        root = Path(temporary)
        app = create_app(Settings(database_url=f'sqlite:///{root / "test.db"}', storage=root / 'outputs',
            local_workspace=True, executor='eager', ai_enabled=True,
            ai_models={role: 'OFFLINE_TEST' for role in ('vision', 'design', 'review')}))
        app.state.db.create_schema_for_tests()
        original = orchestrator.dispatch_workflow
        orchestrator.dispatch_workflow = lambda *args: None
        listener = socket.socket();listener.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
        thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True);thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                if time.monotonic() > deadline:raise RuntimeError('Test server did not start')
                time.sleep(.05)
            with sync_playwright() as playwright:
                options = dict(headless=True)
                if not Path(playwright.chromium.executable_path).is_file():
                    installed = sorted((Path(os.environ['LOCALAPPDATA']) / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
                    if installed:options['executable_path'] = str(installed[-1])
                browser = playwright.chromium.launch(**options)
                page = browser.new_page(viewport=dict(width=1440, height=1100))
                failures = [];page.on('pageerror', lambda error: failures.append(str(error)))

                def fresh(label):
                    response = page.request.post(url + '/api/projects', data=dict(name=label))
                    assert response.status == 201, response.text()
                    project = response.json()['id']
                    page.goto(f'{url}/studio?project={project}&tab=reference')
                    expect(page.locator('#project')).to_have_value(project)
                    return project

                fresh('Sample table dimensions')
                page.get_by_label('Overall width', exact=True).fill('2900')
                page.locator('input[type=file]').set_input_files(str(ROOT / 'table-a-1.jpg'))
                summary = page.get_by_role('region', name='Image dimensions')
                expect(page.get_by_role('heading', name='Matched sample: Dining table A')).to_be_visible()
                expect(summary).to_contain_text('2,900 mm');expect(summary).to_contain_text('1,100 mm')
                expect(summary).to_contain_text('Your input');expect(summary).to_contain_text('Sample drawing')
                page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                page.reload();expect(summary).to_contain_text('2,900 mm')
                page.set_viewport_size(dict(width=390, height=844))
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.set_viewport_size(dict(width=1440, height=1100))

                project = fresh('Unmatched photo estimates')
                unknown = root / 'unknown.png';Image.new('RGB', (800, 600), '#827459').save(unknown)
                page.locator('input[type=file]').set_input_files(str(unknown))
                expect(summary).to_contain_text('No reliable sample match')
                # Simulate only the AI dimension checkpoint to verify live UI
                # estimates independently of the paid provider and CAD duration.
                with app.state.db.sessions.begin() as session:
                    workflow = session.query(AgentWorkflow).filter_by(project_id=project).one()
                    payload = dict(values=[dict(key=key, value=value, explanation='Offline test estimate')
                        for key, value in [('width_mm', 900), ('depth_mm', 500), ('height_mm', 750)]],
                        assumptions=['Offline test data'])
                    workflow.checkpoints = dict(engineering_dimensions=dict(payload=payload, sha256=canonical_hash(payload)))
                page.get_by_role('button', name='Refresh', exact=True).click()
                expect(summary).to_contain_text('900 mm');expect(summary).to_contain_text('AI estimate')

                large = Path('data/reference_samples/Momentro/Shoe Cabinet/Referance Resources/ATLANTE 4 WOOD.jpg')
                if large.is_file():
                    fresh('Large original shoe cabinet image')
                    page.locator('input[type=file]').set_input_files(str(large))
                    expect(page.get_by_role('heading', name='Matched sample: Shoe cabinet')).to_be_visible(timeout=30000)
                    expect(summary).to_contain_text('2,100 mm');expect(summary).to_contain_text('858 mm')
                assert not failures, failures
                browser.close()
            report = dict(status='passed', desktop=True, mobile=True, no_horizontal_overflow=True,
                user_override=True, reload_persistence=True, unmatched_estimates=True,
                large_original_resize=large.is_file(), paid_ai_requests=0, browser_errors=failures)
            (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2))
        finally:
            server.should_exit = True;thread.join(timeout=10)
            app.state.db.engine.dispose();orchestrator.dispatch_workflow = original


if __name__ == '__main__':main()
