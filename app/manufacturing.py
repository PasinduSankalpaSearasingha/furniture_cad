import csv
import json
from collections import defaultdict
from pathlib import Path
from openpyxl import Workbook
from .models import CabinetSpec, Material


def write_csv(path, rows, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows({k: ("'"+v if isinstance(v,str) and v.startswith(('=', '+', '-', '@')) else v)
                     for k,v in row.items()} for row in rows)


def schedules_from_registry(build, spec):
    rows = []
    groups = defaultdict(list)
    for p in build.registry:
        rows.append(dict(part_id=p.part_id, label=p.label, material=p.material.value,
            material_id=p.material_id, length_mm=p.blank_length_mm, width_mm=p.blank_width_mm,
            thickness_mm=p.blank_thickness_mm, finished_length_mm=p.length_mm,
            finished_width_mm=p.width_mm, finished_thickness_mm=p.thickness_mm,
            quantity=p.quantity, grain_axis=p.grain_axis, edge_treatment=p.edge_treatment or 'SPECIFICATION_REQUIRED',
            revision=p.revision, status='REVIEW', blank_verified=p.blank_verified, note=p.note))
        groups[(p.material_id, p.material.value, p.blank_thickness_mm)].append(p)
    profiles = {p.material_id: p for p in spec.material_profiles}
    bom = []
    for (mid,material,thickness), parts in sorted(groups.items()):
        profile = profiles.get(mid)
        bom.append(dict(material=material, material_id=mid, thickness_mm=thickness,
            parts_count=sum(p.quantity for p in parts), part_ids=','.join(p.part_id for p in parts),
            blank_volume_m3=sum(p.blank_length_mm*p.blank_width_mm*p.blank_thickness_mm*p.quantity for p in parts)/1e9,
            blank_area_m2=sum(p.blank_length_mm*p.blank_width_mm*p.quantity for p in parts)/1e6,
            supplier_reference=profile.supplier_reference if profile else 'SPECIFICATION_REQUIRED',
            stock_quantity=None, procurement_status='NESTING_AND_WASTAGE_REQUIRED',
            unit_cost=None, extended_cost=None, pricing_status='PRICE_REQUIRED'))
    hardware = [h.model_dump(mode='json') for h in spec.hardware]
    return dict(parts=rows, bom=bom, hardware=hardware,
                estimate=dict(status='INCOMPLETE', total=None, currency=None,
                              missing=['PRICE_REQUIRED','WASTAGE_REQUIRED','LABOUR_REQUIRED','FINISHING_REQUIRED','PACKAGING_REQUIRED']))


def export_manufacturing(spec: CabinetSpec, build, outdir: Path, report=None):
    from .validation_engine import validate_build
    outdir.mkdir(parents=True, exist_ok=True)
    data = schedules_from_registry(build, spec)
    cols = list(data['parts'][0])
    wood = [r for r in data['parts'] if r['material'] != Material.glass.value]
    glass = [r for r in data['parts'] if r['material'] == Material.glass.value]
    write_csv(outdir/'cut_list_REVIEW.csv', wood, cols)
    write_csv(outdir/'glass_schedule_REVIEW.csv', glass, cols)
    write_csv(outdir/'material_bom_REVIEW.csv', data['bom'], list(data['bom'][0]))
    hcols = ['hardware_id','manufacturer','supplier_reference','product_code','hardware_type','quantity','approved']
    write_csv(outdir/'hardware_schedule_REVIEW.csv', [{k:h[k] for k in hcols} for h in data['hardware']], hcols)
    wb = Workbook()
    wb.remove(wb.active)
    for title, items, headers in [('Cut list',wood,cols),('Glass',glass,cols),('BOM',data['bom'],list(data['bom'][0])),
                                  ('Hardware',[{k:h[k] for k in hcols} for h in data['hardware']],hcols)]:
        sheet = wb.create_sheet(title)
        sheet.append(headers)
        for row in items: sheet.append([row.get(k) for k in headers])
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str): cell.data_type = 's'
        sheet.freeze_panes = 'A2'
    wb.save(outdir/'schedules_REVIEW.xlsx')
    report = report or validate_build(spec, build, data)
    result = report.as_dict()
    result.update(part_count=len(build.parts), operations_count=len(build.operations), part_ids=[p.part_id for p in build.registry],
                  blocking_issues=[f.message for f in report.findings if f.severity in ('BLOCKER','ERROR')])
    for name, obj in [('review_report.json',result),('validation_report.json',result),('schedules_REVIEW.json',data),
                      ('parts_registry.json',build.registry.snapshot()),
                      ('machining_operations_REVIEW.json',[o.model_dump(mode='json') for o in build.operations])]:
        (outdir/name).write_text(json.dumps(obj, indent=2), encoding='utf-8')
    (outdir/'assembly_review.md').write_text(
        '# '+spec.family.title()+' revision '+str(spec.revision)+' — REVIEW ONLY\n\n'
        'Verify physical measurements, actual stock, blank allowances, all connections, back attachment, '
        'frame joinery, glass retention, hinge mounting and opening sweep. Structural and safety verification '
        'requires qualified engineering. Follow the blocking findings in validation_report.json.\n\n'
        '**Not shop-floor assembly instructions. No machine instructions are generated.**\n',encoding='utf-8')
    (outdir/'specification.json').write_text(spec.model_dump_json(indent=2),encoding='utf-8')
    return data
