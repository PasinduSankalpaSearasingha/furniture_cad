"""OFFLINE DEMO ONLY. Synthetic responses; this is not an image-understanding implementation."""
import base64
from io import BytesIO
from PIL import Image
from app.ai.schemas import JoineryLookup
from app.ai.openai_client import AIServiceError
from app.pipeline import canonical_hash


def fixtures():
    parts = [('P001','Left side','panel','plywood'),('P002','Right side','panel','plywood'),
             ('P003','Top','panel','plywood'),('P004','Bottom','panel','plywood'),('P005','Assumed back','panel','plywood'),
             ('D001','Left stile','frame','solid_wood'),('D002','Right stile','frame','solid_wood'),
             ('D003','Top rail','frame','solid_wood'),('D004','Bottom rail','frame','solid_wood'),
             ('G001','Visual glass pane','glass','glass')]
    components = [dict(component_id=pid,label=label,kind=kind,possible_materials=[material],
        provenance='AI_ESTIMATED' if pid=='P005' else 'IMAGE_OBSERVED',
        evidence='SYNTHETIC MOCK RESPONSE: not an actual vision measurement') for pid,label,kind,material in parts]
    vision = dict(category='display cabinet',components=components,proportions=['Tall rectangular cabinet'],
        observations=['Synthetic fixture: one framed glass door'],assumptions=['Back construction is not visible'],
        missing_measurements=[dict(field='height_mm',reason='Physical measurement required')],
        limitations=['Mocked response; reference itself is a CAD-derived review preview'])
    design = dict(status='SUPPORTED_TEMPLATE',template_id='cabinet.v1',summary='Synthetic mixed-material cabinet proposal',
        components=components,relationships=[dict(connection_id='C001',first_component_id='P001',second_component_id='P004',relationship='side to bottom')],
        parameters=[dict(field='width_mm',suggested_value=700,provenance='AI_ESTIMATED',explanation='Unverified fixture estimate; never manufacturing authority')],
        required_measurements=[],unrepresented_features=[])
    joinery = dict(joints=[dict(connection_id='C001',profile_id=None,status='ENGINEERING_REVIEW_REQUIRED',reason='No factory data supplied')],
        required_hardware_types=['hinge','connector','glass_retainer'],unsupported_connections=['Frame joints and glass retention require engineering'])
    review = dict(summary='Synthetic advisory review; engineering checks remain authoritative',issues=[],
                  revision_recommended=False,engineering_certified=False)
    return dict(FurnitureVisionResult=vision,DesignProposal=design,JoineryRecommendation=joinery,QualityReviewResult=review)


def png_bytes():
    image = Image.new('RGB',(48,64),'tan')
    output = BytesIO()
    image.save(output,format='PNG')
    return output.getvalue()


class MockGateway:
    calls = []
    responses = None
    fail_role = None
    fail_once = False

    def __init__(self,config,models,record=lambda name,data:None):
        self.record = record

    def trace(self,role,payload,images=0):
        invocation = self.record('invocation_started',dict(model='MOCK_MODEL_NOT_FOR_API',request_hash=canonical_hash(payload)))
        self.record('invocation_completed',dict(invocation_id=invocation,model='MOCK_MODEL_NOT_FOR_API',
            response_id='MOCK_RESPONSE',usage=dict(input_tokens=100,output_tokens=50,cached_input_tokens=0,
                generated_images=images,image_usage=[],raw={})))

    def structured(self,role,instructions,payload,schema,images=(),tools=None,max_output_tokens=None,timeout_seconds=None,reasoning_effort=None):
        type(self).calls.append(role)
        if type(self).fail_role==role:
            if type(self).fail_once: type(self).fail_role=None
            raise AIServiceError('AI_TIMEOUT','Synthetic timeout for offline test',True)
        if tools:
            arguments = JoineryLookup(materials=['plywood'])
            self.record('tool_called',dict(name='lookup_approved_joinery',arguments=arguments.model_dump()))
            tools['lookup_approved_joinery']['handler'](arguments)
        self.trace(role,dict(schema=schema.__name__,payload=payload))
        return schema.model_validate_json(__import__('json').dumps((type(self).responses or fixtures())[schema.__name__]))

    def image(self,instructions,prompt,images,quality,size):
        type(self).calls.append('image')
        if type(self).fail_role=='image': raise AIServiceError('IMAGE_GENERATION_FAILED','Synthetic image-tool failure',True)
        self.trace('image',dict(prompt=prompt,quality=quality,size=size),images=1)
        return base64.b64encode(png_bytes()).decode()
