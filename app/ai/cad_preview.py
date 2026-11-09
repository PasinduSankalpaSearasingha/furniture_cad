"""Rasterize actual reopened STEP geometry. This is not an AI-generated image."""
from io import BytesIO
import json
import os
import tempfile
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'furniture-matplotlib'))
import cadquery as cq
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from app.geometry import exact_bounds


def render_step_preview(directory):
    model = cq.importers.importStep(str(directory/'cabinet.step'))
    solids = model.solids().vals()
    parts = json.loads((directory/'parts_registry.json').read_text(encoding='utf-8'))
    figure = Figure(figsize=(7,8),dpi=140,facecolor='#f5f2ed')
    FigureCanvasAgg(figure)
    axes = figure.add_subplot(111,projection='3d',computed_zorder=False)
    colors = {'solid_wood':'#91603b','plywood':'#bc9767','mdf':'#b4b2aa','glass':'#b6e0eb',
              'metal':'#8b9195','plastic':'#b3b6b6','foam':'#d1cbbf','fabric':'#a5a696'}
    for solid in solids:
        bb = exact_bounds(solid)
        center = ((bb.xmin+bb.xmax)/2,(bb.ymin+bb.ymax)/2,(bb.zmin+bb.zmax)/2)
        part = min(parts,key=lambda p:sum((a-b)**2 for a,b in zip(p['center_xyz_mm'],center)))
        vertices,triangles = solid.tessellate(1.0,0.2)
        points = [v.toTuple() for v in vertices]
        mesh = [[points[i] for i in triangle] for triangle in triangles]
        axes.add_collection3d(Poly3DCollection(mesh,facecolor=part.get('color_rgb') or colors[part['material']],edgecolor='#4f4439',
            linewidth=0.12,alpha=0.28 if part['material']=='glass' else 1))
    bb = exact_bounds(model.val())
    axes.set_xlim(bb.xmin,bb.xmax)
    axes.set_ylim(bb.ymin,bb.ymax)
    axes.set_zlim(bb.zmin,bb.zmax)
    axes.set_box_aspect((bb.xlen,bb.ylen,bb.zlen))
    axes.view_init(elev=17,azim=-68)
    axes.set_axis_off()
    figure.text(0.5,0.06,'CAD-DERIVED PREVIEW — REVIEW ONLY',ha='center',fontsize=11,color='#4f4439')
    result = BytesIO()
    figure.savefig(result,format='png',facecolor=figure.get_facecolor())
    figure.clear()
    return result.getvalue()
