"""Reopen and audit all actual drawing files, including orientation and units."""
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import ezdxf
from pypdf import PdfReader
from .cad_six_view_generator import VIEWS


def validate_drawings(output,manifest):
    output=Path(output);checks=[]
    for sheet in manifest['sheets']:
        if set(sheet['views'])!=set(VIEWS):raise ValueError('Every assembly and part requires all six named views')
        for name,view in sheet['views'].items():
            basis=VIEWS[name]
            if tuple(view['look_from'])!=basis.look_from or tuple(view['up'])!=basis.up or tuple(view['right'])!=basis.right:
                raise ValueError('Incorrect canonical orientation: '+name)
            if not np.array_equal(np.cross(view['right'],view['up']),view['look_from']):raise ValueError('Mirrored view')
            if view['visible_edges']<1:raise ValueError('Blank native projection')
            root=ET.parse(output/view['svg']).getroot()
            if not root.attrib.get('width','').endswith('mm'):raise ValueError('SVG units missing')
            if not {'VISIBLE','HIDDEN','CENTERLINES','DIMENSIONS','ANNOTATIONS'}<={node.attrib.get('id') for node in root.iter()}:
                raise ValueError('SVG layer contract failed')
            dxf=ezdxf.readfile(output/view['dxf'])
            if dxf.units!=4 or dxf.audit().has_errors:raise ValueError('DXF units/audit failed')
            if not {'VISIBLE','HIDDEN','CENTERLINES','DIMENSIONS','ANNOTATIONS'}<={layer.dxf.name for layer in dxf.layers}:
                raise ValueError('Drawing layer contract failed')
            if not dxf.modelspace().query('LINE ARC CIRCLE ELLIPSE SPLINE'):raise ValueError('DXF has no native curves')
            for d in view['dimensions']:
                if d['value_mm']<=0 or d['classification'] not in ('confirmed','derived','estimated'):raise ValueError('Invalid dimension evidence')
        pages=PdfReader(output/sheet['pdf']).pages
        if len(pages)!=sheet['pages']:raise ValueError('PDF page inventory mismatch')
        width,height=sheet['paper_mm'];pt=72/25.4
        for placement in sheet['placements']:
            if placement['x_pt']<0 or placement['y_pt']<0 or placement['x_pt']+placement['width_pt']>width*pt+.1 or placement['y_pt']+placement['height_pt']>height*pt+.1:
                raise ValueError('Drawing geometry clipped by PDF page')
        if any(p.get_contents() is None or not p.get_contents().get_data().strip() for p in pages):raise ValueError('Blank PDF page')
        checks.append(dict(part_id=sheet['part_id'],six_views=True,pdf_reopened=True,dxf_reopened=True,canonical_bases_verified=True,
                           projection_layout=manifest['projection'],geometry_clipping_check=True))
    return dict(status='validated',checks=checks,limits=['No automatic manufacturing approval','Native envelope dimensions only; feature dimensions require engineering review'])
