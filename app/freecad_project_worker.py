"""FreeCAD embedded Python worker; deliberately never imports CadQuery/OCP."""
import json
from pathlib import Path
import sys
import traceback
import ast
import operator
import FreeCAD as App
import Part


def native_box(doc,parameters,part,values):
    """Use Sketcher+Pad for an untrimmed, axis-aligned plain rectangular member."""
    import Sketcher
    def numeric(value):
        if isinstance(value,(int,float)):return float(value)
        def read(node):
            if isinstance(node,ast.Constant):return float(node.value)
            if isinstance(node,ast.Name):return values[node.id]
            if isinstance(node,ast.UnaryOp):return read(node.operand)*(-1 if isinstance(node.op,ast.USub) else 1)
            if isinstance(node,ast.BinOp):return {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}[type(node.op)](read(node.left),read(node.right))
            raise ValueError('Unsupported arithmetic')
        return read(ast.parse(value,mode='eval').body)
    if part['geometry']!='box' or any(part.get(k) for k in ('features','surface_grid','surface_dishes','surface_nurbs')):return None
    def parameterized(value):return isinstance(value,str) and any(isinstance(n,ast.Name) for n in ast.walk(ast.parse(value,mode='eval')))
    if any(parameterized(v) for v in [part.get(k,0) for k in ('corner_radius','edge_radius','dish_depth')]+part.get('rotation_deg',[0,0,0])):return None
    if any(abs(numeric(part.get(k,0)))>1e-9 for k in ('corner_radius','edge_radius','dish_depth')):return None
    if any(abs(numeric(v))>1e-9 for v in part.get('rotation_deg',[0,0,0])):return None
    size=[numeric(v) for v in part['size']]
    source=Part.Shape();source.read(str(Path(parameters.CacheDirectory)/(part['part_id']+'.brep')))
    if abs(source.Volume-size[0]*size[1]*size[2])>max(.001,source.Volume*1e-8):return None
    def expression(value):
        tree=ast.parse(str(value),mode='eval')
        class Names(ast.NodeTransformer):
            def visit_Name(self,node):return ast.copy_location(ast.Attribute(value=ast.Name(id='Parameters',ctx=ast.Load()),attr=node.id,ctx=ast.Load()),node)
        return ast.unparse(Names().visit(tree))
    body=doc.addObject('PartDesign::Body',part['part_id'])
    sketch=doc.addObject('Sketcher::SketchObject',part['part_id']+'_Sketch');body.addObject(sketch)
    sx,sy,sz=size
    for a,b in [((0,0),(sx,0)),((sx,0),(sx,sy)),((sx,sy),(0,sy)),((0,sy),(0,0))]:
        sketch.addGeometry(Part.LineSegment(App.Vector(*a,0),App.Vector(*b,0)),False)
    for i in (0,2):sketch.addConstraint(Sketcher.Constraint('Horizontal',i))
    for i in (1,3):sketch.addConstraint(Sketcher.Constraint('Vertical',i))
    for i in range(4):sketch.addConstraint(Sketcher.Constraint('Coincident',i,2,(i+1)%4,1))
    sketch.addConstraint(Sketcher.Constraint('Coincident',0,1,-1,1))
    for edge,length,value in ((0,sx,part['size'][0]),(1,sy,part['size'][1])):
        index=sketch.addConstraint(Sketcher.Constraint('Distance',edge,length))
        sketch.setExpression(f'Constraints[{index}]',expression(value))
    pad=body.newObject('PartDesign::Pad',part['part_id']+'_Pad');pad.Profile=sketch;pad.Length=sz
    pad.setExpression('Length',expression(part['size'][2]))
    for axis,value,position in zip('xyz',part['size'],part.get('position',[0,0,0])):
        sketch.setExpression('Placement.Base.'+axis,'-('+expression(value)+')/2')
        body.setExpression('Placement.Base.'+axis,expression(position))
    body.addProperty('App::PropertyBool','NativeSketchPad','Construction');body.NativeSketchPad=True
    return body


def check_source(obj):
    reference=Part.Shape();suffix='.local.brep' if obj.UseLocalFrame else '.brep'
    reference.read(str(Path(obj.Parameters.CacheDirectory)/(obj.PartID+suffix)))
    actual=obj.Shape
    if not actual.isValid() or len(actual.Solids)!=1:raise ValueError('Invalid regenerated part '+obj.PartID)
    if abs(actual.Volume-reference.Volume)>max(.01,reference.Volume*1e-7):raise ValueError('Native source volume mismatch '+obj.PartID)
    for axis in ('XMin','XMax','YMin','YMax','ZMin','ZMax'):
        if abs(getattr(actual.BoundBox,axis)-getattr(reference.BoundBox,axis))>1e-4:
            raise ValueError('Native source placement/envelope mismatch '+obj.PartID)


def export(request_path):
    request=json.loads(Path(request_path).read_text(encoding='utf-8'))
    root=Path(request['output']);sys.path.insert(0,str(root))
    from furniture_cad_proxy import ParametersProxy,PartProxy
    plan=request['design'];doc=App.newDocument('Furniture')
    parameters=doc.addObject('App::FeaturePython','Parameters');ParametersProxy(parameters)
    for kind,name,value in [('App::PropertyString','DesignJSON',json.dumps(plan)),
                            ('App::PropertyStringList','ParameterKeys',[v['key'] for v in plan['values']]),
                            ('App::PropertyPath','ProjectRoot',request['project_root']),
                            ('App::PropertyFile','PythonExecutable',request['python']),
                            ('App::PropertyPath','CacheRoot',str(root)),
                            ('App::PropertyPath','CacheDirectory',''),('App::PropertyString','LastError','')]:
        parameters.addProperty(kind,name,'Regeneration');setattr(parameters,name,value)
    parameters.setEditorMode('DesignJSON',1);parameters.setEditorMode('ParameterKeys',1)
    for v in plan['values']:
        parameters.addProperty('App::PropertyFloat',v['key'],'Dimensions',v['explanation'])
        setattr(parameters,v['key'],v['value'])
        if v['key'] in request['locked']:parameters.setEditorMode(v['key'],1)
    # Initial authoritative B-Reps also prove eligibility for native Pad members.
    parameters.Proxy.execute(parameters)
    docpart={}
    for part in plan['parts']:
        feature=native_box(doc,parameters,part,{v['key']:v['value'] for v in plan['values']})
        native=feature is not None
        docpart[part['part_id']]=native
        if feature is None:
            feature=doc.addObject('PartDesign::FeaturePython',part['part_id']);PartProxy(feature)
        feature.Label=part['part_id']+' '+part['label']
        feature.addProperty('App::PropertyLink','Parameters','Source');feature.Parameters=parameters
        feature.addProperty('App::PropertyString','PartID','Source');feature.PartID=part['part_id'];feature.setEditorMode('PartID',1)
        if not native:
            feature.addProperty('App::PropertyString','ConstructionJSON','Construction');feature.ConstructionJSON=json.dumps(part)
        feature.addProperty('App::PropertyBool','UseLocalFrame','Placement');feature.UseLocalFrame=False
        feature.addProperty('App::PropertyString','Evidence','Review');feature.Evidence=part['evidence']
        # Native shape is rebuilt from the exact same declarative source for all outputs.
    doc.recompute()
    errors=[obj.Name for obj in doc.Objects if 'Invalid' in obj.State]
    if errors or parameters.LastError:raise RuntimeError('FreeCAD recompute failed: '+str(errors)+' '+parameters.LastError)
    checks={}
    for obj in doc.Objects:
        if hasattr(obj,'PartID'):
            check_source(obj)
            if not obj.Shape.isValid() or len(obj.Shape.Solids)!=1:raise ValueError('Invalid FreeCAD solid '+obj.Name)
            checks[obj.PartID]=dict(volume_mm3=obj.Shape.Volume,bounds_mm=[obj.Shape.BoundBox.XLength,obj.Shape.BoundBox.YLength,obj.Shape.BoundBox.ZLength])
    destination=root/'assembly.FCStd';doc.saveAs(str(destination))
    # Verify actual parameter regeneration, relative placement and reopened persistence.
    values={v['key']:v['value'] for v in plan['values']}
    key=next((k for k in ('width_mm','depth_mm','height_mm') if k not in request['locked']),None)
    validation=dict(status='no_unlocked_envelope_parameter',changed_parameter=None)
    if key:
        before={o.PartID:(o.Shape.Volume,list(o.Shape.Solids[0].CenterOfMass)) for o in doc.Objects if hasattr(o,'PartID')}
        original=getattr(parameters,key);setattr(parameters,key,float(original)*1.02);doc.recompute()
        after={o.PartID:(o.Shape.Volume,list(o.Shape.Solids[0].CenterOfMass)) for o in doc.Objects if hasattr(o,'PartID')}
        for obj in doc.Objects:
            if hasattr(obj,'PartID'):check_source(obj)
        affected=[pid for pid in before if before[pid]!=after[pid]]
        if not affected or parameters.LastError:raise ValueError('Unlocked parameter did not regenerate native geometry')
        changed=Path(parameters.CacheDirectory)
        if not (changed/'assembly.step').is_file():raise ValueError('Edited STEP was not regenerated')
        validation=dict(status='parameter_regenerated',changed_parameter=key,before=values[key],after=values[key]*1.02,
                        affected_parts=affected,edited_step_regenerated=True,
                        edited_specification=json.loads((changed/'feature_specification.json').read_text(encoding='utf-8')))
        setattr(parameters,key,original);doc.recompute();doc.save()
    # Exercise an actual curved construction control, not just its placement.
    curve_validation=None
    for part in plan['parts']:
        if part['geometry'] not in ('round_sweep','ellipse_sweep','rect_sweep','profile_sweep','nurbs_panel'):continue
        feature=doc.getObject(part['part_id']);original=feature.ConstructionJSON;edited=json.loads(original)
        if edited.get('path'):
            index=len(edited['path'])//2;value=edited['path'][index][1]
            edited['path'][index][1]=f'({value})+2';control=f'path[{index}][1]'
        elif edited.get('surface_nurbs'):
            grid=edited['surface_nurbs']['control_points'];i=len(grid)//2;j=len(grid[0])//2
            grid[i][j][2]=f'({grid[i][j][2]})+2';control=f'surface_nurbs.control_points[{i}][{j}][2]'
        else:continue
        before=feature.Shape.Volume
        feature.ConstructionJSON=json.dumps(edited);parameters.touch();doc.recompute()
        for obj in doc.Objects:
            if hasattr(obj,'PartID'):check_source(obj)
        if parameters.LastError or not feature.Shape.isValid():raise ValueError('Curved construction edit failed')
        if abs(feature.Shape.Volume-before)<1e-7:raise ValueError('Curved control did not change native geometry')
        curve_validation=dict(part_id=part['part_id'],control=control,valid_after_edit=True,
            edited_specification=json.loads((Path(parameters.CacheDirectory)/'feature_specification.json').read_text(encoding='utf-8')))
        feature.ConstructionJSON=original;parameters.touch();doc.recompute();doc.save();break
    App.closeDocument(doc.Name)
    reopened=App.openDocument(str(destination));reopened.recompute()
    if any(not o.Shape.isValid() for o in reopened.Objects if hasattr(o,'PartID')):raise ValueError('Reopened FCStd invalid')
    # Per-part files retain the full parameter context and only the selected visible part.
    for part in plan['parts']:
        pid=part['part_id'];partdir=root/'parts'/pid;partdir.mkdir(parents=True,exist_ok=True)
        single=App.newDocument('Part_'+pid)
        selected=reopened.getObject(pid)
        # Copy dependency graph including root and parametric part implementation.
        copied=single.copyObject(selected,True);copied.UseLocalFrame=True;copied.touch()
        if getattr(copied,'NativeSketchPad',False):
            for axis in 'xyz':copied.setExpression('Placement.Base.'+axis,None)
            copied.Placement.Base=App.Vector(0,0,0)
        single.recompute();single.saveAs(str(partdir/'part.FCStd'));App.closeDocument(single.Name)
        verify=App.openDocument(str(partdir/'part.FCStd'));verify.recompute()
        saved=verify.getObject(pid)
        check_source(saved)
        if not saved.Shape.isValid() or len(saved.Shape.Solids)!=1:raise ValueError('Individual FCStd failed reimport: '+pid)
        checks[pid]['individual_fcstd_reopened']=True
        checks[pid]['individual_frame']='local_stock_frame'
        App.closeDocument(verify.Name)
    App.closeDocument(reopened.Name)
    native_count=sum(docpart.values())
    return dict(freecad_version=App.Version()[:3],parts=checks,editability=validation,curved_editability=curve_validation,
                representation='Native Sketcher/Pad boxes and FeaturePython B-Reps driven by the same declarative source',
                requires_project_venv=True,native_sketch_history=native_count>0,
                feature_types={pid:('Sketcher+PartDesign::Pad' if docpart.get(pid,False) else 'FeaturePython') for pid in checks})


if __name__=='__main__':
    path=Path(sys.argv[1]);result=path.with_name('freecad_result.json')
    try:result.write_text(json.dumps(export(path),indent=2),encoding='utf-8')
    except Exception:
        result.write_text(json.dumps(dict(error=traceback.format_exc())),encoding='utf-8');raise
