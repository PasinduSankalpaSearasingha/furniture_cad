"""Conservative, explicit transitions. No client-supplied state mutation."""
from enum import Enum
from fastapi import HTTPException


class DesignState(str, Enum):
    DRAFT='DRAFT'
    SPECIFICATION_PENDING='SPECIFICATION_PENDING'
    SPECIFICATION_VERIFIED='SPECIFICATION_VERIFIED'
    CAD_GENERATED='CAD_GENERATED'
    VALIDATION_PENDING='VALIDATION_PENDING'
    VALIDATION_FAILED='VALIDATION_FAILED'
    DESIGNER_REVIEW='DESIGNER_REVIEW'
    CHANGES_REQUESTED='CHANGES_REQUESTED'
    APPROVED='APPROVED'
    PRODUCTION_RELEASED='PRODUCTION_RELEASED'


TRANSITIONS = {
    'DRAFT': {'SPECIFICATION_PENDING','SPECIFICATION_VERIFIED'},
    'SPECIFICATION_PENDING': {'SPECIFICATION_VERIFIED','CAD_GENERATED','CHANGES_REQUESTED'},
    'SPECIFICATION_VERIFIED': {'CAD_GENERATED','CHANGES_REQUESTED'},
    'CAD_GENERATED': {'VALIDATION_PENDING'},
    'VALIDATION_PENDING': {'VALIDATION_FAILED','DESIGNER_REVIEW'},
    'VALIDATION_FAILED': {'CAD_GENERATED','CHANGES_REQUESTED'},
    'DESIGNER_REVIEW': {'APPROVED','CHANGES_REQUESTED','CAD_GENERATED'},
    'CHANGES_REQUESTED': set(),  # Corrections create a new immutable specification revision.
    'APPROVED': {'PRODUCTION_RELEASED'},
    'PRODUCTION_RELEASED': set(),
}


def transition(revision, state, session, actor):
    from .database import audit
    state = state.value if isinstance(state, DesignState) else state
    if state not in TRANSITIONS.get(revision.state,set()):
        raise HTTPException(409, f'Invalid transition {revision.state} -> {state}')
    old = revision.state
    revision.state = state
    audit(session,actor,'state_changed',revision.id,previous=old,current=state)


def ensure_approvable(revision, job):
    if not revision.verified_by or revision.verified_spec_hash != revision.spec_hash:
        raise HTTPException(409,'Designer verification of this exact specification is required')
    if revision.state != 'DESIGNER_REVIEW' or job is None or job.state != 'SUCCEEDED':
        raise HTTPException(409,'Completed validation and designer review are required')
    if (not job.validation or job.validation.get('has_blockers',True) or
            any(f.get('severity') in ('BLOCKER','ERROR') for f in job.validation.get('findings',[]))):
        raise HTTPException(409,'Unresolved validation issues prevent approval')
