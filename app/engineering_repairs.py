"""Small declarative repairs merged into a saved whole-furniture plan locally."""
from pydantic import Field
from app.photo_schema import PhotoModel,PhotoDesign,PhotoPart,ResolvedValue,SeparateFurnitureItem
from app.pipeline import canonical_hash


class EngineeringRepairPatch(PhotoModel):
    parts: list[PhotoPart] = Field(default_factory=list,max_length=60)
    values: list[ResolvedValue] = Field(default_factory=list,max_length=60)
    remove_part_ids: list[str] = Field(default_factory=list,max_length=60)
    separate_items: list[SeparateFurnitureItem] | None = None
    summary: str = Field(min_length=1,max_length=1200)
    assumptions: list[str] = Field(default_factory=list,max_length=15)


def merge_repair(source,patch,known):
    """Untouched parts/values remain byte-for-byte the saved declaration.

    Actual whole-model solid, envelope, collision and contact checks still run
    after this merge; a small response never bypasses the full CAD validator.
    """
    original=PhotoDesign.model_validate(source);raw=original.model_dump(mode='json')
    ids=[p.part_id for p in patch.parts];removed=patch.remove_part_ids
    if len(ids)!=len(set(ids)) or len(removed)!=len(set(removed)) or set(ids)&set(removed):
        raise ValueError('Repair part replacements and removals must be unique and disjoint')
    existing={p.part_id for p in original.parts}
    if not set(removed)<=existing:raise ValueError('A repair cannot remove an unknown part')
    replacements={p.part_id:p.model_dump(mode='json') for p in patch.parts}
    raw['parts']=[replacements.get(p['part_id'],p) for p in raw['parts'] if p['part_id'] not in removed]
    raw['parts'].extend(p.model_dump(mode='json') for p in patch.parts if p.part_id not in existing)
    supplied={v['key']:v['value'] for v in known};keys=[v.key for v in patch.values]
    if len(keys)!=len(set(keys)):raise ValueError('A repair parameter was supplied twice')
    values={v['key']:v for v in raw['values']}
    for value in patch.values:
        if value.key in supplied and value.value!=supplied[value.key]:raise ValueError('A repair cannot change supplied dimension '+value.key)
        values[value.key]=value.model_dump(mode='json')
    raw['values']=list(values.values())
    if patch.separate_items is not None:raw['separate_items']=[item.model_dump(mode='json') for item in patch.separate_items]
    if all(raw[key]==original.model_dump(mode='json').get(key) for key in ('parts','values','separate_items') if key in raw):
        raise ValueError('The correction did not change any geometry or parameter')
    raw['assumptions']=list(dict.fromkeys([*patch.assumptions,*raw['assumptions']]))[:30]
    result=PhotoDesign.model_validate(raw)
    if canonical_hash(result.model_dump(mode='json'))==canonical_hash(original.model_dump(mode='json')):
        raise ValueError('The correction did not change the design')
    return result
