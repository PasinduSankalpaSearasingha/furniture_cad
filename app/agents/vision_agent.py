from app.ai.schemas import FurnitureVisionResult
from app.ai.prompt_templates import VISION


def analyze(gateway,images,notes):
    return gateway.structured('vision',VISION,{'notes':notes},FurnitureVisionResult,images)
