"""Update only named non-secret options; never print or replace the backend key."""
from pathlib import Path


def main():
    target=Path(__file__).resolve().parents[1]/'.env'
    text=target.read_text(encoding='utf-8') if target.exists() else ''
    updates={'OPENAI_REASONING_MODEL':'gpt-6.1-sol','OPENAI_IMAGE_MODEL':'gpt-image-2.5-sunburst',
        'OPENAI_REASONING_EFFORT':'high','BLENDER_RENDER_ENGINE':'CYCLES','BLENDER_RENDER_DEVICE':'AUTO',
        'BLENDER_PREVIEW_SAMPLES':'32','BLENDER_FINAL_SAMPLES':'128','BLENDER_RENDER_WIDTH':'1920',
        'BLENDER_RENDER_HEIGHT':'1080','BLENDER_TIMEOUT_SECONDS':'600','BLENDER_THREADS':'4',
        'MAX_AI_REFINEMENT_ITERATIONS':'3','AUTO_APPLY_UNVERIFIED_GEOMETRY':'false','MAX_CONCEPT_IMAGES_PER_JOB':'3',
        'BLENDER_MODELING_PASSES':'2','BLENDER_CAD_TOLERANCE_MM':'2'}
    lines=[]
    for line in text.splitlines():
        name=line.partition('=')[0].strip()
        lines.append(name+'='+updates.pop(name) if name in updates else line)
    lines.extend(name+'='+value for name,value in updates.items())
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Updated GPT-6.1 Sol and background Blender settings; existing credentials preserved.')


if __name__=='__main__': main()
