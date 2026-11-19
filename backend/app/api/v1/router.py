from fastapi import APIRouter

from app.api.v1.endpoints import auth, workflows, executions, approvals, integrations, github


router = APIRouter()
router.include_router(auth.router)
router.include_router(workflows.router)
router.include_router(executions.router)
router.include_router(approvals.router)
router.include_router(integrations.router)
router.include_router(github.router)
