from fastapi import APIRouter, Depends
from app.services.inspection_service import inspection_service
from app.schemas.report import DashboardStats
from app.api.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/stats", tags=["Dashboard Statistics"])

@router.get("", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user)
):
    """
    Compute dashboard metrics aggregated strictly for the authenticated user's reports.
    """
    stats = await inspection_service.get_dashboard_stats(user_id=current_user.id)
    return DashboardStats(**stats)
