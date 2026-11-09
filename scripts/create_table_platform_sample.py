"""Fresh table reconstruction through the platform's real CAD exporters.

No reference PDF is read or copied. No provider/API call is made. The declared
profile is a manual interpretation for comparison, not an end-to-end AI test.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.photo_schema import PhotoDesign
from app.models import CabinetSpec
from app.photo_cad import create_photo_furniture
from app.engineering_cad import geometry_checks,check_supplied_dimensions,build_engineering_package
from app.config import Settings


def table_plan():
    from scipy.optimize import brentq
    width,depth,height,top=2800,1100,750,45
    footprint_x,footprint_y,thickness=2100,766,55
    angle=brentq(lambda t:(footprint_x-thickness*math.sin(t))*math.tan(t)+thickness*math.cos(t)-footprint_y,0,.7)
    diagonal=(footprint_x-thickness*math.sin(angle))/math.cos(angle)
    values=dict(width_mm=width,depth_mm=depth,height_mm=height,top_thickness_mm=top,
        base_length_mm=footprint_x,base_depth_mm=footprint_y,base_panel_thickness_mm=thickness,
        top_corner_radius_mm=100,base_diagonal_mm=diagonal,base_angle_deg=math.degrees(angle))
    # Cubic Bezier outlines in a local diagonal / vertical panel plane. The
    # profile follows the photographed hourglass form; its control points are
    # explicitly estimated instead of extracted from the reference drawing.
    segments=[
        [(-.4,.02),(.4,.02),(1,1)],
        [(1,1),(1,.95),(1,.915)],
        [(.65,.20),(.14,.06),(.14,0)],
        [(.14,-.06),(.65,-.20),(1,-.915)],
        [(1,-.95),(1,-1),(1,-1)],
        [(.4,-.02),(-.4,-.02),(-1,-1)],
        [(-1,-1),(-1,-.95),(-1,-.915)],
        [(-.65,-.20),(-.14,-.06),(-.14,0)],
        [(-.14,.06),(-.65,.20),(-1,.915)],
        [(-1,.95),(-1,1),(-1,1)]]
    whole=[(-1,1),*[p for segment in segments for p in segment]]
    positive=[(0,.265),(.35,.265),(.7,.51),(1,1),
        *[p for segment in segments[1:5] for p in segment],
        (.7,-.51),(.35,-.265),(0,-.265),(0,-.09),(0,.09),(0,.265)]
    def outline(points):
        return [[f'{x:.12g}*base_diagonal_mm/2',f'{y:.12g}*(height_mm-top_thickness_mm)/2'] for x,y in points]
    parts=[dict(part_id='F001',label='Rounded timber tabletop',geometry='box',
        size=['width_mm','depth_mm','top_thickness_mm'],position=[0,0,'height_mm-top_thickness_mm/2'],
        corner_radius='top_corner_radius_mm',edge_radius=2,material='solid_wood',color_rgb=[.63,.56,.36],
        evidence='Manually specified from photo; 2800 x 1100 x 750 and 45 mm top are reference-drawing target values. Corner and edge radii estimated.')]
    for pid,label,points,rotation in [('F002','Continuous sculpted diagonal base',whole,'base_angle_deg'),
        ('F003','Cross-base positive wing',positive,'-base_angle_deg'),
        ('F004','Cross-base negative wing',[(-x,y) for x,y in positive],'-base_angle_deg')]:
        parts.append(dict(part_id=pid,label=label,geometry='outline_panel',
            size=['base_diagonal_mm','height_mm-top_thickness_mm','base_panel_thickness_mm'],
            position=[0,0,'(height_mm-top_thickness_mm)/2'],rotation_deg=[90,0,rotation],
            material='solid_wood',color_rgb=[.17,.16,.10],outline=outline(points),curve_mode='bezier',
            evidence='Manually estimated sculpted base silhouette. Cross-base segmentation, 55 mm section and joints are assumed for this CAD comparison.'))
    known={'width_mm','depth_mm','height_mm','top_thickness_mm','base_length_mm','base_depth_mm'}
    plan=PhotoDesign(furniture_type='Sculpted cross-base dining table',
        summary='Manual photo reconstruction built with the platform CAD pipeline. Reference target dimensions; inferred base profile and construction.',
        values=[dict(key=k,value=v,explanation='Reference drawing target dimension' if k in known else 'Manual inferred profile parameter, unverified') for k,v in values.items()],
        parts=parts,assumptions=['No OpenAI image analysis was run: the configured key was rejected.',
            'This manual plan demonstrates the CAD/export stack, not the quality of automatic photo recognition.',
            'Base curve, material, panel thickness, corner radii and component split are estimates.',
            'No original drawing geometry is read, traced or copied; CAD is rebuilt from the declared plan.',
            'Joinery, load capacity and manufacturing tolerances remain unverified.'])
    return plan,known


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--geometry-only',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    plan,known=table_plan()
    values={v.key:v.value for v in plan.values}
    spec=CabinetSpec(project_name='Table A - platform reconstruction',width_mm=values['width_mm'],
        depth_mm=values['depth_mm'],height_mm=values['height_mm'],panel_thickness_mm=45,
        carcass_material='solid_wood',family='photo',photo_design=plan,revision=1)
    build=create_photo_furniture(spec);checks=geometry_checks(build)
    checks['supplied_dimensions']=check_supplied_dimensions(build,[dict(key=k,value=values[k]) for k in ('width_mm','depth_mm','height_mm')])
    if not checks['assembly_connected']:raise ValueError('The reconstructed table is disconnected')
    print(json.dumps(dict(stage='geometry_validated',part_count=len(build.parts),contacts=checks['contacts'])),flush=True)
    if args.geometry_only:return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output=root/'outputs'/('table-platform-generated-'+stamp+'-'+uuid4().hex[:6])
    evidence=dict(generation_method='MANUAL_PHOTO_RECONSTRUCTION_THROUGH_PLATFORM_CAD',
        automatic_photo_analysis=False,paid_ai_calls=0,
        reference_photo_sha256=hashlib.sha256((root/'data/reference_samples/Momentro/Table/Table A/Referance Image - 4.JPG').read_bytes()).hexdigest(),
        value_provenance={k:dict(source='SAMPLE_DRAWING' if k in known else 'MANUAL_ESTIMATE',value=v,
            basis='REFERENCE_TARGET_DIMENSION' if k in known else 'MANUAL_PHOTO_INTERPRETATION') for k,v in values.items()},
        assumptions=plan.assumptions)
    config=Settings(ai_enabled=False,local_workspace=True,executor='local',
        techdraw_docker_image='fiske-techdraw:0.20.2',techdraw_timeout_seconds=180)
    build_engineering_package(config,spec,output,evidence,source_build=build,source_checks=checks,
        progress=lambda stage,value:print(json.dumps(dict(stage=stage,progress=value)),flush=True))
    # Produce the requested four-view comparison from the same reopened STEP.
    from app.table_comparison_sheet import create_comparison_sheet
    create_comparison_sheet(output)
    print(json.dumps(dict(status='COMPLETED',output=str(output),paid_ai_calls=0),indent=2),flush=True)


if __name__=='__main__':main()
