"""Authenticated source, history and handling actions for operational cases."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from . import casework_service as service
from .operations_evidence import actor_field
from .auth import current_user

router = APIRouter(prefix='/operational-cases', tags=['业务处置'])


class HandlingAction(BaseModel):
    action: Literal['submit', 'approve', 'return']
    revision: int = Field(ge=0)
    note: str = Field(min_length=4, max_length=500)
    outcome: str = Field(default='', max_length=40)
    reference: str = Field(default='', max_length=120)


@router.get('')
def catalog(user=Depends(current_user)):
    return {
        'role': actor_field(user, 'role'),
        'workflows': {code: {
            'title': workflow['title'], 'reference_label': workflow['reference_label'],
            'outcomes': {value: label for value, (label, _) in workflow['outcomes'].items()},
        } for code, workflow in service.WORKFLOWS.items()},
    }


@router.get('/{workflow}/sources')
def sources(workflow: str, user=Depends(current_user)):
    return service.source_rows(service.workflow_for(workflow))


@router.get('/{workflow}/history')
def history(workflow: str, key: str = Query(min_length=1, max_length=100), user=Depends(current_user)):
    return service.case_view(workflow, key)


@router.post('/{workflow}/actions', status_code=201)
def act(workflow: str, action: HandlingAction, key: str = Query(min_length=1, max_length=100), user=Depends(current_user)):
    return service.change(workflow, key, action, user)
