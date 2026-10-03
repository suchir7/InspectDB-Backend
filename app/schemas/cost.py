from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class WorkloadInput(BaseModel):
    requests_per_day: int = Field(default=5000, ge=100, le=10000000, description="Expected API/Database requests per day")
    read_percentage: float = Field(default=85.0, ge=0.0, le=100.0, description="Percentage of read queries (vs writes)")
    write_percentage: float = Field(default=15.0, ge=0.0, le=100.0, description="Percentage of write operations (vs reads)")
    avg_document_size_kb: float = Field(default=8.0, ge=0.5, le=16000.0, description="Average JSON document size in KB")
    data_storage_gb: float = Field(default=15.0, ge=1.0, le=10000.0, description="Total active document data size in GB")
    backup_retention_days: int = Field(default=7, ge=1, le=35, description="Backup retention window in days")
    monthly_uptime_hours: int = Field(default=730, ge=0, le=730, description="Active running hours per month (730 = 24/7, 160 = 8h/weekday)")
    environment_tier: str = Field(default="development", description="development | demo | production")
    traffic_pattern: str = Field(default="steady", description="steady | bursty | batch | intermittent")
    availability_tier: str = Field(default="single_az", description="single_az | multi_az")
    region: str = Field(default="us-east-1", description="AWS Region (e.g., us-east-1, us-west-2, eu-west-1, ap-south-1)")
    selected_deployment: str = Field(default="scheduled_dev", description="local_dev | scheduled_dev | provisioned_single_az | provisioned_multi_az | serverless_elastic")

class CostBreakdown(BaseModel):
    compute_cost: float = Field(..., description="Monthly compute instance cost ($)")
    storage_cost: float = Field(..., description="Monthly active document storage cost ($)")
    io_cost: float = Field(..., description="Monthly request I/O cost ($)")
    backup_cost: float = Field(..., description="Monthly additional backup storage cost ($)")
    total_monthly_cost: float = Field(..., description="Total estimated monthly cost ($)")
    currency: str = "USD"
    hourly_rate_effective: float
    pricing_source: str = "AWS DocumentDB Standard Pricing (US East / N. Virginia baseline, Q1 2026)"
    assumptions: List[str] = Field(default_factory=list)
    included_components: List[str] = Field(default_factory=list)
    excluded_components: List[str] = Field(default_factory=list)

class DeploymentOptionEstimate(BaseModel):
    id: str
    name: str
    instance_type: str
    node_count: int
    monthly_uptime_hours: int
    high_availability: bool
    monthly_cost: float
    breakdown: CostBreakdown
    suitability: str
    scalability: str
    operational_complexity: str = "low | medium | high"
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    recommended_for: str

class Recommendation(BaseModel):
    id: str
    title: str
    category: str = Field(default="compute", description="compute | scheduling | storage | architecture | monitoring")
    impact: str = Field(default="medium", description="high | medium | low")
    explanation: str
    proposed_action: str
    reason: Optional[str] = None
    estimated_impact: Optional[str] = None
    tradeoff: Optional[str] = None
    action: Optional[str] = None
    estimated_monthly_savings: Optional[float] = None
    trade_offs: List[str] = Field(default_factory=list)
    implementation_steps: List[str] = Field(default_factory=list)
    why_context: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="pending", description="pending | applied | dismissed")

class CostHealth(BaseModel):
    current_cost: float
    potential_optimization_percent: float
    main_cost_driver: str
    usage_pattern: str
    health_summary: str
    deterministic_insights: List[str] = Field(default_factory=list)

class CostEstimateResponse(BaseModel):
    workload: WorkloadInput
    selected_deployment: DeploymentOptionEstimate
    comparison_options: List[DeploymentOptionEstimate]
    potential_monthly_savings: float
    cost_health: Optional[CostHealth] = None
    pricing_metadata: Dict[str, Any]

class CostAnalysisRequest(BaseModel):
    workload: WorkloadInput
    force_refresh: bool = False

class CostAnalysisResult(BaseModel):
    analysis_id: str
    timestamp: str
    workload: WorkloadInput
    selected_estimate: DeploymentOptionEstimate
    comparison_options: List[DeploymentOptionEstimate]
    potential_monthly_savings: float
    cost_health: Optional[CostHealth] = None
    cost_drivers: List[str]
    identified_waste: List[str]
    optimization_opportunities: List[str] = Field(default_factory=list)
    deployment_observations: List[str] = Field(default_factory=list)
    tradeoffs: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommended_next_steps: List[str] = Field(default_factory=list)
    gemini_summary: str
    recommendations: List[Recommendation]
    missing_information: List[str]
    is_cached: bool = False
    is_demo_mode: bool = True
    gemini_model: str = "gemini-2.5-flash"

class RecommendationStatusUpdate(BaseModel):
    status: str = Field(..., description="pending | applied | dismissed")

class CostTrendPoint(BaseModel):
    date: str
    day_number: int
    daily_cost: float
    cumulative_cost: float
    projected_monthly_cost: float
    compute_cost: float
    storage_cost: float
    io_cost: float
    backup_cost: float

class CostTrendResponse(BaseModel):
    timeframe: str = "30d"  # 7d | 30d | 90d
    current_daily_cost: float
    projected_monthly_cost: float
    points: List[CostTrendPoint]
    assumptions: str = "Simulated cost trend — based on current workload assumptions"

class CostDriverDetail(BaseModel):
    driver_name: str
    current_value: str
    monthly_cost_contribution: float
    percentage_of_total: float
    impact_level: str = Field(default="Medium", description="High | Medium | Low")
    optimization_opportunity: str
    potential_savings: float = 0.0

class CostAnomalyReport(BaseModel):
    has_anomaly: bool = False
    change_direction: str = "stable"  # increase | decrease | stable
    change_percent: float = 0.0
    dollar_difference: float = 0.0
    baseline_cost: float = 0.0
    current_cost: float = 0.0
    contributing_factors: List[str] = Field(default_factory=list)
    severity: str = "info"  # info | warning | critical
    detected_rule: str = "Normal Workload Baseline"

class BudgetStatus(BaseModel):
    monthly_threshold: float = 20.0
    current_estimate: float
    remaining_budget: float
    utilization_percent: float
    status: str = "within"  # within | near | exceeded

class CostMonitoringSnapshot(BaseModel):
    snapshot_id: str
    timestamp: str
    title: str
    workload: WorkloadInput
    deployment_tier: str
    monthly_cost: float
    daily_cost: float
    breakdown: CostBreakdown
    drivers: List[CostDriverDetail] = Field(default_factory=list)
    optimization_opportunity_percent: float = 0.0

class CostComparisonReport(BaseModel):
    baseline_title: str
    current_title: str
    baseline_cost: float
    current_cost: float
    cost_difference: float
    percentage_difference: float
    breakdown_diff: Dict[str, float] = Field(default_factory=dict)
    deterministic_reasons: List[str] = Field(default_factory=list)
    gemini_explanation: Optional[str] = None

class OptimizationSimulationRequest(BaseModel):
    current_workload: WorkloadInput
    proposed_uptime_hours: Optional[int] = None
    proposed_storage_gb: Optional[float] = None
    proposed_deployment: Optional[str] = None
    proposed_backup_days: Optional[int] = None

class OptimizationSimulationResponse(BaseModel):
    current_cost: float
    simulated_cost: float
    dollar_difference: float
    percentage_savings: float
    explanation: str
    tradeoffs: List[str] = Field(default_factory=list)
    simulated_breakdown: CostBreakdown

class CostMonitoringAnalysisRequest(BaseModel):
    current_workload: WorkloadInput
    baseline_workload: Optional[WorkloadInput] = None
    threshold: float = 20.0
    force_refresh: bool = False

class CostMonitoringAnalysisResponse(BaseModel):
    analysis_id: str
    timestamp: str
    summary: str
    current_monthly_cost: float
    daily_cost: float
    projected_monthly_cost: float
    optimization_opportunity_percent: float
    budget_status: BudgetStatus
    breakdown: CostBreakdown
    top_cost_drivers: List[CostDriverDetail]
    anomaly_detection: CostAnomalyReport
    cost_drivers: List[str] = Field(default_factory=list)
    changes_detected: List[str] = Field(default_factory=list)
    optimization_opportunities: List[Dict[str, Any]] = Field(default_factory=list)
    deployment_considerations: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommended_actions: List[Dict[str, Any]] = Field(default_factory=list)
    is_ai_powered: bool = False
    pricing_assumptions: Dict[str, Any] = Field(default_factory=dict)

