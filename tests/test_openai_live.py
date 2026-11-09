"""Explicit opt-in only. This test sends a real image and incurs API usage."""
import os
from pathlib import Path
import base64
import pytest
from app.config import Settings
from app.ai.openai_client import ResponsesGateway
from app.agents.vision_agent import analyze


@pytest.mark.skipif(os.getenv('OPENAI_LIVE_TESTS')!='1',reason='Live OpenAI integration requires explicit OPENAI_LIVE_TESTS=1')
def test_live_vision_schema_and_account_availability():
    config = Settings()
    if not config.ai_enabled or not os.getenv('OPENAI_API_KEY') or not config.ai_models['vision']:
        pytest.fail('Live test requires AI_ENABLED=1, OPENAI_API_KEY and OPENAI_VISION_MODEL')
    data = Path('examples/ai_demo/wooden_glass_cabinet_CAD_REFERENCE.png').read_bytes()
    result = analyze(ResponsesGateway(config,config.ai_models),[dict(type='input_image',
        image_url='data:image/png;base64,'+base64.b64encode(data).decode())],
        'CAD-derived review reference, not a measured photograph; identify visible furniture components')
    assert result.components and result.missing_measurements
