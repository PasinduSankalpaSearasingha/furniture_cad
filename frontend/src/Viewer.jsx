import React,{Suspense,useEffect,useMemo,useRef,useState} from 'react';
import {Canvas,useThree} from '@react-three/fiber';
import {OrbitControls,Grid,Html,useGLTF} from '@react-three/drei';
import {Box3,Vector3,Quaternion} from 'three';

function Model({url,hidden,selected,onSelect,explode,dimensions,view,controls,onReady}) {
  const {scene}=useGLTF(url,false,false);
  const {camera,invalidate}=useThree();
  const model=useMemo(()=>{
    const clone=scene.clone(true);
    clone.traverse(o=>{if(o.isMesh){o.material=Array.isArray(o.material)?o.material.map(m=>m.clone()):o.material.clone();o.userData.basePosition=o.position.clone();o.userData.baseEmissions=(Array.isArray(o.material)?o.material:[o.material]).map(m=>({color:m.emissive?.clone(),intensity:m.emissiveIntensity}));}});
    return clone;
  },[scene]);
  const box=useMemo(()=>new Box3().setFromObject(model),[model]);
  const center=useMemo(()=>box.getCenter(new Vector3()),[box]);
  const size=useMemo(()=>box.getSize(new Vector3()),[box]);const span=Math.max(size.x,size.y,size.z);
  useEffect(()=>{onReady(url);},[model,onReady,url]);
  useEffect(()=>{
    const vectors={front:[0,0,1],back:[0,0,-1],left:[-1,0,0],right:[1,0,0],side:[1,0,0],top:[0,1,.0001],bottom:[0,-1,.0001],isometric:[1,1,1],hero:[1,.65,1.5]};
    camera.position.copy(new Vector3(...(vectors[view]||vectors.hero)).normalize().multiplyScalar(span*2.6));
    camera.near=Math.max(.001,span/1000);camera.far=span*100;camera.updateProjectionMatrix();
    if(controls.current){controls.current.target.set(0,0,0);controls.current.update();}
    invalidate();
  },[camera,view,span,controls,invalidate]);
  useEffect(()=>{
    model.traverse(o=>{
      if(!o.isMesh)return;
      o.position.copy(o.userData.basePosition);o.updateWorldMatrix(true,false);
      const c=new Box3().setFromObject(o).getCenter(new Vector3()).sub(center);
      if(explode&&c.length()>0){
        const q=o.parent.getWorldQuaternion(new Quaternion()).invert();o.position.add(c.normalize().multiplyScalar(span*.25).applyQuaternion(q));
      }
      o.visible=!hidden.has(o.name);
      for(const [i,mat] of (Array.isArray(o.material)?o.material:[o.material]).entries()){
        if(mat.emissive){if(o.name===selected){mat.emissive.set('#796332');mat.emissiveIntensity=.2;}else{mat.emissive.copy(o.userData.baseEmissions[i].color);mat.emissiveIntensity=o.userData.baseEmissions[i].intensity;}}
      }
    });
    invalidate();
  },[model,hidden,selected,explode,center,span,invalidate]);
  useEffect(()=>()=>model.traverse(o=>{if(o.isMesh)for(const m of Array.isArray(o.material)?o.material:[o.material])m.dispose();}),[model]);
  return <><group position={center.clone().multiplyScalar(-1).toArray()}><primitive object={model} onClick={e=>{e.stopPropagation();onSelect(e.object.name);}}/></group>
    <Grid position={[0,-size.y/2-.003,0]} args={[span*5,span*5]} cellSize={span/10} sectionSize={span/2} cellColor="#ccd0ca" sectionColor="#b0b9b0" fadeDistance={span*5}/>
    {dimensions&&<Html position={[0,size.y*.61,0]} center><span className="dimension-label">Mesh ≈ {Math.round(size.x*1000)} W × {Math.round(size.z*1000)} D × {Math.round(size.y*1000)} H mm</span></Html>}
  </>;
}

class ViewerBoundary extends React.Component {
  state={error:null};static getDerivedStateFromError(error){return {error};}
  render(){return this.state.error?<div className="viewer-empty">3D preview could not load. Download the GLB or inspect the job error.</div>:this.props.children;}
}

export default function Viewer({url,parts=[],posterUrl,label='3D model',onPartSelect}) {
  const [hidden,setHidden]=useState(new Set());const [selected,setSelected]=useState('');
  const [explode,setExplode]=useState(false);const [dimensions,setDimensions]=useState(true);const [view,setView]=useState('hero');
  const [rotate,setRotate]=useState(false);
  const [loadedUrl,setLoadedUrl]=useState('');
  const controls=useRef();
  const selectPart=id=>{setSelected(id);onPartSelect?.(id);};
  useEffect(()=>{setHidden(new Set());setSelected('');setRotate(false);setExplode(false);},[url]);
  return <div className="viewer-layout" data-model-ready={String(Boolean(url)&&loadedUrl===url)} data-model-source={label} data-model-url={url||''} data-selected-component={selected}><div className="viewport">
    {url&&posterUrl&&loadedUrl!==url&&<div className="viewer-poster"><img src={posterUrl} alt={`${label} preview`}/><span>Loading interactive {label}…</span></div>}
    <div className="view-controls">{['front','back','left','right','top','bottom','isometric','hero'].map(v=><button key={v} className={v===view?'active':''} onClick={()=>{setRotate(false);setView(v);}}>{v}</button>)}</div>
    {url?<ViewerBoundary key={url}><Canvas frameloop={rotate?'always':'demand'} camera={{position:[2,1.5,2],fov:40}} dpr={[1,1.5]} onPointerMissed={()=>setSelected('')}>
      <color attach="background" args={['#eef0eb']}/><ambientLight intensity={1.5}/><directionalLight position={[4,6,5]} intensity={3}/><directionalLight position={[-3,2,-2]} intensity={1.5}/>
      <Suspense fallback={<Html center><span className="dimension-label">Loading 3D geometry…</span></Html>}>
        <Model url={url} hidden={hidden} selected={selected} onSelect={selectPart} explode={explode} dimensions={dimensions} view={view} controls={controls} onReady={setLoadedUrl}/>
      </Suspense><OrbitControls ref={controls} makeDefault enableDamping autoRotate={rotate} autoRotateSpeed={1} minDistance={.02} maxDistance={40}/>
    </Canvas></ViewerBoundary>:<div className="viewer-empty"><span className="wire-cube">◇</span><strong>Your design, in three dimensions.</strong><p>Generate CAD to explore the actual component assembly.</p></div>}
    <div className="viewer-bottom"><span>Drag to orbit 360° · scroll to zoom</span><button onClick={()=>setRotate(!rotate)} aria-pressed={rotate}>360° rotation {rotate?'on':'off'}</button><button onClick={()=>setExplode(!explode)} aria-pressed={explode}>Explode {explode?'on':'off'}</button><button onClick={()=>setDimensions(!dimensions)} aria-pressed={dimensions}>Dimensions</button></div>
  </div><aside className="component-panel"><div className="panel-title">Components <span>{parts.length}</span></div>
    <button className="text-button" onClick={()=>setHidden(new Set())}>Show all</button>
    <div className="component-list">{parts.map(p=><div className={'component-row '+(selected===p.part_id?'selected':'')} key={p.part_id}>
      <input type="checkbox" aria-label={`Show ${p.label}`} checked={!hidden.has(p.part_id)} onChange={()=>setHidden(old=>{const n=new Set(old);n.has(p.part_id)?n.delete(p.part_id):n.add(p.part_id);return n;})}/>
      <button onClick={()=>selectPart(p.part_id)}><small>{p.part_id} · {p.material.replaceAll('_',' ')}</small><span>{p.label}</span></button>
    </div>)}</div></aside></div>;
}
