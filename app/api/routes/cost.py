from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from app.schemas.cost import (
    WorkloadInput,
    CostEstimateResponse,
    CostAnalysisRequest,
    CostAnalysisResult,
    RecommendationStatusUpdate,
    CostTrendResponse,
    CostDriverDetail,
    CostAnomalyReport,
    CostMonitoringSnapshot,
    CostComparisonReport,
    OptimizationSimulationRequest,
    OptimizationSimulationResponse,
    CostMonitoringAnalysisRequest,
    CostMonitoringAnalysisResponse
)
from app.services.cost_calculator import CostCalculator
from app.services.cost_optimizer_service import cost_optimizer_service
from app.services.cost_monitoring_service import cost_monitoring_service

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

@router.get("/history", response_model=List[CostAnalysisResult])
async def get_cost_analysis_history():
    """
    Retrieves historical analysis snapshots, estimate comparisons,
    and recommendation status states.
    """
    return cost_optimizer_service.get_history()

@router.patch("/recommendations/{rec_id}/status")
async def update_recommendation_status(rec_id: str, update_in: RecommendationStatusUpdate):
    """
    Updates the operational status of a recommendation (pending, applied, dismissed).
    """
    if update_in.status not in ["pending", "applied", "dismissed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be one of: 'pending', 'applied', 'dismissed'."
        )

    updated = cost_optimizer_service.update_recommendation_status(rec_id, update_in.status)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation with ID '{rec_id}' was not found in active history."
        )

    return {"message": f"Recommendation '{rec_id}' status updated to '{update_in.status}'.", "id": rec_id, "status": update_in.status}

@router.delete("/history")
async def clear_cost_history():
    """
    Clears local analysis history and cached estimates.
    """
    cost_optimizer_service.clear_history()
    return {"message": "Cost analysis history and cache have been successfully cleared."}


# =========================================================
# AWS DOCUMENTDB COST MONITORING ENDPOINTS
# =========================================================

class TrendRequest(BaseModel):
    workload: WorkloadInput
    timeframe: str = Field(default="30d", description="7d | 30d | 90d")

class SnapshotCreateRequest(BaseModel):
    title: str
    workload: WorkloadInput

class SnapshotCompareRequest(BaseModel):
    baseline_snapshot_id: str
    current_snapshot_id: str

@router.post("/monitoring/trend", response_model=CostTrendResponse)
async def get_cost_trend(request: TrendRequest):
    """
    Returns deterministic daily and cumulative cost points across 7, 30, or 90 days.
    """
    return CostCalculator.calculate_cost_trend(request.workload, request.timeframe)

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

@router.get("/monitoring/snapshots", response_model=List[CostMonitoringSnapshot])
async def get_monitoring_snapshots():
    """
    Retrieves stored cost monitoring snapshots for tracking and comparisons.
    """
    return cost_monitoring_service.get_snapshots()

@router.post("/monitoring/snapshots", response_model=CostMonitoringSnapshot)
async def create_monitoring_snapshot(request: SnapshotCreateRequest):
    """
    Stores a new workload cost snapshot locally.
    """
    return cost_monitoring_service.save_snapshot(request.title, request.workload)

@router.delete("/monitoring/snapshots/{snapshot_id}")
async def delete_monitoring_snapshot(snapshot_id: str):
    """
    Deletes a cost monitoring snapshot.
    """
    deleted = cost_monitoring_service.delete_snapshot(snapshot_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot '{snapshot_id}' not found."
        )
    return {"message": f"Snapshot '{snapshot_id}' deleted successfully."}

@router.post("/monitoring/compare", response_model=CostComparisonReport)
async def compare_snapshots_endpoint(request: SnapshotCompareRequest):
    """
    Compares two saved snapshots and explains why cost changed.
    """
    try:
        return await cost_monitoring_service.compare_snapshots(
            request.baseline_snapshot_id,
            request.current_snapshot_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )

