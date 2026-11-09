"""Native curve projections from the accepted solids; no model reconstruction."""
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import build123d as bd
import cadquery as cq
import ezdxf
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

ENGINE = 'build123d'
MM_TO_PT = 72 / 25.4


class Projection:
    def __init__(self, shapes, axes=None, *, look_from=None, look_up=None):
        if axes is not None:
            if look_from is not None or look_up is not None:raise ValueError('Choose drawing axes or a camera direction')
            x = cq.Vector(*[int(i == axes[0]) for i in range(3)])
            y = cq.Vector(*[int(i == axes[1]) for i in range(3)])
            look_from,look_up=x.cross(y).toTuple(),y.toTuple()
        elif look_from is None or look_up is None:
            raise ValueError('An explicit camera direction and up vector are required')
        source = cq.Compound.makeCompound(list(shapes))
        drawing = bd.Drawing(bd.Shape(source.wrapped), look_at=(0,0,0),
            look_from=look_from, look_up=look_up)
        self.discarded_empty_edges=0
        for target,source in (('visible',drawing.visible_lines),('hidden',drawing.hidden_lines)):
            edges=source.edges();lengths=[edge.length for edge in edges]
            if not all(math.isfinite(length) for length in lengths):raise ValueError('A drawing edge has invalid length')
            retained=[edge for edge,length in zip(edges,lengths) if length>1e-9]
            self.discarded_empty_edges+=len(edges)-len(retained)
            # HLR occasionally emits a spline whose trimmed parameter interval
            # collapses to zero. It has no drawable geometry, but OCCT's SVG
            # Bezier converter rejects it. Keep every nonempty native curve.
            setattr(self,target,source if len(retained)==len(edges) else bd.Compound(retained))
        if not self.visible.edges():
            raise ValueError('The solid produced no visible drawing edges')
        if len(self.visible.edges()) + len(self.hidden.edges()) > 20000:
            raise ValueError('Drawing projection exceeds its edge budget')
        self._svg_cache = {}
        self.bounds = bd.Compound([self.visible,self.hidden]).bounding_box()

    def svg_drawing(self,plot_scale=MM_TO_PT):
        if plot_scale not in self._svg_cache:
            exporter = bd.ExportSVG(unit=bd.Unit.MM, margin=0, fit_to_stroke=False, precision=9,
                line_weight=.18,scale=plot_scale/MM_TO_PT)
            exporter.add_layer('CAD')
            exporter.add_layer('HIDDEN',line_color=(125,125,125),line_type=bd.LineType.HIDDEN)
            exporter.add_shape(self.hidden,layer='HIDDEN')
            exporter.add_shape(self.visible,layer='CAD')
            with TemporaryDirectory(prefix='furniture-vector-') as temporary:
                path = Path(temporary)/'view.svg'
                exporter.write(path)
                markup = path.read_text(encoding='utf-8')
                drawing = svg2rlg(str(path))
            if drawing is None:
                raise ValueError('The native curve projection could not be rendered')
            self._svg_cache[plot_scale] = markup,drawing
        return self._svg_cache[plot_scale][1]

    def svg_element(self,plot_scale=MM_TO_PT):
        import xml.etree.ElementTree as ET
        self.svg_drawing(plot_scale)
        return ET.fromstring(self._svg_cache[plot_scale][0])

    def draw_pdf(self, canvas, ox, oy, scale):
        canvas.saveState()
        canvas.translate(ox + self.bounds.min.X*scale, oy + self.bounds.min.Y*scale)
        renderPDF.draw(self.svg_drawing(scale),canvas,0,0)
        canvas.restoreState()


def write_dxf(path, placements):
    """Write curves as LINE/ARC/CIRCLE/ELLIPSE/SPLINE, retaining hidden layers."""
    exporter = bd.ExportDXF(version='R2010',unit=bd.Unit.MM)
    exporter.add_layer('CAD')
    exporter.add_layer('HIDDEN',color=bd.ColorIndex.GRAY,line_type=bd.LineType.HIDDEN)
    for projection, x, y in placements:
        placement = bd.Location((x,y,0))
        exporter.add_shape(projection.visible.moved(placement),layer='CAD')
        exporter.add_shape(projection.hidden.moved(placement),layer='HIDDEN')
    exporter.write(path)
    document = ezdxf.readfile(path)
    document.units = 4
    for name,color in [('DIMENSIONS',2),('NOTES',3)]:
        if name not in document.layers:document.layers.new(name,dxfattribs={'color':color})
    if 'EZDXF' not in document.dimstyles:document.dimstyles.new('EZDXF')
    return document
