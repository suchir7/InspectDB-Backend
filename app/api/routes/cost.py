from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.cost import (
    WorkloadInput,
    CostEstimateResponse,
    CostAnalysisRequest,
    CostAnalysisResult,
    CostDriverDetail,
    CostAnomalyReport,
    OptimizationSimulationRequest,
    OptimizationSimulationResponse,
    CostMonitoringAnalysisRequest,
    CostMonitoringAnalysisResponse
)
from app.services.cost_calculator import CostCalculator
from app.services.cost_optimizer_service import cost_optimizer_service
from app.services.cost_monitoring_service import cost_monitoring_service
from app.services.aws_insights import aws_insights_service

router = APIRouter(prefix="/cost", tags=["AI Cost Optimizer & Monitoring"])

# =========================================================
# EXISTING COST OPTIMIZER ENDPOINTS
# =========================================================

@router.post("/estimate", response_model=CostEstimateResponse)
async def calculate_cost_estimate(workload: WorkloadInput):
    """
    Computes deterministic AWS DocumentDB cost estimates and comparative
    deployment tiers based on input workload parameters.
    """
    return CostCalculator.calculate_workload_costs(workload)

@router.post("/analyze", response_model=CostAnalysisResult)
async def analyze_cost_and_deployment(request: CostAnalysisRequest):
    """
    Invokes Google Gemini to evaluate workload metrics, cost drivers,
    potential waste, and actionable architectural recommendations.
    """
    return await cost_optimizer_service.analyze_workload(
        workload=request.workload,
        force_refresh=request.force_refresh
    )

# =========================================================
# LIVE AWS DATA (DocumentDB configuration, usage, prices, billed spend)
# =========================================================

# Sync handler: boto3 calls run in the threadpool instead of blocking the event loop
@router.get("/live")
def get_live_cost_overview(refresh: bool = False, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Live DocumentDB cluster profile, start/stop schedule, CloudWatch usage, AWS list prices,
    Cost Explorer spend, a cost model, what-if scenarios and data-backed recommendations.
    Sections that cannot be read report why instead of returning estimates.
    """
    if not aws_insights_service.user_allowed(current_user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is not allowed to view AWS cost data.")
    return aws_insights_service.overview(refresh=refresh)


# =========================================================
# PLANNING CALCULATIONS & GEMINI ANALYSIS
# =========================================================

@router.post("/monitoring/drivers", response_model=List[CostDriverDetail])
async def get_cost_drivers(workload: WorkloadInput):
    """
    Deconstructs workload factors and returns exact cost drivers and % contribution.
    """
    estimate = CostCalculator.calculate_workload_costs(workload)
    return CostCalculator.calculate_cost_drivers(workload, estimate)

@router.post("/monitoring/anomalies", response_model=CostAnomalyReport)
async def check_cost_anomalies(request: CostMonitoringAnalysisRequest):
    """
    Detects cost increases or architectural deviations between current workload and baseline.
    """
    return CostCalculator.detect_cost_anomalies(request.current_workload, request.baseline_workload)

@router.post("/monitoring/simulate", response_model=OptimizationSimulationResponse)
async def simulate_optimization_impact(request: OptimizationSimulationRequest):
    """
    Performs what-if optimization calculations comparing current vs proposed changes.
    """
    return CostCalculator.simulate_optimization(request)

@router.post("/monitoring/analyze", response_model=CostMonitoringAnalysisResponse)
async def analyze_cost_monitoring(request: CostMonitoringAnalysisRequest):
    """
    Runs Gemini Cost Analyst evaluation on current metrics, trends, and budget thresholds.
    """
    return await cost_monitoring_service.analyze_monitoring(request)
