"""Write a credential-free API collection with literal Postman variables."""
import json
from pathlib import Path


def main():
    definitions=[
        ('Create project','POST','/api/projects',{'name':'Oak writing desk'}),
        ('Analyze uploaded image','POST','/api/projects/{{projectId}}/analyze',{'image_ids':['{{imageId}}']}),
        ('Generate Sunburst concepts','POST','/api/projects/{{projectId}}/generate-concept',{'prompt':'Modern oak writing desk with rounded corners','count':1,'quality':'low','size':'1024x1024','backend':'responses'}),
        ('Edit concept through Image API','POST','/api/projects/{{projectId}}/edit-concept',{'prompt':'Preserve the silhouette and show a walnut finish','image_ids':['{{imageId}}'],'operation':'material','backend':'direct','quality':'low'}),
        ('List concepts','GET','/api/projects/{{projectId}}/concepts',None),
        ('Select concept','POST','/api/projects/{{projectId}}/concepts/{{imageId}}/select',{}),
        ('Generate CAD and Blender renders','POST','/api/projects/{{projectId}}/generate-cad',{'concept_id':'{{imageId}}','known_values':[{'key':'width_mm','value':1200},{'key':'depth_mm','value':600},{'key':'height_mm','value':750}],'render_options':{'quality':'preview','finish':'oak','views':['front','side','top','isometric','hero']}}),
        ('Poll workflow','GET','/api/v1/agents/workflows/{{workflowId}}',None),
        ('Workflow events','GET','/api/v1/agents/workflows/{{workflowId}}/events',None),
        ('Change parameter','PATCH','/api/projects/{{projectId}}/parameters',{'parameter_changes':[{'key':'width_mm','value':1300}],'render_options':{'quality':'draft','views':['hero']}}),
        ('Natural-language edit','POST','/api/projects/{{projectId}}/modify',{'prompt':'Increase only tabletop thickness to 30 mm; preserve all overall dimensions','render_options':{'quality':'preview','views':['hero','front']}}),
        ('Blender preview','POST','/api/projects/{{projectId}}/render/preview',{'render_options':{'quality':'preview','finish':'walnut','environment':'studio','views':['hero','front']}}),
        ('Blender final','POST','/api/projects/{{projectId}}/render/final',{'render_options':{'quality':'high','finish':'oak','views':['hero']}}),
        ('Compare with reference','POST','/api/projects/{{projectId}}/refine',{'render_workflow_id':'{{renderWorkflowId}}'}),
        ('Accept correction','POST','/api/projects/{{projectId}}/corrections/{{workflowId}}/C001/accept',{'render_options':{'quality':'draft','views':['hero']}}),
        ('Reject correction','POST','/api/projects/{{projectId}}/corrections/{{workflowId}}/C001/reject',{}),
        ('Project status','GET','/api/projects/{{projectId}}/status',None),
        ('Components and schedules','GET','/api/projects/{{projectId}}/components',None),
        ('Revisions','GET','/api/projects/{{projectId}}/revisions',None),
        ('Renders','GET','/api/projects/{{projectId}}/renders',None),
    ]
    for kind in ('step','glb','blend','renders','package'):
        definitions.append(('Download '+kind,'GET','/api/projects/{{projectId}}/download/'+kind,None))
    for kind in ('cad','blender','openai'):
        definitions.append((kind+' status','GET','/api/system/'+kind+'/status',None))
    items=[]
    for name,method,path,body in definitions:
        request=dict(method=method,header=[],url='{{baseUrl}}'+path)
        if body is not None:
            request['header']=[dict(key='Content-Type',value='application/json')]
            request['body']=dict(mode='raw',raw=json.dumps(body,indent=2),options=dict(raw=dict(language='json')))
        item=dict(name=name,request=request)
        if name=='Create project': item['event']=[dict(listen='test',script=dict(type='text/javascript',exec=['pm.collectionVariables.set("projectId", pm.response.json().id);']))]
        if name=='Generate Sunburst concepts': item['event']=[dict(listen='test',script=dict(type='text/javascript',exec=['pm.collectionVariables.set("workflowId", pm.response.json().workflow_id);']))]
        items.append(item)
    items.insert(1,dict(name='Upload furniture image',request=dict(method='POST',header=[],url='{{baseUrl}}/api/projects/{{projectId}}/upload-image',body=dict(mode='formdata',formdata=[dict(key='file',type='file',src=None)]))))
    collection=dict(info=dict(name='Forma AI CAD & Blender Studio',schema='https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
        description='Local password-free API examples. Image generation and AI design endpoints incur OpenAI usage. Set image/workflow variables from completed responses. Shared deployments require an existing bearer token.'),
        variable=[dict(key=k,value=v) for k,v in [('baseUrl','http://127.0.0.1:8000'),('projectId',''),('imageId',''),('workflowId',''),('renderWorkflowId','')]],item=items)
    target=Path(__file__).resolve().parents[1]/'docs'/'forma_studio.postman_collection.json'
    target.write_text(json.dumps(collection,indent=2),encoding='utf-8');print('Wrote',len(items),'API examples')


if __name__=='__main__': main()
