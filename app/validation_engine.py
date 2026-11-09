"""Deterministic checks and explicit gaps. Passing geometry is not engineering approval."""
from itertools import combinations
import math
from pydantic import Field
from .models import StrictModel, CabinetSpec, Material
from .geometry import exact_bounds


class Finding(StrictModel):
    code: str
    severity: str
    category: str
    message: str
    part_ids: list[str] = Field(default_factory=list)


class ValidationReport(StrictModel):
    revision: int
    findings: list[Finding]
    approved_for_manufacturing: bool = False

    @property
    def has_blockers(self):
        return any(f.severity in ('ERROR', 'BLOCKER') for f in self.findings)

    def as_dict(self):
        return {**self.model_dump(mode='json'), 'has_blockers': self.has_blockers,
                'status': 'VALIDATION_FAILED' if self.has_blockers else 'DESIGNER_REVIEW'}


def validate_build(spec: CabinetSpec, build, schedules=None, drawings=None) -> ValidationReport:
    findings = [Finding(**i) for i in build.joint_issues]
    def add(code, category, message, ids=(), severity='BLOCKER'):
        findings.append(Finding(code=code, category=category, message=message,
                                part_ids=list(ids), severity=severity))
    if not (spec.measured_dimensions_confirmed and spec.dimension_provenance == 'physically_verified'
            and spec.measurement_reference):
        add('MEASUREMENTS_REQUIRED', 'dimensional', 'Physical measurements and their evidence reference must be verified')
    expected = 10 if spec.include_glazed_door else 5
    ids = [p.part_id for p in build.parts]
    if spec.family!='cabinet':
        expected=len(build.registry)
        if ids!=[f'F{i+1:03d}' for i in range(expected)]:
            add('COMPONENT_REGISTRY_MISMATCH','geometric','Furniture component sequence differs from the registry')
        add('FURNITURE_STUDY_REVIEW_REQUIRED','engineering',
            'Photo-derived proportions, curves and contact trims require dimensional, joint and structural review' if spec.photo_design else
            'Library proportions, curves, profiles and contact trims are estimates; joint, ergonomic and structural engineering is required')
    if spec.photo_design:
        expected=len(spec.photo_design.parts)
        if ids!=[part.part_id for part in spec.photo_design.parts]:
            add('PHOTO_COMPONENT_MISMATCH','geometric','Photographed components differ from the saved reconstruction plan')
        add('PHOTO_RECONSTRUCTION_APPROXIMATE','dimensional','Photograph-derived profiles and hidden geometry have not been physically verified')
    if spec.display_layout is not None:
        from .display_cad import display_component_ids
        expected_ids = display_component_ids(spec)
        expected = len(expected_ids)
        if set(ids)!=expected_ids:
            add('COMPONENT_REGISTRY_MISMATCH','geometric','Image-study component identifiers differ from the deterministic template')
        add('IMAGE_STUDY_REVIEW_REQUIRED','engineering',
            'Photo-derived layout and dimensions are estimated; carving, hardware, glazing and construction need a custom engineering design')
    if len(ids) != expected or len(set(ids)) != len(ids) or set(ids) != set(build.shapes):
        add('COMPONENT_REGISTRY_MISMATCH', 'geometric', 'Missing, duplicate or unexpected components')
    profiles = {p.material_id: p for p in spec.material_profiles}
    bounds = {}
    for part in build.registry:
        pid = [part.part_id]
        if part.part_id not in build.shapes:
            continue  # The registry mismatch above is already a blocking finding.
        shape = build.shapes[part.part_id].val()
        if not shape.isValid() or shape.Volume() <= 0 or len(shape.Solids()) != 1:
            add('INVALID_CAD_SOLID', 'geometric', 'Expected one valid positive-volume solid', pid, 'ERROR')
        bb = exact_bounds(shape)
        bounds[part.part_id] = bb
        for actual, recorded in zip((bb.xlen, bb.ylen, bb.zlen), part.size_xyz_mm):
            if not math.isclose(actual, recorded, abs_tol=1e-6):
                add('PART_DIMENSION_MISMATCH', 'dimensional', 'CAD envelope differs from registry', pid, 'ERROR')
        for actual, recorded in zip(((bb.xmin+bb.xmax)/2, (bb.ymin+bb.ymax)/2, (bb.zmin+bb.zmax)/2), part.center_xyz_mm):
            if not math.isclose(actual, recorded, abs_tol=1e-6):
                add('ASSEMBLY_ALIGNMENT_ERROR', 'geometric', 'CAD location differs from registry', pid, 'ERROR')
        if part.revision != spec.revision:
            add('PART_REVISION_MISMATCH', 'documentation', 'Part revision differs from specification', pid)
        profile = profiles.get(part.material_id)
        if profile is None:
            add('MATERIAL_SPECIFICATION_REQUIRED', 'material', 'Actual stock profile is missing', pid)
        else:
            if not profile.approved or not profile.evidence_reference:
                add('MATERIAL_APPROVAL_REQUIRED', 'material', 'Material profile needs evidence-backed approval', pid)
            if profile.material != part.material or not math.isclose(profile.actual_thickness_mm, part.blank_thickness_mm, abs_tol=1e-6):
                add('MATERIAL_THICKNESS_OR_TYPE_MISMATCH', 'material', 'Blank thickness or material differs from actual stock profile', pid)
            missing = []
            if not profile.edge_treatment: missing.append('edge treatment')
            if part.material in (Material.plywood, Material.mdf) and not profile.stock_size_mm: missing.append('sheet dimensions')
            if part.material != Material.glass and not profile.grain_direction: missing.append('grain direction')
            if part.material == Material.mdf and not profile.finish: missing.append('finish')
            if part.material == Material.solid_wood:
                if not profile.species: missing.append('species')
                if profile.moisture_percent is None: missing.append('moisture')
                if not profile.movement_review: missing.append('wood movement review')
                if not profile.stock_size_mm: missing.append('stock dimensions')
            if missing: add('MATERIAL_DETAILS_REQUIRED', 'material', ', '.join(missing), pid)
            if profile.stock_size_mm:
                a,b = profile.stock_size_mm
                l,w = part.blank_length_mm, part.blank_width_mm
                fits = (w <= a and l <= b) if part.grain_axis == 'width' else (l <= a and w <= b)
                if part.grain_axis == 'none': fits = fits or (w <= a and l <= b)
                if not fits: add('BLANK_EXCEEDS_STOCK', 'material', 'Blank does not fit specified stock/grain orientation', pid)
        if not part.blank_verified:
            add('BLANK_ALLOWANCE_REQUIRED', 'dimensional', 'Explicit approved blank allowances are required', pid)
    # Numerical epsilons here are kernel consistency thresholds, not machining tolerances.
    for a,b in combinations(build.parts, 2):
        if a.part_id not in build.shapes or b.part_id not in build.shapes: continue
        first,second = bounds[a.part_id],bounds[b.part_id]
        # Disjoint envelopes and shared faces cannot contain overlapping volume.
        if any(min(getattr(first,axis+'max'),getattr(second,axis+'max'))-
               max(getattr(first,axis+'min'),getattr(second,axis+'min')) <= 1e-8 for axis in ('x','y','z')):
            continue
        if build.shapes[a.part_id].val().intersect(build.shapes[b.part_id].val()).Volume() > 1e-5:
            add('UNEXPECTED_INTERSECTION', 'geometric', 'Components have overlapping solid volume', [a.part_id,b.part_id], 'ERROR')
    if spec.joint_type == 'dowel_demo':
        add('DEMO_JOINERY_NOT_APPROVED', 'joinery', 'Legacy demonstration bores are never approved factory joinery')
    add('CONNECTION_ENGINEERING_REQUIRED', 'joinery', 'Complete carcass/back connections, residual walls and loads require engineered design')
    if not spec.hardware:
        add('HARDWARE_SPECIFICATION_REQUIRED', 'hardware', 'No verified connector/hardware schedule is supplied')
    for h in spec.hardware:
        if (not h.approved or not h.evidence_reference or not h.installation_reference or any(p not in ids for p in h.part_ids)):
            add('HARDWARE_SPECIFICATION_REQUIRED', 'hardware', f'{h.hardware_id}: approval, installation reference and valid parts required', h.part_ids)
        elif any(build.registry[p].material not in h.compatible_materials for p in h.part_ids):
            add('HARDWARE_MATERIAL_INCOMPATIBLE', 'hardware', h.hardware_id, h.part_ids)
    if spec.hardware:
        add('HARDWARE_MOUNTING_GEOMETRY_REQUIRED', 'hardware', 'Catalog installation geometry is not yet applied to this template')
    if spec.include_glazed_door:
        glass_ids = [part.part_id for part in build.registry if part.material==Material.glass]
        glass = profiles.get(spec.material_assignments.get('glass'))
        if glass is None or not all((glass.glass_type, glass.retention_reference, glass.safety_reference, glass.edge_treatment, glass.approved)):
            add('GLASS_SPECIFICATION_REQUIRED', 'glass', 'Glass type, actual thickness, edges, retention and safety evidence required', glass_ids)
        if spec.glass_edge_clearance_mm is None or spec.door_standoff_mm is None:
            add('DOOR_CLEARANCE_SPECIFICATION_REQUIRED', 'glass', 'Legacy display offsets have no approved clearance basis')
        add('GLASS_RETENTION_GEOMETRY_REQUIRED', 'glass', 'Pane is visual only; retention and glass cut dimensions are not engineered', glass_ids)
        add('DOOR_SWING_CHECK_REQUIRED', 'geometric', 'Hinge pivot, opening sweep and collision envelope are not verified')
        for kind in ('hinge', 'handle', 'glass_retainer'):
            if not any(h.hardware_type == kind for h in spec.hardware):
                add('HARDWARE_SPECIFICATION_REQUIRED', 'hardware', f'{kind} configuration is missing')
    if schedules is not None:
        cut = schedules['parts']
        actual = {r['part_id']: (r['quantity'], r['revision']) for r in cut}
        wanted = {p.part_id: (p.quantity, p.revision) for p in build.registry}
        if actual != wanted or len(cut) != len(wanted):
            add('CUT_LIST_REGISTRY_MISMATCH', 'documentation', 'Cut/glass rows differ from registry')
        for row in cut:
            if row['part_id'] not in wanted: continue
            p = build.registry[row['part_id']]
            pairs = [('length_mm',p.blank_length_mm),('width_mm',p.blank_width_mm),
                     ('thickness_mm',p.blank_thickness_mm),('finished_length_mm',p.length_mm),
                     ('finished_width_mm',p.width_mm),('finished_thickness_mm',p.thickness_mm)]
            if any(row.get(key) is None or not math.isclose(row[key],value,abs_tol=1e-6) for key,value in pairs):
                add('CUT_LIST_DIMENSION_MISMATCH', 'documentation', 'Cut/glass dimensions differ from source parts', [p.part_id])
        if sum(r['parts_count'] for r in schedules['bom']) != sum(p.quantity for p in build.registry):
            add('BOM_QUANTITY_MISMATCH', 'documentation', 'BOM quantities differ from registry')
    if drawings is not None and (drawings['revision'] != spec.revision or set(drawings['part_ids']) != set(ids)):
        add('DRAWING_REVISION_OR_PART_MISMATCH', 'documentation', 'Drawing manifest differs from source revision')
    add('SHOP_DRAWING_ENGINEERING_REQUIRED', 'documentation', 'Projected review views are not complete section/joint/tolerance shop drawings')
    add('ENGINEERING_BENCHMARK_REQUIRED', 'engineering', 'Strength, stability, glass safety and machining safety require qualified review and a real measured benchmark')
    add('PRICE_REQUIRED', 'costing', 'No approved procurement, labour, finishing or packaging prices supplied', severity='WARNING')
    add('GEOMETRY_CHECKS_COMPLETED', 'geometric', 'Solid, envelope, placement and intersection checks ran; these do not establish structural safety', severity='INFO')
    return ValidationReport(revision=spec.revision, findings=findings)
