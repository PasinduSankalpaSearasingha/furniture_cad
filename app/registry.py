"""The single source of dimensions for CAD, schedules and drawings (millimetres)."""
from collections.abc import Iterator
from .models import Part


class PartsRegistry:
    def __init__(self):
        self._parts: dict[str, Part] = {}

    def add(self, part: Part):
        if part.part_id in self._parts:
            raise ValueError(f'Duplicate part ID: {part.part_id}')
        self._parts[part.part_id] = part

    def __getitem__(self, part_id: str) -> Part:
        return self._parts[part_id]

    def __iter__(self) -> Iterator[Part]:
        return iter(self._parts.values())

    def __len__(self):
        return len(self._parts)

    def snapshot(self):
        return [p.model_dump(mode='json') for p in self]
