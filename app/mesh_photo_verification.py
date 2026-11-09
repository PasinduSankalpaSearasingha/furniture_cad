"""Reuse local foreground extraction and camera fitting on the actual photograph."""
import time
from uuid import uuid4
import numpy as np
from .reference_fit_schema import FitView, ReferenceFitOptions
from .ai.reference_fitting import (prepare_target, mesh_from_build, frame_for, initial_camera,
                                  render_mask, metrics, fit_camera)


class PhotoReference:
    def __init__(self, image_bytes, plan, build, view=None):
        self.view = view or FitView(image_id=uuid4())
        self.target = prepare_target(image_bytes, self.view, 96)
        self.usable = self.target['usable']
        self.camera = None
        if not self.usable:
            return
        mesh = mesh_from_build(build, plan)
        frame = frame_for(mesh)
        best = None
        # This searches the estimated viewing direction locally; no AI calls.
        for azimuth in range(-180, 180, 30):
            for elevation in (0, 25, 45):
                camera = initial_camera(mesh, self.target, frame, azimuth, elevation)
                score = metrics(render_mask(mesh, camera, self.target['size']), self.target)['loss']
                if best is None or score < best[0]:
                    best = score, camera
        fit = fit_camera(mesh, self.target, frame, best[1],
            ReferenceFitOptions(camera_evaluations=48), time.monotonic()+8)
        self.camera = fit['camera']

    def evaluate(self, plan, build):
        if not self.usable:
            return None, dict(status='unverified', reason='Original-photo foreground extraction is ambiguous',
                              warnings=self.target['warnings'])
        mesh = mesh_from_build(build, plan)
        result = metrics(render_mask(mesh, self.camera, self.target['size']), self.target)
        return result['loss'], dict(status='estimated_camera_photo_comparison', metrics=result,
            camera=self.camera, mask_reviewed=self.view.mask_reviewed, camera_physically_calibrated=False,
            foreground_method=self.target['method'], warnings=self.target['warnings'],
            physical_accuracy_verified=False)
