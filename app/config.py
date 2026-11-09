from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL','sqlite:///./data/furniture.db'))
    storage: Path = field(default_factory=lambda: Path(os.getenv('STORAGE_ROOT','outputs')).resolve())
    broker_url: str = field(default_factory=lambda: os.getenv('REDIS_URL','redis://localhost:6379/0'))
    executor: str = field(default_factory=lambda: os.getenv('JOB_EXECUTOR') or 'auto')
    _local_executor: object = field(default=None,init=False,repr=False,compare=False)
    _render_executor: object = field(default=None,init=False,repr=False,compare=False)
    token_hours: int = 8
    local_workspace: bool = field(default_factory=lambda: os.getenv('LOCAL_WORKSPACE','0') == '1')
    # Hosted without login: the single workspace is open to anyone who can reach the URL.
    public_workspace: bool = field(default_factory=lambda: os.getenv('PUBLIC_WORKSPACE','0') == '1')
    # Let visitors create their own designer account from the sign-in page.
    allow_signup: bool = field(default_factory=lambda: os.getenv('ALLOW_SIGNUP','0') == '1')
    local_workflow_process: bool = field(default_factory=lambda: os.getenv('LOCAL_WORKFLOW_PROCESS','1') == '1')
    max_request_bytes: int = 2 * 1024 * 1024
    ai_enabled: bool = field(default_factory=lambda: os.getenv('AI_ENABLED','0') == '1')
    openai_timeout_seconds: float = field(default_factory=lambda: float(os.getenv('OPENAI_TIMEOUT_SECONDS','90')))
    openai_background_mode: bool = field(default_factory=lambda: os.getenv('OPENAI_BACKGROUND_MODE','1') == '1')
    openai_poll_interval_seconds: float = field(default_factory=lambda: float(os.getenv('OPENAI_POLL_INTERVAL_SECONDS','2')))
    ai_max_output_tokens: int = field(default_factory=lambda: int(os.getenv('AI_MAX_OUTPUT_TOKENS','6000')))
    photo_max_output_tokens: int = field(default_factory=lambda: int(os.getenv('AI_PHOTO_MAX_OUTPUT_TOKENS','28000')))
    photo_timeout_seconds: float = field(default_factory=lambda: float(os.getenv('AI_PHOTO_TIMEOUT_SECONDS','600')))
    photo_reasoning_effort: str = field(default_factory=lambda: os.getenv('AI_PHOTO_REASONING_EFFORT') or os.getenv('OPENAI_REASONING_EFFORT','high'))
    photo_design_model: str = field(default_factory=lambda: os.getenv('OPENAI_PHOTO_DESIGN_MODEL',''))
    photo_review_model: str = field(default_factory=lambda: os.getenv('OPENAI_PHOTO_REVIEW_MODEL',''))
    furniture_budget_usd: str = field(default_factory=lambda: os.getenv('AI_FURNITURE_BUDGET_USD','2.00'))
    photo_refinement_passes: int = field(default_factory=lambda: int(os.getenv('AI_PHOTO_REFINEMENT_PASSES','2')))
    photo_numeric_fit_enabled: bool = field(default_factory=lambda: os.getenv('PHOTO_NUMERIC_FIT_ENABLED','1')=='1')
    cad_optimizer_enabled: bool = field(default_factory=lambda: os.getenv('CAD_OPTIMIZER_ENABLED','1')=='1')
    cad_optimizer_mode: str = field(default_factory=lambda: os.getenv('CAD_OPTIMIZER_MODE','balanced'))
    cad_optimizer_max_seconds: float = field(default_factory=lambda: float(os.getenv('CAD_OPTIMIZER_MAX_SECONDS','600')))
    cad_final_correction_enabled: bool = field(default_factory=lambda: os.getenv('CAD_FINAL_CORRECTION_ENABLED','1')=='1')
    blender_modeling_passes: int = field(default_factory=lambda: int(os.getenv('BLENDER_MODELING_PASSES','2')))
    blender_cad_tolerance_mm: float = field(default_factory=lambda: float(os.getenv('BLENDER_CAD_TOLERANCE_MM','2')))
    reasoning_effort: str = field(default_factory=lambda: os.getenv('OPENAI_REASONING_EFFORT','high'))
    blender_executable: str = field(default_factory=lambda: os.getenv('BLENDER_EXECUTABLE',''))
    blender_device: str = field(default_factory=lambda: os.getenv('BLENDER_RENDER_DEVICE','AUTO').upper())
    blender_preview_samples: int = field(default_factory=lambda: int(os.getenv('BLENDER_PREVIEW_SAMPLES','32')))
    blender_final_samples: int = field(default_factory=lambda: int(os.getenv('BLENDER_FINAL_SAMPLES','128')))
    blender_width: int = field(default_factory=lambda: int(os.getenv('BLENDER_RENDER_WIDTH','1920')))
    blender_height: int = field(default_factory=lambda: int(os.getenv('BLENDER_RENDER_HEIGHT','1080')))
    blender_timeout: int = field(default_factory=lambda: int(os.getenv('BLENDER_TIMEOUT_SECONDS','600')))
    blender_threads: int = field(default_factory=lambda: int(os.getenv('BLENDER_THREADS','4')))
    blender_max_triangles: int = field(default_factory=lambda: int(os.getenv('BLENDER_MAX_TRIANGLES','1500000')))
    blender_memory_budget_mb: int = field(default_factory=lambda: int(os.getenv('BLENDER_MEMORY_BUDGET_MB','4096')))
    blender_hdri: str = field(default_factory=lambda: os.getenv('BLENDER_HDRI_PATH',''))
    max_refinements: int = field(default_factory=lambda: int(os.getenv('MAX_AI_REFINEMENT_ITERATIONS','3')))
    max_concepts: int = field(default_factory=lambda: int(os.getenv('MAX_CONCEPT_IMAGES_PER_JOB','3')))
    ai_max_attempts: int = 3
    max_image_bytes: int = 1500 * 1024
    techdraw_docker_image: str = field(default_factory=lambda: os.getenv('TECHDRAW_DOCKER_IMAGE',''))
    techdraw_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('TECHDRAW_TIMEOUT_SECONDS','180')))
    ai_models: dict = field(default_factory=lambda: {
        'vision':os.getenv('OPENAI_REASONING_MODEL') or os.getenv('OPENAI_VISION_MODEL',''),
        'design':os.getenv('OPENAI_REASONING_MODEL') or os.getenv('OPENAI_DESIGN_MODEL',''),
        'joinery':os.getenv('OPENAI_JOINERY_MODEL') or os.getenv('OPENAI_DESIGN_MODEL',''),
        'review':os.getenv('OPENAI_REVIEW_MODEL',''),
        'render':os.getenv('OPENAI_RENDER_MODEL') or os.getenv('OPENAI_DESIGN_MODEL',''),
        'image':os.getenv('OPENAI_IMAGE_MODEL',''),
        'fallback':os.getenv('OPENAI_FALLBACK_MODEL',''),
    })

    def __post_init__(self):
        if self.cad_optimizer_mode not in ('economy','balanced','quality'):
            raise ValueError('CAD optimizer mode must be economy, balanced or quality')
        if not .05 <= self.cad_optimizer_max_seconds <= 600:
            raise ValueError('CAD optimizer time budget must be within 0.05..600 seconds')
        from decimal import Decimal,InvalidOperation
        try:
            amount=Decimal(self.furniture_budget_usd)
            if not amount.is_finite() or not Decimal('.01')<=amount<=Decimal('100'):
                raise ValueError('Furniture AI budget must be within USD 0.01..100')
        except InvalidOperation as exc:raise ValueError('Furniture AI budget must be a dollar amount') from exc
        if not 10<=self.techdraw_timeout_seconds<=900:raise ValueError('TechDraw timeout must be within 10..900 seconds')
        if not 0<=self.blender_modeling_passes<=3:raise ValueError('Blender modeling passes must be within 0..3')
        if not .1<=self.blender_cad_tolerance_mm<=10:raise ValueError('Blender/CAD comparison tolerance must be within 0.1..10 mm')
        self.storage = Path(self.storage).resolve()
        reasoning_model=os.getenv('OPENAI_REASONING_MODEL')
        if reasoning_model:
            self.ai_models.update({role:reasoning_model for role in ('vision','design','joinery','review','render')})
        if self.executor == 'auto': self.executor = 'local' if self.local_workspace else 'celery'
        if self.executor not in ('local','celery','eager'):
            raise ValueError('JOB_EXECUTOR must be local, celery or eager (test only)')
        if not 1 <= self.openai_timeout_seconds <= 600 or not 256 <= self.ai_max_output_tokens <= 16000:
            raise ValueError('AI timeout/token limits exceed the supported bounds')
        if not .25 <= self.openai_poll_interval_seconds <= 10:
            raise ValueError('OpenAI poll interval must be within 0.25..10 seconds')
        if not 256<=self.photo_max_output_tokens<=32000:
            raise ValueError('Photo geometry token budget must be within 256..32000')
        if not 1<=self.photo_timeout_seconds<=600:
            raise ValueError('Photo geometry timeout must be within 1..600 seconds')
        if self.photo_reasoning_effort not in ('','low','medium','high','xhigh','max'):
            raise ValueError('Unsupported photo reasoning effort')
        if not 0<=self.photo_refinement_passes<=2: raise ValueError('Photo refinement passes must be within 0..2')
        if self.reasoning_effort not in ('low','medium','high','xhigh','max'):
            raise ValueError('Unsupported reasoning effort')
        if self.blender_device not in ('AUTO','CPU','GPU'):
            raise ValueError('BLENDER_RENDER_DEVICE must be AUTO, CPU or GPU')
        if not (1<=self.blender_preview_samples<=512 and 1<=self.blender_final_samples<=2048 and
                128<=self.blender_width<=4096 and 128<=self.blender_height<=4096 and
                10<=self.blender_timeout<=1800 and 1<=self.blender_threads<=32 and
                1000<=self.blender_max_triangles<=5000000 and 256<=self.blender_memory_budget_mb<=32768 and
                0<=self.max_refinements<=3 and 1<=self.max_concepts<=3):
            raise ValueError('Rendering or workflow resource limits are outside supported bounds')
