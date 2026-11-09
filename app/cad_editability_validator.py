"""Independently rebuild edited declarations and regenerate their six views."""
from pathlib import Path
import xml.etree.ElementTree as ET
import cadquery as cq
from .photo_schema import PhotoDesign
from .mesh_to_cad import build_native
from .cad_six_view_generator import six_views


def validate_edited_views(specification,output):
    plan=PhotoDesign.model_validate(specification);build=build_native(plan)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'edited_assembly.step';build.assembly.save(str(path))
    reopened=cq.importers.importStep(str(path)).val()
    if not reopened.isValid() or len(reopened.Solids())!=len(build.parts):raise ValueError('Edited STEP did not reopen correctly')
    views=six_views([s.val() for s in build.shapes.values()])
    for name,view in views.items():
        if not view.visible.edges():raise ValueError('Edited view is blank')
        ET.ElementTree(view.svg_element()).write(output/(name+'.svg'),encoding='utf-8',xml_declaration=True)
    return dict(edited_step_reopened=True,six_views_regenerated=list(views),part_count=len(build.parts),
                all_native_solids_valid=True)
