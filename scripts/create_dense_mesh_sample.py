"""Synthetic end-to-end sample: known CAD, rendered reference, perturbed leg.

This makes no paid calls and supplies no evidence about real physical furniture.
"""
import json
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
from app.mesh_to_cad import build_native, tessellate_build
from app.mesh_pipeline import run_reconstruction
from app.ai.geometry_preview import render_build
from tests.test_engineering_cad import table_plan
from app.cad_optimizer_schema import CADCamera, CADViewEvidence
from app.cad_projection import NativeProjection


def main(output='outputs/dense_sample'):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    true_plan = table_plan()
    true_build = build_native(true_plan)
    reference = tessellate_build(true_build)
    reference.mesh.export(output/'synthetic_reference.obj')
    (output/'original_photo.png').write_bytes(render_build(true_build, size=(384, 384)))
    # Recover the exact known camera used by render_build, so the synthetic
    # photo evidence has a calibrated projection rather than a guessed pose.
    points = np.asarray(reference.mesh.vertices)
    center = (points.min(0)+points.max(0))/2
    azimuth, elevation = np.deg2rad([-110, 12])
    toward = np.array([np.cos(elevation)*np.cos(azimuth), np.cos(elevation)*np.sin(azimuth), np.sin(elevation)])
    right = np.cross([0.,0.,1.], toward); right /= np.linalg.norm(right)
    up = np.cross(toward, right)
    projected = np.column_stack(((points-center)@right, (points-center)@up))
    span = np.ptp(projected, axis=0); middle = (projected.min(0)+projected.max(0))/2
    scale = min((384-60)/span[0], (384-85)/span[1])
    camera = CADCamera(image_size=(384,384), projection='orthographic',
        intrinsic_matrix=[[scale,0,192],[0,scale,180],[0,0,1]],
        rotation_vector=tuple(cv2.Rodrigues(np.vstack((right,-up,-toward)))[0].ravel()),
        translation_mm=(-center@right-middle[0], center@up+middle[1], 2000+center@toward),
        reliable=True, uncertainty='Exactly known synthetic raster projection; no physical camera claim')
    silhouette = NativeProjection(true_build, camera).silhouette()
    view = CADViewEvidence(image_id=uuid4(), camera=camera, silhouette=silhouette.tolist(), silhouette_reviewed=True)
    initial = true_plan.model_copy(deep=True)
    initial.parts[1].position[0] = '-width_mm/2+75'
    report = run_reconstruction(output/'synthetic_reference.obj', output,
        initial_plan=initial, up_axis='Z', known_dimensions={'width_mm':700},
        options=dict(mode='quality', max_evaluations=40, max_seconds=120,
            photo_views=[view.model_dump(mode='json')],
            controls=dict(parameter_ids=['F002.position.0'])),
        progress=lambda stage, percent: print(f'{percent:3d}% {stage}', flush=True))
    print(json.dumps(dict(status=report['status'], initial_mean_mm=report['initial']['symmetric_mean_mm'],
        final_mean_mm=report['final']['symmetric_mean_mm'], final_points=report['actual_verification_points'],
        primary_fitting_method=report['primary_fitting_method'], photo_status=report['photo_verification_status'],
        step_reimport=report['export_validation']['assembly_reopened']), indent=2))


if __name__ == '__main__':
    main()
