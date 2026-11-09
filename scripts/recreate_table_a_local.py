"""Local, explicitly unreviewed reconstruction of the user's Table A references.

Preview is the default. --publish adds a new immutable project revision after
the same solid, dimension and STEP-export checks as normal furniture jobs.
No OpenAI request is made and no AI visual acceptance is fabricated.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.photo_schema import PhotoDesign
from app.pipeline import canonical_hash

PROJECT='50167110-b19c-496c-865f-05b69ea4f756'
SOURCE='260ff5a4-fe94-4245-ac68-d4a6f79b3278'
OUTPUT=Path('outputs/table-a-local-recreation')
KEY='table-a-three-reference-local-v1'


def design():
    values=dict(width_mm=2800,depth_mm=1100,height_mm=750,tabletop_thickness_mm=25,
        tabletop_corner_radius_mm=90,tabletop_edge_radius_mm=4,base_height_mm=725,
        shoulder_span_mm=2000,web_thickness_mm=65,upper_arch_sag_mm=160,lower_arch_clearance_mm=420,
        foot_center_x_mm=1010,foot_center_y_mm=358,foot_width_mm=80,foot_depth_mm=50,
        root_width_mm=160,root_depth_mm=60,root_height_mm=535,root_halfspan_mm=380,foot_section_corner_radius_mm=12)
    parts=[dict(part_id='F001',label='Rounded light wood tabletop',geometry='box',
        size=['width_mm','depth_mm','tabletop_thickness_mm'],position=[0,0,'height_mm-tabletop_thickness_mm/2'],
        corner_radius='tabletop_corner_radius_mm',edge_radius='tabletop_edge_radius_mm',
        material='solid_wood',color_rgb=[.68,.57,.36],evidence='Thin light wood top with rounded plan corners in the user reference views.')]
    # Closed cubic outline, viewed in X/Z. It carries the continuous concave
    # upper edge, shallow lower arch and the two recessed shoulder transitions.
    outline=[[-1,1],[-.97,1],[-.94,1],[-.90,1],
        [-.65,.83],[-.32,.78],[0,.78],[.32,.78],[.65,.83],[.90,1],
        [.94,1],[.97,1],[1,1],[.90,.90],[.59,.738],[.52,.662],
        [.50,.620],[.58,.550],[.65,.483],[.44,.510],[.26,.579],[0,.579],
        [-.26,.579],[-.44,.510],[-.65,.483],[-.58,.550],[-.50,.620],[-.52,.662],
        [-.59,.738],[-.90,.90],[-1,1]]
    outline=[[f'shoulder_span_mm*{x/2:.6f}',
        'base_height_mm-upper_arch_sag_mm' if x==0 and z==.78 else
        'lower_arch_clearance_mm' if x==0 and z==.579 else f'base_height_mm*{z:.6f}'] for x,z in outline]
    parts.append(dict(part_id='F002',label='Continuous concave sculpted central web',geometry='outline_panel',
        size=['shoulder_span_mm','base_height_mm','web_thickness_mm'],outline=outline,curve_mode='bezier',
        rotation_deg=[90,0,0],position=[0,0,0],material='solid_wood',color_rgb=[.15,.12,.075],
        evidence='Broad continuous dark web, concave top, lower arch and flowing shoulder recesses interpreted from the perspective and front reference views.'))
    for sx,sy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
        def x(value):return f'{sx}*({value})'
        def y(value):return f'{sy}*({value})'
        sections=[]
        for fraction in (0,.25,.5,.75,1):
            t=f'{fraction:g}'
            sections.append(dict(center=[x(f'foot_center_x_mm*(1-{t})+root_halfspan_mm*{t}'),
                y(f'foot_center_y_mm*(1-{t})'),f'root_height_mm*{t}'],
                width=f'foot_width_mm*(1-{t})+root_width_mm*{t}',
                depth=f'foot_depth_mm*(1-{t})+root_depth_mm*{t}',profile='rectangle',
                corner_radius='foot_section_corner_radius_mm'))
        parts.append(dict(part_id=f'F{len(parts)+1:03d}',label=('Front' if sy<0 else 'Rear')+(' left' if sx<0 else ' right')+' flowing tapered foot',
            geometry='loft',loft_mode='BOUNDED',size=['root_width_mm','root_depth_mm','root_height_mm'],sections=sections,
            material='solid_wood',color_rgb=[.15,.12,.075],
            evidence='One of four splayed, broad tapered feet. Rounded rectangular sections retain the reference blade faces and taper along a nearly straight centreline into the central web.'))
    return PhotoDesign(furniture_type='Table A curved four-foot dining table',summary='Local reconstruction from the supplied tabletop and sculptural-base reference views. Saved overall targets retained; photo identity requires human review.',
        values=[dict(key=k,value=v,explanation='Saved overall design target' if k in ('width_mm','depth_mm','height_mm') else 'Locally interpreted, editable estimate from the reference views') for k,v in values.items()],
        parts=parts,assumptions=['Overall targets retained at 2800 x 1100 x 750 mm from the existing project sample drawing.',
            'This is a locally authored review reconstruction, not a completed OpenAI photo analysis or AI visual approval.',
            'The primary JPG is stored in the project. Two further inline perspective/front views were interpreted visually from the user conversation.',
            'Base curves, cross-sections, material thicknesses, hidden joins and radii remain design estimates.'],
        unrepresented_features=['Exact wood grain, subtle carved relief and edge wear','Unmeasured hidden construction and joinery'])


def inputs(source):
    raw=copy.deepcopy(source.input_data)
    for key in ('engineering_reexport_of','engineering_edit','engineering_local_completion','analysis_workflow_id','analysis_sha256','dimension_fields','client_request_hash'):
        raw.pop(key,None)
    raw.update(output_scope='WHOLE_FURNITURE',sweep_frame_policy='WORLD_UP_V2',reference_views=[dict(image_id=raw['image_ids'][0],angle='perspective')],
        engineering_manual_reconstruction=dict(source_workflow_id=SOURCE,origin='LOCAL_REFERENCE_RECONSTRUCTION',
            source_reference_count_in_conversation=3,stored_original_image_count=1,ai_requests=0,visual_review_status='HUMAN_REVIEW_REQUIRED'),
        notes='Recreate the table from all supplied views: rounded thin top, broad continuous concave dark base, four flowing tapered feet. Local CAD construction because the backend OpenAI key is rejected.')
    return raw


def preview(plan,raw,config):
    from app.geometry_repairs import resolve_repaired_geometry
    from app.engineering_cad import geometry_checks,check_supplied_dimensions,render_preview
    resolved,spec,values,build,adjustments,repairs=resolve_repaired_geometry(plan,raw,'Table A / three reference views')
    checks=geometry_checks(build);checks['supplied_dimensions']=check_supplied_dimensions(build,raw['known_values'])
    if not checks.get('furniture_items_connected',checks['assembly_connected']):raise ValueError('Reconstructed table must be connected')
    OUTPUT.mkdir(parents=True,exist_ok=True)
    (OUTPUT/'plan.json').write_text(json.dumps(resolved.model_dump(mode='json'),indent=2),encoding='utf-8')
    (OUTPUT/'checks.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    for name,az,el in [('reference',-115,17),('front',-90,0),('side',0,0),('isometric',-60,20)]:
        (OUTPUT/(name+'.png')).write_bytes(render_preview(build,az,el,size=(1300,900)))
    print(json.dumps(dict(preview_directory=str(OUTPUT.resolve()),checks=checks,repairs=repairs)),flush=True)
    return resolved


def publish(plan,raw,db,config):
    from sqlalchemy import select
    from app.database import AgentWorkflow,Project,audit
    from app.engineering_schema import EngineeringDimensions,EngineeringAssembly,EngineeringReview
    from app.agents.orchestrator import run_workflow
    def freeze(value):return dict(payload=value,sha256=canonical_hash(value))
    with db.sessions.begin() as session:
        existing=session.scalar(select(AgentWorkflow).where(AgentWorkflow.project_id==PROJECT,AgentWorkflow.idempotency_key==KEY))
        if existing:
            print(json.dumps(dict(workflow_id=existing.id,status=existing.state,existing=True)),flush=True);return existing.id
        source=session.get(AgentWorkflow,SOURCE);project=session.get(Project,PROJECT)
        dims=EngineeringDimensions(values=plan.values,assumptions=plan.assumptions,
            appearance=dict(summary='Locally interpreted Table A shape contract from the user reference views',
                critical_features=[dict(feature_id='V001',label='Rounded thin top',visible_shape='Light wood rounded rectangular slab with a thin softened edge',relative_layout='Above the dark base'),
                    dict(feature_id='V002',label='Continuous sculpted web',visible_shape='Broad dark concave upper web and shallow lower arch',relative_layout='Between two high shoulders'),
                    dict(feature_id='V003',label='Four flowing feet',visible_shape='Four broad tapered feet flare along length and depth, with rounded ends and broad roots',relative_layout='Two front and two rear feet join the central web')],
                continuity=['Smooth interpreted base transitions; exact surface matching remains unverified'],
                symmetry='Approximate bilateral symmetry from the supplied front view',avoid_substitutions=['Straight stock legs','Separate narrow rods instead of broad blades']),
            reference_view=dict(azimuth_deg=-115,elevation_deg=17))
        assembly=EngineeringAssembly(furniture_type=plan.furniture_type,assumptions=plan.assumptions,
            parts=[dict(part_id=p.part_id,label=p.label,description='Internal solid of the reconstructed whole furniture',visible_evidence=p.evidence) for p in plan.parts])
        review=EngineeringReview(accepted=False,findings=['Local reference reconstruction. Compare the complete CAD with all three user reference views; exact photo matching has not been verified.'],
            limitations=['No new AI analysis or visual review was performed. Curves and unmeasured details are interpreted design estimates.'])
        checkpoints={key:freeze(value.model_dump(mode='json')) for key,value in dict(engineering_dimensions=dims,engineering_assembly=assembly,
            engineering_plan=plan,engineering_critic_0=review,engineering_visual_review_0=review).items()}
        row=AgentWorkflow(project_id=PROJECT,created_by=project.owner_id,mode='IMAGE_TO_CAD',input_data=raw,input_hash=canonical_hash(raw),
            model_config={**source.model_config,'vision':'gpt-6.1-sol','design':'gpt-6.1-sol','review':'gpt-6.1-sol','fallback':''},
            idempotency_key=KEY,checkpoints=checkpoints)
        session.add(row);session.flush();wid=row.id
        audit(session,project.owner_id,'local_reference_reconstruction_queued',wid,source_workflow_id=SOURCE,ai_requests=0)
    (OUTPUT/'receipt.json').write_text(json.dumps(dict(workflow_id=wid,project_id=PROJECT,ai_requests=0),indent=2))
    print(json.dumps(dict(workflow_id=wid,action='exporting_local_reference_reconstruction')),flush=True)
    run_workflow(wid,db,config)
    with db.sessions() as session:
        row=session.get(AgentWorkflow,wid)
        (OUTPUT/'workflow.json').write_text(json.dumps(dict(id=row.id,status=row.state,result=row.result,error=row.error),indent=2),encoding='utf-8')
        print(json.dumps(dict(workflow_id=wid,status=row.state,error=row.error,job_id=row.job_id)),flush=True)
        if row.state!='REVIEW_REQUIRED':raise SystemExit('Local export did not complete; inspect the saved workflow without resubmitting generation')
    return wid


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--publish',action='store_true');args=parser.parse_args()
    from app.config import Settings
    from app.database import Database,AgentWorkflow
    config=Settings();db=Database(config.database_url)
    try:
        with db.sessions() as session:
            source=session.get(AgentWorkflow,SOURCE)
            if not source or source.project_id!=PROJECT:raise SystemExit('The requested source project does not match')
            if canonical_hash(source.input_data)!=source.input_hash:raise SystemExit('Source workflow integrity failed')
            raw=inputs(source)
        plan=preview(design(),raw,config)
        if args.publish:publish(plan,raw,db,config)
    finally:db.engine.dispose()


if __name__=='__main__':main()
