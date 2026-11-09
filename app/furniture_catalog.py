"""Editable starting proportions, not anthropometric standards or measured sizes."""
from copy import deepcopy
from typing import Literal
from pydantic import Field, model_validator
from .models import FurnitureParameters


# identifier, label, family, width, depth, height, overrides
_ROWS = [
 ('dining_chair','Dining chair','chair',460,520,850,{}),
 ('armchair','Wooden armchair','chair',620,600,820,{'armrests':True,'curve_profile':'wraparound','seat_shape':'sculpted'}),
 ('lounge_chair','Lounge chair','chair',720,760,800,{'seat_height_mm':380,'armrests':True,'arm_height_mm':590}),
 ('office_chair','Office chair study','chair',650,650,1000,{'armrests':True,'seat_height_mm':470,'backrest':True}),
 ('accent_chair','Accent chair','chair',600,620,850,{'curve_profile':'arched','seat_shape':'ellipse'}),
 ('rocking_chair','Rocking chair study','chair',650,850,1000,{'armrests':True,'curve_profile':'arched'}),
 ('kids_chair','Child chair study','chair',320,350,580,{'seat_height_mm':300,'backrest':True,'leg_width_mm':28,'arm_height_mm':450}),
 ('bar_stool','Bar stool','stool',450,450,950,{'seat_height_mm':750,'backrest':True,'seat_shape':'ellipse'}),
 ('counter_stool','Counter stool','stool',440,440,850,{'seat_height_mm':650,'backrest':True}),
 ('footstool','Footstool','stool',420,350,350,{'seat_height_mm':350,'backrest':False}),
 ('dining_bench','Dining bench','bench',1400,420,450,{'backrest':False}),
 ('garden_bench','Garden bench','bench',1500,600,900,{'armrests':True,'back_slats':6}),
 ('dining_table','Dining table','table',1600,900,750,{}),
 ('round_table','Round dining table','table',1100,1100,750,{'top_shape':'ellipse','pedestal':True}),
 ('coffee_table','Coffee table','table',1100,600,420,{}),
 ('side_table','Side table','table',500,450,550,{}),
 ('console_table','Console table','table',1200,350,800,{}),
 ('writing_desk','Writing desk','table',1200,600,750,{}),
 ('office_desk','Office desk','table',1600,750,750,{}),
 ('standing_desk','Standing desk study','table',1400,700,1050,{}),
 ('dressing_table','Dressing table','table',1100,450,760,{}),
 ('bookshelf','Bookcase','storage',900,320,1800,{'shelves':4}),
 ('wardrobe','Wardrobe','storage',1600,600,2100,{'doors':2,'shelves':2,'compartments':2}),
 ('kitchen_base','Kitchen base cabinet','storage',800,600,900,{'doors':2,'shelves':1}),
 ('kitchen_wall','Kitchen wall cabinet','storage',800,350,720,{'doors':2,'shelves':1}),
 ('sideboard','Sideboard','storage',1600,450,800,{'doors':3,'shelves':1,'compartments':3}),
 ('tv_unit','TV unit','storage',1600,400,500,{'shelves':1,'compartments':3}),
 ('shoe_rack','Shoe cabinet','storage',900,350,1000,{'shelves':4}),
 ('nightstand','Bedside cabinet','storage',450,400,550,{'drawers':2,'shelves':0}),
 ('chest_of_drawers','Chest of drawers','storage',900,450,1100,{'drawers':4,'shelves':0}),
 ('filing_cabinet','Filing cabinet study','storage',500,600,1100,{'drawers':3,'shelves':0}),
 ('bathroom_vanity','Bathroom vanity','storage',900,500,850,{'doors':2,'shelves':1}),
 ('single_bed','Single bed frame','bed',1000,2050,950,{}),
 ('double_bed','Double bed frame','bed',1450,2050,950,{}),
 ('queen_bed','Queen bed frame','bed',1600,2100,1000,{}),
 ('king_bed','King bed frame','bed',1900,2100,1000,{}),
 ('bunk_bed','Bunk bed study','bed',1000,2050,1700,{}),
 ('two_seat_sofa','Two seat sofa','sofa',1700,850,850,{'seats':2,'armrests':True}),
 ('three_seat_sofa','Three seat sofa','sofa',2200,900,850,{'seats':3,'armrests':True}),
 ('loveseat','Loveseat','sofa',1400,800,820,{'seats':2,'armrests':True}),
 ('ottoman','Ottoman','sofa',800,600,430,{'seats':1,'backrest':False,'armrests':False,'seat_height_mm':430}),
 ('room_divider','Room divider','screen',1500,150,1800,{'back_slats':10}),
 ('coat_rack','Coat rack','rack',550,550,1750,{}),
]


def catalog():
    result=[]
    for key,name,family,w,d,h,overrides in _ROWS:
        params=FurnitureParameters(**overrides)
        result.append(dict(id=key,name=name,family=family,dimensions_mm=dict(width=w,depth=d,height=h),
            parameters=params.model_dump(mode='json'),dimension_basis='COMMON_DEFAULT_ESTIMATE',
            capabilities=['editable proportions','part previews','STEP / GLB / STL','DXF / PDF drawings']+
                (['curved rails','sculpted seats','tapered splayed legs'] if family in ('chair','stool','bench') else []),
            limitations=(['Mechanisms, castors and ergonomics require a separate engineered design.'] if key=='office_chair' else
                         ['Rocker stability and dynamic loads are unverified.'] if key=='rocking_chair' else
                         ['Guard rails, ladder, entrapment and load safety are unverified.'] if key=='bunk_bed' else [])))
    return result


def template(identifier):
    return next((deepcopy(row) for row in catalog() if row['id']==identifier),None)


def default_spec(identifier, **overrides):
    item=template(identifier)
    if not item: raise ValueError('Unknown furniture template')
    result=dict(project_name=item['name'],family=item['family'],furniture_template=identifier,
        width_mm=item['dimensions_mm']['width'],depth_mm=item['dimensions_mm']['depth'],height_mm=item['dimensions_mm']['height'],
        carcass_material='solid_wood',panel_thickness_mm=18,dimension_provenance='estimated',
        furniture_parameters=item['parameters'])
    return {**result,**overrides}
