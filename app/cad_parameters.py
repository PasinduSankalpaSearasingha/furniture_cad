"""Named, bounded CAD controls with component ownership and measurement locks."""
import ast
from dataclasses import asdict, dataclass, field
import numpy as np
from app.photo_schema import PhotoDesign, number


def references(value):
    result = set()
    if isinstance(value, str):
        try:
            result.update(n.id for n in ast.walk(ast.parse(value, mode='eval')) if isinstance(n, ast.Name))
        except SyntaxError:
            pass
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ('label', 'evidence', 'material', 'geometry', 'part_id'):
                result.update(references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(references(item))
    return result


@dataclass
class CADParameter:
    parameter_id: str
    component_ids: list[str]
    current_value: float
    minimum: float
    maximum: float
    unit: str
    user_confirmed: bool
    optimize: bool
    phase: str
    evidence_ids: list[str] = field(default_factory=list)
    uncertainty: float | None = None
    path: tuple = ()

    def report(self):
        return asdict(self)


class CADParameterRegistry:
    def __init__(self, plan, known_values, options):
        self.source = PhotoDesign.model_validate(plan)
        self.options = options
        self.parameters = {}
        self.known = {v['key']: v['value'] for v in known_values}
        all_ids = {p.part_id for p in self.source.parts}
        selected = set(options.component_ids)
        if options.mismatch_feature_ids:
            mapped = set()
            for feature in options.mismatch_feature_ids:
                if feature not in options.feature_components:
                    raise ValueError('A mismatched feature has no component mapping: ' + feature)
                mapped.update(options.feature_components[feature])
            selected = selected & mapped if selected else mapped
            if not selected:
                raise ValueError('No components are associated with the selected mismatches')
        self.selected = selected or all_ids
        if not self.selected <= all_ids:
            raise ValueError('Optimization references unknown components')
        raw = self.source.model_dump(mode='json')
        ownership = {p.part_id: references(p.model_dump(mode='json')) for p in self.source.parts}
        for value in self.source.values:
            owners = sorted(pid for pid, names in ownership.items() if value.key in names)
            unit = 'degrees' if value.key.endswith('_deg') else 'count' if value.key.endswith('_count') else 'mm'
            phase = 'global' if value.key in ('width_mm', 'depth_mm', 'height_mm') else 'component'
            span = options.max_rotation_deg if unit == 'degrees' else max(1., abs(value.value) * options.max_relative_change)
            confirmed = value.key in self.known
            eligible = bool(owners) and set(owners) <= self.selected and unit != 'count'
            minimum = value.value - span
            if unit == 'mm' and value.value > 0:
                minimum = max(.2, minimum)
            self._add(CADParameter(value.key, owners, value.value, minimum, value.value + span,
                unit, confirmed, eligible and not confirmed, phase, path=('values', value.key)))
        # Numeric controls retain stable identifiers; expressions keep named dependencies.
        fields = ('position', 'rotation_deg', 'size', 'corner_radius', 'edge_radius',
                  'outline', 'path', 'sections', 'sweep_sections', 'surface_grid', 'surface_nurbs')
        source_values = {v.key: v.value for v in self.source.values}
        def walk(value, path, part):
            expression = isinstance(value, str)
            if expression:
                try:
                    numeric = number(value, source_values)
                except ValueError:
                    return  # Geometry enums such as an ellipse profile are not controls.
            else:
                numeric = value
            if (expression or isinstance(value, (int, float))) and not isinstance(value, bool):
                value = numeric
                root = path[2]
                if root == 'sweep_sections' and path[-1] == 'fraction':
                    return
                phase = 'position' if root in ('position', 'rotation_deg') else 'curve' if root in (
                    'path', 'outline', 'sections', 'sweep_sections', 'surface_grid', 'surface_nurbs') else 'component'
                angle = root == 'rotation_deg'
                span = options.max_rotation_deg if angle else options.max_displacement_mm
                positive = root in ('size', 'corner_radius', 'edge_radius') or path[-1] in ('width', 'depth', 'corner_radius')
                rational_weight = root=='surface_nurbs' and 'weights' in path
                if rational_weight:
                    span=max(.05,abs(value)*options.max_relative_change)
                if positive:
                    span = max(.1, abs(value) * options.max_relative_change)
                minimum = max(.0 if 'radius' in str(path[-1]) else .2, value - span) if positive else value - span
                if rational_weight: minimum=max(.05,value-span)
                pid = part['part_id'] + '.' + '.'.join(map(str, path[2:]))
                dependencies = references(part_value(raw, path))
                locked_dependencies = dependencies & self.known.keys() if dependencies <= self.known.keys() else set()
                # Local translation offsets may vary within the fixed outer
                # envelope. Measured member dimensions/angles remain locked.
                if root == 'position':
                    locked_dependencies -= {'width_mm', 'depth_mm', 'height_mm'}
                confirmed = bool(locked_dependencies)
                self._add(CADParameter(pid, [part['part_id']], float(value), minimum, value + span,
                    'dimensionless' if rational_weight else 'degrees' if angle else 'mm', confirmed, part['part_id'] in self.selected and not confirmed,
                    phase, path=path))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, (*path, index), part)
            elif isinstance(value, dict):
                for key, item in value.items():
                    walk(item, (*path, key), part)
        for index, part in enumerate(raw['parts']):
            for key in fields:
                if key in part:
                    walk(part[key], ('parts', index, key), part)
        unknown = set(options.parameter_ids) - self.parameters.keys()
        if unknown:
            raise ValueError('Unknown CAD parameter identifiers: ' + ', '.join(sorted(unknown)))
        overrides = {p.parameter_id: p for p in options.parameters}
        if set(overrides) - self.parameters.keys():
            raise ValueError('Unknown CAD parameter override')
        for pid, parameter in self.parameters.items():
            override = overrides.get(pid)
            if override:
                if override.minimum is not None:
                    parameter.minimum = max(parameter.minimum, override.minimum)
                if override.maximum is not None:
                    parameter.maximum = min(parameter.maximum, override.maximum)
                parameter.optimize &= override.optimize
                parameter.uncertainty = override.uncertainty
                parameter.evidence_ids = list(override.evidence_ids)
            parameter.evidence_ids = sorted(set(parameter.evidence_ids + [fid for fid, ids in options.feature_components.items()
                if set(ids) & set(parameter.component_ids)]))
            if not parameter.minimum <= parameter.current_value <= parameter.maximum:
                raise ValueError('Parameter bounds exclude its initial value: ' + pid)
            parameter.optimize &= parameter.maximum > parameter.minimum
            if options.parameter_ids:
                parameter.optimize &= pid in options.parameter_ids
        for relation in options.relations:
            if relation.left not in self.parameters or relation.right not in self.parameters:
                raise ValueError('Unknown parameter in symmetry/coupling constraint')
            if abs(self.parameters[relation.left].current_value - relation.factor *
                    self.parameters[relation.right].current_value - relation.offset) > relation.tolerance:
                raise ValueError('Initial CAD violates a required parameter relation')
            # The left-hand value is derived, never an independent search variable.
            self.parameters[relation.left].optimize = False

    def _add(self, parameter):
        self.parameters[parameter.parameter_id] = parameter

    def candidates(self):
        order = {'global': 0, 'component': 1, 'position': 2, 'curve': 3}
        return sorted((p for p in self.parameters.values() if p.optimize),
                      key=lambda p: (order[p.phase], not bool(p.evidence_ids), p.parameter_id))

    def apply(self, updates):
        raw = self.source.model_dump(mode='json')
        assigned = dict(updates)
        for relation in self.options.relations:
            right = assigned.get(relation.right, self.parameters[relation.right].current_value)
            expected = relation.factor * right + relation.offset
            left = self.parameters[relation.left]
            if left.user_confirmed or not set(left.component_ids) <= self.selected:
                if abs(expected - left.current_value) > relation.tolerance:
                    raise ValueError('A symmetry constraint would change a locked parameter')
            assigned[relation.left] = expected
        for pid, value in assigned.items():
            parameter = self.parameters[pid]
            if parameter.user_confirmed and value != parameter.current_value:
                raise ValueError('User-confirmed parameter is locked: ' + pid)
            if not parameter.minimum - 1e-9 <= value <= parameter.maximum + 1e-9:
                raise ValueError('CAD parameter exceeds bounds: ' + pid)
            if parameter.path[0] == 'values':
                next(v for v in raw['values'] if v['key'] == pid)['value'] = float(value)
            else:
                node = raw
                for key in parameter.path[:-1]:
                    node = node[key]
                original = part_value(self.source.model_dump(mode='json'), parameter.path)
                if isinstance(original, str):
                    delta = value - parameter.current_value
                    node[parameter.path[-1]] = original if abs(delta) < 1e-12 else f'({original}) + ({delta:.12g})'
                else:
                    node[parameter.path[-1]] = float(value)
        self._preserve_continuity(raw)
        result = PhotoDesign.model_validate(raw)
        values = {v.key: v.value for v in result.values}
        if any(values.get(key) != value for key, value in self.known.items()):
            raise ValueError('Fixed CAD measurements changed')
        # Named values can also move expression-backed local controls. Check
        # their resolved bounds as well as the directly assigned search vector.
        for parameter in self.parameters.values():
            actual = values[parameter.parameter_id] if parameter.path[0] == 'values' else number(part_value(raw, parameter.path), values)
            if not parameter.minimum - 1e-8 <= actual <= parameter.maximum + 1e-8:
                raise ValueError('A dependent CAD control exceeds its allowed bounds')
            if parameter.user_confirmed and abs(actual - parameter.current_value) > 1e-8:
                raise ValueError('A confirmed component dimension or angle changed')
        for before, after in zip(self.source.parts, result.parts):
            if before.part_id not in self.selected and before != after:
                raise ValueError('An unrelated CAD component changed')
        for relation in self.options.relations:
            def current(pid):
                parameter = self.parameters[pid]
                if parameter.path[0] == 'values':
                    return values[pid]
                node = raw
                for key in parameter.path:
                    node = node[key]
                return number(node, values)
            if abs(current(relation.left) - relation.factor * current(relation.right) - relation.offset) > relation.tolerance:
                raise ValueError('Candidate breaks a symmetry or continuity constraint')
        return result

    def _preserve_continuity(self, raw):
        """Preserve existing smooth cubic joins and closed outline endpoints.

        Coupled handles retain their named dependencies through bounded offsets.
        Confirmed controls cannot be changed to satisfy continuity.
        """
        baseline = {v.key: v.value for v in self.source.values}
        current = {v['key']: v['value'] for v in raw['values']}
        for part, changed in zip(self.source.parts, raw['parts']):
            if part.curve_mode != 'bezier' or part.part_id not in self.selected:
                continue
            for field_name in ('path', 'outline'):
                points = getattr(part, field_name)
                if not points:
                    continue
                previous = np.array([[number(x, baseline) for x in p] for p in points])
                target = changed[field_name]
                def assign(index, axis, value):
                    existing = target[index][axis]
                    if isinstance(existing, str):
                        if abs(number(existing, current) - value) > 1e-6:
                            pid = f'{part.part_id}.{field_name}.{index}.{axis}'
                            parameter = self.parameters[pid]
                            if parameter.user_confirmed or not parameter.minimum <= value <= parameter.maximum:
                                raise ValueError('Continuity adjustment would change a locked curve control')
                            original = getattr(part, field_name)[index][axis]
                            delta = value - number(original, current)
                            target[index][axis] = f'({original}) + ({delta:.12g})'
                    else:
                        pid = f'{part.part_id}.{field_name}.{index}.{axis}'
                        parameter = self.parameters[pid]
                        if not parameter.minimum <= value <= parameter.maximum:
                            raise ValueError('Curve continuity exceeds control-point bounds')
                        target[index][axis] = float(value)
                if field_name == 'outline' and np.allclose(previous[0], previous[-1]):
                    for axis in range(2):
                        assign(len(target) - 1, axis, number(target[0][axis], current))
                joins = [(j, j-1, j+1) for j in range(3, len(points) - 1, 3)]
                if field_name == 'outline' and np.allclose(previous[0], previous[-1]):
                    joins.append((0, len(points)-2, 1))
                for join, incoming, outgoing in joins:
                    a, b = previous[join] - previous[incoming], previous[outgoing] - previous[join]
                    ratio = np.linalg.norm(b) / max(np.linalg.norm(a), 1e-12)
                    if ratio > 1e-8 and np.linalg.norm(a) > 1e-8 and np.allclose(b, a * ratio, atol=1e-6):
                        for axis in range(len(previous[0])):
                            assign(outgoing, axis, number(target[join][axis], current) + ratio *
                                (number(target[join][axis], current) - number(target[incoming][axis], current)))


def part_value(raw, path):
    for key in path:
        raw = raw[key]
    return raw
