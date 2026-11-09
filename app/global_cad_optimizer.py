"""Bounded native-CAD parameter search scored against the entire 3D surface."""
import time
import threading
import numpy as np
from scipy.optimize import least_squares
from .cad_parameters import CADParameterRegistry
from .geometry_cache import GeometryCache,ResourceBudget
from .geometry_acceleration import SurfaceIndex
from .mesh_sampling import sample_surface
from .mesh_analyzer import MeshData,analyze_mesh
from .mesh_cad_schema import DenseOptimizationOptions
from .photo_schema import PhotoDesign
from .global_mesh_fitter import GlobalMeshFitter,NativeMeshProxy


class _BudgetStop(Exception):
    pass


class _MemoryMonitor:
    def __enter__(self):
        import psutil
        self.process=psutil.Process()
        self.peak=self.process.memory_info().rss
        self.finished=threading.Event()
        def sample():
            while not self.finished.wait(.05):
                self.peak=max(self.peak,self.process.memory_info().rss)
        self.thread=threading.Thread(target=sample,daemon=True)
        self.thread.start()
        return self

    def __exit__(self,*_):
        self.peak=max(self.peak,self.process.memory_info().rss)
        self.finished.set(); self.thread.join()


def _correspondence(reference,cad,options,parts):
    part_index={p.part_id:i for i,p in enumerate(parts)}
    mapping=options.component_map
    if not mapping and reference.report.get('native_part_ids'):
        mapping={i:pid for i,pid in enumerate(reference.report['native_part_ids'])}
    if mapping:
        labels=np.full(len(reference.mesh.faces),-1,dtype=np.int32)
        unknown=set(mapping.values())-part_index.keys()
        if options.component_map and unknown:
            raise ValueError('Explicit component map refers to an unknown native component')
        for label,pid in mapping.items():
            if pid in part_index:
                labels[reference.face_components == label]=part_index[pid]
        result=MeshData(reference.mesh.copy(),labels,dict(reference.report))
        return result,dict(method='explicit/native component identities',unassigned_faces=int((labels<0).sum()))
    closest=SurfaceIndex(cad,threads=options.workers).closest(reference.mesh.triangles_center)
    result=MeshData(reference.mesh.copy(),cad.face_components[closest['face_ids']],dict(reference.report))
    return result,dict(method='initial closest triangle regions; uncertain segmentation',unassigned_faces=0)


def optimize_global_cad(plan,reference,options=None,*,inputs=None,cache=None,progress=None,photo_bytes=None,publish_best=None):
    with _MemoryMonitor() as monitor:
        result=_optimize(plan,reference,options,inputs=inputs,cache=cache,progress=progress,
                         photo_bytes=photo_bytes,publish_best=publish_best)
    result.report['resources']['peak_rss_mb']=monitor.peak/1024**2
    result.report['resources']['peak_memory_method']='process RSS sampled every 50 ms'
    return result


def _optimize(plan,reference,options,*,inputs,cache,progress,photo_bytes,publish_best):
    # Keep the established public builder/photograph hooks, including their
    # validation behavior and tests, while replacing only the fitting engine.
    from .dense_cad_optimizer import build_native,tessellate_build,photo_error,sensitive_parameters,DenseOptimizationResult
    from .engineering_cad import geometry_checks,check_supplied_dimensions
    from .mesh_registration import align_reference_mesh
    started=time.monotonic()
    options=DenseOptimizationOptions.model_validate(options or {})
    plan=PhotoDesign.model_validate(plan)
    inputs=inputs or {}
    budget=ResourceBudget(options.memory_mb,workers=options.workers)
    cache=(cache or GeometryCache(max_bytes=min(256,options.memory_mb//4)*1024**2)) if options.geometry_cache else None
    native_cache={} if options.geometry_cache else None
    controls=options.controls.model_copy(update={'mode':'quality' if options.mode=='extended' else options.mode})
    registry=CADParameterRegistry(plan,inputs.get('known_values',[]),controls)
    baseline=build_native(plan,inputs,geometry_cache=native_cache)
    checks=geometry_checks(baseline)
    initial_cad=tessellate_build(baseline)
    registration=dict(method='previously aligned reference frame',reference_to_cad_transform=np.eye(4).tolist(),shape_parameters_optimized=False)
    if options.align_reference:
        reference,registration=align_reference_mesh(reference,initial_cad,count=options.coarse_points,budget=budget,cache=cache)
    reference,correspondence=_correspondence(reference,initial_cad,options,plan.parts)
    fitter=GlobalMeshFitter(reference,options,budget=budget,cache=cache)
    deadline=started+options.max_seconds
    holdout=fitter.samples(options.coarse_points)
    holdout_cad=sample_surface(initial_cad,options.coarse_points,budget=budget,cache=cache)
    initial=fitter.evaluate(initial_cad,holdout_cad,holdout)
    best=initial
    best_plan,best_build,best_cad,best_checks=plan,baseline,initial_cad,checks
    best_updates={}
    required={tuple(sorted((c['a'],c['b']))) for c in checks['contacts']}
    required.update(tuple(sorted(pair)) for pair in controls.required_contacts)
    photo_reference=None
    if options.original_photo_validation and photo_bytes:
        from .mesh_photo_verification import PhotoReference
        photo_reference=PhotoReference(photo_bytes,plan,baseline,options.photo_reference)
    def photograph(model,build):
        if not options.original_photo_validation:
            return None,[]
        score,evidence=photo_error(model,build,options.photo_views)
        if score is None and photo_reference is not None:
            score,details=photo_reference.evaluate(model,build); evidence.append(details)
        return score,evidence
    initial_photo,photo_evidence=photograph(plan,baseline)
    best_photo=initial_photo
    if publish_best: publish_best(plan,baseline)
    stages=[options.coarse_points]
    if options.mode in ('balanced','quality','extended'): stages.append(options.medium_points)
    if options.mode in ('quality','extended'): stages.append(options.fine_points)
    stages=list(dict.fromkeys(stages))
    evaluations=0
    exact_evaluations=0
    revisions=[]; attempts=[]; stage_reports=[]
    stop_reason='completed'
    structural_review=bool(plan.unrepresented_features or correspondence['unassigned_faces'])
    active=registry.candidates() if options.enabled and not structural_review else []
    for count in stages:
        if not active or time.monotonic() >= deadline or evaluations >= options.max_evaluations:
            stop_reason=('unrepresented_regions_require_structural_review' if structural_review else
                         'disabled_or_no_active_parameters') if not active else 'evaluation_or_wall_time_budget'
            break
        stage_started=time.monotonic(); start_evaluations=evaluations; start_score=best.score
        importance=None
        if options.adaptive_sampling and count>options.coarse_points:
            error=SurfaceIndex(best_cad,threads=options.workers).closest(reference.mesh.triangles_center)['distances']
            importance=error/max(float(np.quantile(error,.9)),.01)
        # Include the dense Jacobian and robust residual buffers in the trial
        # allocation estimate. Verification has no Jacobian and retains its
        # independent higher-density budget.
        fitting_count=budget.sample_count(count,bytes_per_point=max(320,128+options.max_parameters*64))
        samples=fitter.samples(fitting_count,importance)
        cad_samples=sample_surface(best_cad,fitting_count,budget=budget,cache=cache,adaptive=options.adaptive_sampling)
        proxy=NativeMeshProxy(best_plan,best_cad,cad_samples)
        active,sensitivities=sensitive_parameters(registry,best_plan,cad_samples,options.max_parameters,best_updates)
        if not active:
            stop_reason='no_sensitive_unlocked_parameters'; break
        baseline_trial=fitter.evaluate(best_cad,cad_samples,samples,include_report=False)
        shortlist={}
        updates=dict(best_updates)
        # All groups retain the original registry bounds. Density changes cannot
        # repeatedly expand the allowed displacement or bypass measurement locks.
        for phase in ('global','position','component','curve'):
            group=[p for p in active if p.phase==phase and p.maximum>p.minimum]
            if not group: continue
            names=[p.parameter_id for p in group]
            x0=np.array([updates.get(p.parameter_id,p.current_value) for p in group])
            low=np.array([p.minimum for p in group]); high=np.array([p.maximum for p in group])
            best_proxy=[np.inf,evaluations]
            residual_size=len(baseline_trial.residuals)+len(group)
            scale=np.sqrt(max(len(samples.points),len(cad_samples.points)))
            residual_cache={}
            evaluation_cache={}
            def residual(x,normal_reference=None):
                nonlocal evaluations
                if time.monotonic()>=deadline:
                    raise _BudgetStop()
                key=np.asarray(x,dtype=np.float64).tobytes()
                if normal_reference is None and key in residual_cache: return residual_cache[key]
                if evaluations>=options.max_evaluations: raise _BudgetStop()
                if evaluations-best_proxy[1]>=controls.stagnation_evaluations:
                    raise _BudgetStop()
                evaluations+=1
                trial={**updates,**dict(zip(names,x))}
                try:
                    model=registry.apply(trial)
                    trial_cad,trial_samples=proxy.evaluate(model)
                    evaluation=fitter.evaluate(trial_cad,trial_samples,samples,include_report=False,normal_reference=normal_reference)
                    if normal_reference is None and evaluation.score<best_proxy[0]-1e-10: best_proxy[:]=evaluation.score,evaluations
                    # Reject clearly worse proxy trials before any OCCT rebuild.
                    if normal_reference is None and evaluation.score<baseline_trial.score-options.minimum_improvement:
                        trial_key=tuple(sorted(trial.items()))
                        shortlist[trial_key]=(evaluation.score,dict(trial))
                        if len(shortlist)>options.exact_candidates*5:
                            worst=max(shortlist,key=lambda key:shortlist[key][0]); del shortlist[worst]
                    regularizer=(x-x0)/np.maximum(high-low,1)*np.sqrt(options.regularization_weight)
                    result=np.concatenate((evaluation.residuals,regularizer))*scale
                except (ValueError,FloatingPointError):
                    evaluation=None
                    result=np.full(residual_size,1e6)
                if progress: progress('global_surface_fitting',evaluations,options.max_evaluations)
                if normal_reference is None:
                    residual_cache[key]=result
                    evaluation_cache[key]=evaluation
                    while len(residual_cache)>2:
                        oldest=next(iter(residual_cache));del residual_cache[oldest];evaluation_cache.pop(oldest,None)
                return result
            def jacobian(x):
                # Open3D queries are float32. Relative default differences can
                # vanish at zero angles or cross an entire thin panel near the
                # optimum. Use bounded steps in each control's actual units.
                base=residual(x)
                fixed_normals=evaluation_cache.get(np.asarray(x,dtype=np.float64).tobytes())
                matrix=np.empty((len(base),len(x)))
                for column,parameter in enumerate(group):
                    nominal=.002 if parameter.unit=='dimensionless' else .05
                    step=min(nominal,(high[column]-low[column])*.1)
                    if x[column]+step>high[column]: step=-step
                    if x[column]+step<low[column]: step=min(nominal,high[column]-x[column])
                    probe=x.copy();probe[column]+=step
                    changed=residual(probe,normal_reference=fixed_normals)
                    if np.max(np.abs(changed))>=1e5 and low[column]<=x[column]-step<=high[column]:
                        step=-step;probe=x.copy();probe[column]+=step;changed=residual(probe,normal_reference=fixed_normals)
                    matrix[:,column]=(changed-base)/step
                return matrix
            try:
                solved=least_squares(residual,x0,bounds=(low,high),loss='linear',
                    x_scale=np.maximum((high-low)*.25,.001),jac=jacobian,
                    max_nfev=max(1,(options.max_evaluations-evaluations)//(len(group)+1)),
                    ftol=1e-6,xtol=1e-5,gtol=1e-7)
                updates.update(dict(zip(names,solved.x)))
                if solved.status==0: stop_reason='solver_function_evaluation_limit'
            except _BudgetStop:
                stop_reason='evaluation_or_stagnation_or_wall_time_budget'
                break
        shortlisted=sorted(shortlist.values(),key=lambda item:item[0])[:options.exact_candidates]
        for proxy_score,trial in shortlisted:
            if time.monotonic()>=deadline: break
            try:
                model=registry.apply(trial)
                build=build_native(model,inputs,geometry_cache=native_cache)
                valid=geometry_checks(build)
                contacts={tuple(sorted((c['a'],c['b']))) for c in valid['contacts']}
                if not required<=contacts: raise ValueError('Candidate lost a required assembly contact')
                cad=tessellate_build(build)
                sampled=sample_surface(cad,options.coarse_points,budget=budget,cache=cache)
                evaluation=fitter.evaluate(cad,sampled,holdout)
                exact_evaluations+=1
                photo,evidence=photograph(model,build)
                if best_photo is not None and (photo is None or photo>best_photo+1e-6):
                    raise ValueError('Candidate worsens reviewed original-photo evidence')
                attempts.append(dict(valid=True,score=evaluation.score,proxy_score=proxy_score,updates=trial))
                if evaluation.score<best.score-options.minimum_improvement:
                    best_plan,best_build,best_cad,best_checks=model,build,cad,valid
                    best_updates,best=dict(trial),evaluation
                    best_photo,photo_evidence=photo,evidence
                    revisions.append(dict(score=best.score,surface_mean_mm=evaluation.report['symmetric_mean_mm'],updates=trial))
                    if publish_best: publish_best(model,build)
            except Exception as exc:
                attempts.append(dict(valid=False,reason=str(exc)[:300],updates=trial))
        stage_reports.append(dict(requested_points=count,actual_points=len(samples.points),evaluations=evaluations-start_evaluations,
            seconds=time.monotonic()-stage_started,exact_score_improvement=start_score-best.score,sensitivities=sensitivities,
            shortlisted_native_candidates=len(shortlisted),memory_fallback=len(samples.points)<count))
        if start_score-best.score<=options.minimum_improvement:
            stop_reason='density_increase_did_not_improve_fit'; break
    final_reference=fitter.samples(options.verify_points)
    final_samples=sample_surface(best_cad,options.verify_points,budget=budget,cache=cache)
    final=fitter.evaluate(best_cad,final_samples,final_reference)
    locked=check_supplied_dimensions(best_build,inputs.get('known_values',[]))
    final_photo,photo_evidence=photograph(best_plan,best_build)
    report=dict(version='GLOBAL_SURFACE_OPTIMIZER_V2',primary_fitting_method='global_surface',mode=options.mode,
        status='improved' if revisions else 'validated_initial',initial=initial.report,final=final.report,
        initial_objective=initial.score,final_objective=best.score,initial_photo_error=initial_photo,photo_error=final_photo,
        units='mm',evaluations=evaluations,exact_evaluations=exact_evaluations,query_evaluations=fitter.evaluations,
        mesh_query_seconds=fitter.query_seconds,stages=stage_reports,revisions=revisions,attempts=attempts,
        stop_reason=stop_reason,registration=registration,component_correspondence=correspondence,
        structural_review_required=structural_review,multiview=None,fitting_views=0,virtual_camera_evaluations=0,
        ignored_legacy_multi_angle=options.multi_angle,photo_evidence=photo_evidence,
        photo_verification_status=('reviewed_photo_camera_evidence' if any(v.camera and v.camera.reliable for v in options.photo_views)
            else 'estimated_camera_photo_comparison') if final_photo is not None else
            ('disabled_by_configuration' if not options.original_photo_validation else 'unverified: usable photo foreground or reviewed evidence required'),
        original_photograph_priority='photo guard vetoes visible-shape regressions; excluded from primary 3D objective',
        locked_measurements=locked,fixed_dimensions_preserved=True,solid_checks=best_checks,
        processing_seconds=time.monotonic()-started,resources=budget.snapshot(),fallback=final_reference.fallback,
        actual_verification_points=len(final_reference.points),requested_verification_points=options.verify_points,
        requested_stage_points=stages,sdf=fitter.sdf_status,external_ai_calls=0,additional_llm_calls=0,
        physical_accuracy_verified=False,hidden_geometry='estimated from the generated mesh; not physical evidence',
        solver='bounded SciPy trust-region least_squares with area-weighted soft-L1 residuals and staged controls',
        fast_model='cached B-Rep triangle proxies and fixed barycentric samples; bidirectional closest-triangle queries',
        exact_model='rebuilt and validated native B-Rep; same global objective measured on CAD tessellation',
        native_component_cache_enabled=options.geometry_cache,
        loss_configuration=dict(bidirectional=options.bidirectional,point_to_plane=options.point_to_plane,
            normal_loss=options.normal_loss,component_aware=options.component_aware,robust_delta_mm=options.robust_delta_mm,
            surface_weight=options.surface_weight,point_to_plane_weight=options.point_to_plane_weight,
            normal_weight=options.normal_weight,component_weight=options.component_weight),
        finite_differences='bounded absolute steps; fixed normal correspondences within each Jacobian',
        parameters=[registry.parameters[pid].report() for pid in sorted({pid for stage in stage_reports for pid in stage['sensitivities']})],
        limitations=['Arbitrary sculpted furniture and automatic multi-patch fitting remain unresolved',
            'Final verification may outlast the numerical solve time budget; the isolated job worker enforces a hard deadline',
            'Distances use a temporary CAD tessellation at 0.25 mm; STEP remains native B-Rep geometry'])
    return DenseOptimizationResult(best_plan,best_build,report,fitter.reference,final_reference.points,final.reverse['distances'])
