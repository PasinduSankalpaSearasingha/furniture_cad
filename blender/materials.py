"""Procedural PBR materials. No external texture licences or downloads are implied."""
import bpy

WOOD={'teak':(.32,.16,.065),'oak':(.57,.36,.16),'walnut':(.19,.075,.028),'mahogany':(.29,.065,.028),
      'plywood':(.62,.43,.23),'mdf':(.43,.32,.20),'laminated_mdf':(.35,.24,.13)}
METAL={'brushed_metal':(.45,.47,.50),'chrome':(.66,.68,.71),'matte_black':(.018,.02,.023),'aluminum':(.65,.67,.69)}


def make_material(part,finish):
    category=part['material']
    if finish=='source':
        finish={'solid_wood':'oak','metal':'brushed_metal','glass':'clear_glass','plywood':'plywood',
                'mdf':'mdf','foam':'foam','fabric':'fabric','plastic':'plastic'}.get(category,'oak')
        color=part.get('color_rgb') or WOOD.get(finish,(.48,.31,.16))
    else:
        # A wood finish cannot turn the model's glass or hardware into timber.
        if category=='glass' and finish not in ('clear_glass','frosted_glass','tinted_glass'): finish='clear_glass'
        if category=='metal' and finish not in METAL: finish='brushed_metal'
        color=WOOD.get(finish,METAL.get(finish,(.67,.68,.65)))
    mat=bpy.data.materials.new(part['part_id']+'_'+finish);mat.use_nodes=True
    nodes=mat.node_tree.nodes;links=mat.node_tree.links;shader=nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value=(*color,1)
    shader.inputs['Roughness'].default_value=.36
    if finish in ('clear_glass','frosted_glass','tinted_glass'):
        shader.inputs['Base Color'].default_value=(.80,.91,.88,1) if finish=='tinted_glass' else (.97,.99,1,1)
        shader.inputs['Transmission Weight'].default_value=1
        shader.inputs['IOR'].default_value=1.5
        shader.inputs['Roughness'].default_value=.28 if finish=='frosted_glass' else .045
    elif finish in METAL:
        shader.inputs['Metallic'].default_value=1
        shader.inputs['Roughness'].default_value=.08 if finish=='chrome' else .28
    elif finish in WOOD:
        coord=nodes.new('ShaderNodeTexCoord');mapping=nodes.new('ShaderNodeVectorMath');mapping.operation='MULTIPLY'
        axis=part.get('grain_axis','none')
        direction=part.get('length_axis',[1,0,0])
        idx=max(range(3),key=lambda i:abs(direction[i])) if axis!='none' else max(range(3),key=lambda i:part['size_xyz_mm'][i])
        scales=[28,28,28];scales[idx]=1.2
        mapping.inputs[1].default_value=scales;links.new(coord.outputs['Generated'],mapping.inputs[0])
        noise=nodes.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=3
        noise.inputs['Detail'].default_value=4;noise.inputs['Roughness'].default_value=.72
        links.new(mapping.outputs['Vector'],noise.inputs['Vector'])
        ramp=nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].position=.20;ramp.color_ramp.elements[1].position=.8
        ramp.color_ramp.elements[0].color=(*(c*.52 for c in color),1)
        ramp.color_ramp.elements[1].color=(*(min(1,c*1.25) for c in color),1)
        links.new(noise.outputs['Fac'],ramp.inputs[0]);links.new(ramp.outputs['Color'],shader.inputs['Base Color'])
        bump=nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.17;bump.inputs['Distance'].default_value=.00012
        links.new(noise.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs['Normal'],shader.inputs['Normal'])
        rough=nodes.new('ShaderNodeMapRange');rough.inputs['To Min'].default_value=.26;rough.inputs['To Max'].default_value=.48
        links.new(noise.outputs['Fac'],rough.inputs['Value']);links.new(rough.outputs['Result'],shader.inputs['Roughness'])
        shader.inputs['Coat Weight'].default_value=.15
    elif finish=='painted_mdf':
        shader.inputs['Roughness'].default_value=.3;shader.inputs['Coat Weight'].default_value=.2
    else: shader.inputs['Roughness'].default_value=.8 if finish in ('fabric','foam') else .35
    mat['finish']=finish;mat['texture_source']='Procedural Blender shader nodes; no external textures'
    return mat
