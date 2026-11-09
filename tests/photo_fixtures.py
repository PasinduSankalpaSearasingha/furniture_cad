"""Synthetic photo-plan fixtures; no actual image recognition is claimed here."""
from app.photo_schema import PhotoPart, PhotoDesign
from examples.ai_demo.mock_gateway import fixtures


def analysis_fixture():
    vision=fixtures()['FurnitureVisionResult']
    vision.update(category='curved wooden chair',observations=['SYNTHETIC TEST: curved rail and seat'],components=vision['components'][:4])
    fields=[('width_mm','Overall width',620),('depth_mm','Overall depth',600),('height_mm','Overall height',820),
        ('seat_height_mm','Seat height',450),('seat_thickness_mm','Seat thickness',30),('leg_width_mm','Leg width',40)]
    return dict(vision=vision,matching_inputs=[dict(key=k,label=label,unit='mm',estimated_value=value,
        explanation='SYNTHETIC TEST: unverified size suggestion') for k,label,value in fields],summary='Synthetic chair recognition and optional inputs')


def photo_plan_fixture(**overrides):
    values=dict(width_mm=620,depth_mm=600,height_mm=820,seat_height_mm=450,seat_thickness_mm=30,
        leg_width_mm=40,rail_diameter_mm=30,arm_height_mm=740)
    values.update(overrides)
    parts=[]
    def put(label,geometry,size,position=(0,0,0),**kw):
        parts.append(PhotoPart(part_id=f'F{len(parts)+1:03d}',label=label,geometry=geometry,size=list(size),
            position=list(position),material='solid_wood',color_rgb=[.55,.34,.18],evidence='SYNTHETIC TEST geometry',**kw))
    w,d='width_mm','depth_mm'
    outline=[['-width_mm/2',0],['-width_mm/2','depth_mm*.276'],['-width_mm*.276','depth_mm/2'],[0,'depth_mm/2'],
        ['width_mm*.276','depth_mm/2'],['width_mm/2','depth_mm*.276'],['width_mm/2',0],
        ['width_mm/2','-depth_mm*.276'],['width_mm*.276','-depth_mm/2'],[0,'-depth_mm/2'],
        ['-width_mm*.276','-depth_mm/2'],['-width_mm/2','-depth_mm*.276'],['-width_mm/2',0]]
    put('Photographed seat outline','outline_panel',[w,d,'seat_thickness_mm'],
        ['width_mm/2','depth_mm/2','seat_height_mm-seat_thickness_mm/2'],outline=outline,curve_mode='bezier',dish_depth=5)
    z='height_mm-rail_diameter_mm/2'
    put('Continuous curved back and arm rail','round_sweep',['rail_diameter_mm',1,1],path=[
        [30,'depth_mm*.30','arm_height_mm'],[30,'depth_mm-20','arm_height_mm'],['width_mm*.2','depth_mm-20',z],['width_mm/2','depth_mm-20',z],
        ['width_mm*.8','depth_mm-20',z],['width_mm-30','depth_mm-20','arm_height_mm'],['width_mm-30','depth_mm*.30','arm_height_mm']],curve_mode='bezier')
    for x,y in [('width_mm*.18','depth_mm*.2'),('width_mm*.82','depth_mm*.2'),('width_mm*.18','depth_mm*.8'),('width_mm*.82','depth_mm*.8')]:
        put('Curved tapered leg','loft',[40,40,'seat_height_mm-seat_thickness_mm'],sections=[
            dict(center=[x,y,0],width='leg_width_mm*.7',depth='leg_width_mm*.7'),
            dict(center=[x,y+'+12','(seat_height_mm-seat_thickness_mm)/2'],width='leg_width_mm*.85',depth='leg_width_mm*.85'),
            dict(center=[x,y,'seat_height_mm-seat_thickness_mm'],width='leg_width_mm',depth='leg_width_mm')])
    # A separate test-defined support reaches the rail and is contoured by contact trim.
    put('Center back support','box',[22,22,'height_mm-seat_height_mm-15'],
        ['width_mm/2','depth_mm-20','(height_mm+seat_height_mm-15)/2'])
    return PhotoDesign(furniture_type='synthetic curved chair',summary='Synthetic individualized outline and curved member plan',
        values=[dict(key=k,value=v,explanation='Synthetic test value') for k,v in values.items()],parts=parts,
        assumptions=['SYNTHETIC TEST: estimated geometry, not photograph analysis'],unrepresented_features=['Unseen joints'])
