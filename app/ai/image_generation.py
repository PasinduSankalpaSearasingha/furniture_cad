"""Image generation lives behind the same Responses gateway as text specialists."""
import base64
from .openai_client import AIServiceError


def decode_generated_image(encoded):
    if not isinstance(encoded,str) or len(encoded)>28*1024*1024:
        raise AIServiceError('IMAGE_GENERATION_FAILED','Generated image exceeded the supported byte limit')
    try: return base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise AIServiceError('IMAGE_GENERATION_FAILED','Image tool returned invalid base64 data')


class FurnitureImageGenerationService:
    """All paths execute a real SDK call; persistence validates the decoded raster separately."""
    def __init__(self,gateway,quality='low',size='1024x1024',output_format='png',backend='responses'):
        self.gateway,self.quality,self.size,self.output_format,self.backend=gateway,quality,size,output_format,backend

    def generate_concept(self,specification,images=()):
        prompt=str(specification)
        if self.backend=='direct':
            import base64
            pixels=[base64.b64decode(im['image_url'].split(',',1)[1],validate=True) for im in images]
            encoded=self.gateway.image_api(prompt,pixels,self.quality,self.size,self.output_format)
        else:
            encoded=self.gateway.image('Create an original furniture design concept. This is an AI_CONCEPT_IMAGE, '
                'not measurement evidence or a manufacturing drawing. Follow the requested design.',
                prompt,images,self.quality,self.size,output_format=self.output_format)
        return decode_generated_image(encoded)

    def generate_variations(self,specification,images=()):
        return self.generate_concept('Create a distinct design variation: '+str(specification),images)

    def edit_reference_image(self,image,instructions):
        return self.generate_concept('Edit the supplied reference: '+instructions,[image])

    def generate_material_variations(self,image,finish):
        return self.generate_concept('Appearance concept only. Preserve the silhouette and show '+finish,[image])

    def generate_marketing_concept(self,image,instructions=''):
        return self.generate_concept('Create a furniture product presentation concept. '+instructions,[image])
