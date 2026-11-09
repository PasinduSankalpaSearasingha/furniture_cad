from app.ai.prompt_templates import RENDER


def render(gateway,prompt,images,quality,size):
    return gateway.image(RENDER,prompt,images,quality,size)
