"""Offline mesh-to-native-CAD CLI; uses no Neural4D or LLM calls.

Example: .venv\Scripts\python.exe -m scripts.run_mesh_reconstruction furniture.obj --up-axis Z --height 750
"""
import argparse
import json
from pathlib import Path
from app.mesh_pipeline import run_reconstruction


def main():
    parser = argparse.ArgumentParser(description='Fit native furniture CAD to an existing GLB/OBJ reference')
    parser.add_argument('mesh', type=Path)
    parser.add_argument('--output', type=Path, default=Path('outputs/local_mesh_reconstruction'))
    parser.add_argument('--plan', type=Path, help='Existing PhotoDesign feature specification')
    parser.add_argument('--photo', type=Path, help='Original photograph for local visible-shape comparison')
    parser.add_argument('--up-axis', choices=['Y','Z'], default='Y')
    parser.add_argument('--kind', choices=['table','cabinet','chair'], default='table')
    parser.add_argument('--mode', choices=['economy','balanced','quality','extended'], default='quality')
    parser.add_argument('--height', type=float, default=750, help='Estimated height in mm; does not assert a measurement')
    parser.add_argument('--known', type=Path, help='JSON object with confirmed width_mm/depth_mm/height_mm')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.photo:
        from app.ai.images import normalized_png
        encoded, _, _ = normalized_png(args.photo.read_bytes(), 10*1024**2)
        (args.output/'original_photo.png').write_bytes(encoded)
    report = run_reconstruction(args.mesh, args.output, up_axis=args.up_axis, estimated_height_mm=args.height,
        initial_plan=json.loads(args.plan.read_text()) if args.plan else None,
        known_dimensions=json.loads(args.known.read_text()) if args.known else None, furniture_type=args.kind,
        options=dict(mode=args.mode), progress=lambda stage, percent: print(f'{percent:3d}% {stage}', flush=True))
    print(json.dumps(dict(status=report['status'], final=report['final'],
        photo_status=report['photo_verification_status'], production_ready=report['production_ready']), indent=2))


if __name__ == '__main__':
    main()
