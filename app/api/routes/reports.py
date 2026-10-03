from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends, status
from app.services.inspection_service import inspection_service
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.report import (
    ReportResponse,
    ReportListResponse,
    ReportCreate,
    ReportUpdate
)

router = APIRouter(prefix="/reports", tags=["Inspection Reports"])

@router.get("", response_model=ReportListResponse)
async def list_reports(
    search: Optional[str] = Query(None, description="Search across ID, title, inspector, location"),
    category: Optional[str] = Query(None, description="Filter by category"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    severity: Optional[str] = Query(None, description="Filter by overall severity"),
    sort_by: str = Query("inspection_date", description="Field to sort by"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_user)
):
    """
    List inspection reports strictly owned by the authenticated user.
    """
    reports, total = await inspection_service.list_reports(
        user_id=current_user.id,
        search=search,
        category=category,
        status=status_filter,
        severity=severity,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        limit=limit
    )
    return ReportListResponse(
        total=total,
        page=page,
        limit=limit,
        reports=[ReportResponse(**r) for r in reports]
    )

@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve single report only if owned by the authenticated user.
    Returns 404 if report does not exist or belongs to another user.
    """
    report = await inspection_service.get_report_by_id(report_id, user_id=current_user.id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inspection report with ID '{report_id}' was not found."
        )
    return ReportResponse(**report)

@router.post("", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    report_in: ReportCreate,
    current_user: User = Depends(get_current_user)
):
    """
    Create a new inspection report explicitly owned by the authenticated user.
    """
    created = await inspection_service.create_report(report_in, user_id=current_user.id)
    return ReportResponse(**created)

@router.put("/{report_id}", response_model=ReportResponse)
async def update_report(
    report_id: str,
    report_in: ReportUpdate,
    current_user: User = Depends(get_current_user)
):
    """
    Update an existing report only if owned by the authenticated user.
    """
    updated = await inspection_service.update_report(report_id, report_in, user_id=current_user.id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inspection report with ID '{report_id}' was not found."
        )
    return ReportResponse(**updated)

@router.delete("/{report_id}", status_code=status.HTTP_200_OK)
async def delete_report(
    report_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Delete an inspection report only if owned by the authenticated user.
    """
    deleted = await inspection_service.delete_report(report_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inspection report with ID '{report_id}' was not found."
        )
    return {"message": f"Report '{report_id}' was successfully deleted.", "report_id": report_id}
