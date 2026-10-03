import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.schemas.cost import (
    WorkloadInput,
    CostMonitoringSnapshot,
    CostMonitoringAnalysisRequest,
    CostMonitoringAnalysisResponse,
    CostComparisonReport,
    BudgetStatus,
    CostTrendResponse
)
from app.services.cost_calculator import CostCalculator
from app.services.ai_service import ai_query_service
from app.config.settings import settings

try:
    from google import genai
except ImportError:
    genai = None

logger = logging.getLogger(__name__)

GEMINI_MONITORING_SYSTEM_PROMPT = """You are an expert AWS FinOps & DocumentDB Cost Monitoring Specialist.
You are evaluating a deterministic cost monitoring report for an application using Amazon DocumentDB (MongoDB-compatible).

### Objectives:
1. Explain what is happening to the estimated database cost over time based on the provided numbers.
2. Identify the primary cost drivers (compute runtime, active storage, request I/O, backup retention).
3. Detect and explain significant changes between periods or deviations from best practices.
4. Provide structured, actionable, and explainable optimization opportunities.
5. Detail concrete trade-offs and operational risks for each recommendation.

### CRITICAL RULES:
- DO NOT invent prices or claim access to live AWS billing data. All numbers come directly from the deterministic calculator input provided.
- DO NOT claim that you modified or inspected live AWS infrastructure.
- Always use the provided numerical estimates.
- Structure recommendations clearly with title, reason, impact, tradeoff, and action.
- All recommendations must be advisory.

### Required JSON Output Format:
You MUST respond with a single valid JSON object following this exact schema:
{
  "summary": "2-3 sentence executive explanation of the current cost trajectory, cost drivers, and key optimization areas.",
  "cost_drivers": [
    "Explanation of top cost driver 1",
    "Explanation of top cost driver 2"
  ],
  "changes_detected": [
    "Detected workload or cost change 1",
    "Detected workload or cost change 2"
  ],
  "optimization_opportunities": [
    {
      "title": "Clear recommendation title",
      "reason": "Root workload factor causing this expenditure",
      "impact": "Potential reduction in compute/storage charges",
      "tradeoff": "Operational or availability trade-off",
      "action": "Specific recommended change",
      "suggested_change": "Switch from 730 hrs to 160 hrs scheduled runtime",
      "priority": "High | Medium | Low"
    }
  ],
  "deployment_considerations": [
    "Deployment consideration 1",
    "Deployment consideration 2"
  ],
  "risks": [
    "Operational risk 1",
    "Operational risk 2"
  ],
  "recommended_actions": [
    {
      "step": 1,
      "action": "Immediate action to execute",
      "rationale": "Why this should be done first"
    }
  ]
}
"""

class CostMonitoringService:
    """
    Manages AWS DocumentDB continuous cost monitoring, trend tracking,
    anomaly detection, period-over-period comparisons, and Gemini cost analysis.
    """

    def __init__(self):
        self._snapshots: Dict[str, CostMonitoringSnapshot] = {}
        self._init_default_snapshots()

    def _init_default_snapshots(self):
        """Initializes a few realistic baseline snapshots for simulation demonstrations."""
        # Baseline: Unoptimized 24/7 Dev Workload
        base_workload = WorkloadInput(
            requests_per_day=5000,
            read_percentage=85.0,
            write_percentage=15.0,
            data_storage_gb=15.0,
            backup_retention_days=14,
            monthly_uptime_hours=730,
            environment_tier="development",
            selected_deployment="provisioned_single_az"
        )
        base_est = CostCalculator.calculate_workload_costs(base_workload)
        base_drivers = CostCalculator.calculate_cost_drivers(base_workload, base_est)

        snap1 = CostMonitoringSnapshot(
            snapshot_id="snap-baseline-24-7",
            timestamp="2026-09-15T08:00:00Z",
            title="Baseline: Continuous 24/7 Dev Cluster",
            workload=base_workload,
            deployment_tier="provisioned_single_az",
            monthly_cost=base_est.selected_deployment.monthly_cost,
            daily_cost=round(base_est.selected_deployment.monthly_cost / 30.0, 2),
            breakdown=base_est.selected_deployment.breakdown,
            drivers=base_drivers,
            optimization_opportunity_percent=76.1
        )
        self._snapshots[snap1.snapshot_id] = snap1

        # Snapshot 2: Scheduled Dev Mode
        opt_workload = WorkloadInput(
            requests_per_day=5000,
            read_percentage=85.0,
            write_percentage=15.0,
            data_storage_gb=15.0,
            backup_retention_days=7,
            monthly_uptime_hours=160,
            environment_tier="development",
            selected_deployment="scheduled_dev"
        )
        opt_est = CostCalculator.calculate_workload_costs(opt_workload)
        opt_drivers = CostCalculator.calculate_cost_drivers(opt_workload, opt_est)

        snap2 = CostMonitoringSnapshot(
            snapshot_id="snap-scheduled-dev",
            timestamp="2026-10-01T09:30:00Z",
            title="Optimized: 8h/Weekday Scheduled Dev",
            workload=opt_workload,
            deployment_tier="scheduled_dev",
            monthly_cost=opt_est.selected_deployment.monthly_cost,
            daily_cost=round(opt_est.selected_deployment.monthly_cost / 30.0, 2),
            breakdown=opt_est.selected_deployment.breakdown,
            drivers=opt_drivers,
            optimization_opportunity_percent=0.0
        )
        self._snapshots[snap2.snapshot_id] = snap2

    def get_snapshots(self) -> List[CostMonitoringSnapshot]:
        return sorted(self._snapshots.values(), key=lambda s: s.timestamp, reverse=True)

    def save_snapshot(self, title: str, workload: WorkloadInput) -> CostMonitoringSnapshot:
        est = CostCalculator.calculate_workload_costs(workload)
        drivers = CostCalculator.calculate_cost_drivers(workload, est)
        monthly_cost = est.selected_deployment.monthly_cost

        # Calculate opportunity percent
        single_az_cost = 58.44
        opp_pct = max(0.0, round(((single_az_cost - monthly_cost) / single_az_cost) * 100, 1)) if monthly_cost < single_az_cost else 0.0

        snapshot = CostMonitoringSnapshot(
            snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            title=title or f"Snapshot {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            workload=workload,
            deployment_tier=workload.selected_deployment,
            monthly_cost=monthly_cost,
            daily_cost=round(monthly_cost / 30.0, 2),
            breakdown=est.selected_deployment.breakdown,
            drivers=drivers,
            optimization_opportunity_percent=opp_pct
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def delete_snapshot(self, snapshot_id: str) -> bool:
        if snapshot_id in self._snapshots:
            del self._snapshots[snapshot_id]
            return True
        return False

    async def analyze_monitoring(
        self,
        request: CostMonitoringAnalysisRequest
    ) -> CostMonitoringAnalysisResponse:
        """
        Performs continuous cost monitoring analysis, combining deterministic pricing metrics
        with Gemini 2.5 Flash architectural interpretation and plain-English insights.
        """
        analysis_id = f"cmon-{uuid.uuid4().hex[:8]}"
        timestamp = datetime.now(timezone.utc).isoformat()

        # 1. Deterministic Cost Calculations
        estimate = CostCalculator.calculate_workload_costs(request.current_workload)
        monthly_cost = estimate.selected_deployment.monthly_cost
        daily_cost = round(monthly_cost / 30.0, 2)
        projected_cost = monthly_cost
        breakdown = estimate.selected_deployment.breakdown

        # 2. Drivers and Anomaly Detection
        drivers = CostCalculator.calculate_cost_drivers(request.current_workload, estimate)
        anomaly = CostCalculator.detect_cost_anomalies(request.current_workload, request.baseline_workload)

        # 3. Budget & Threshold Status
        utilization = round((monthly_cost / request.threshold) * 100, 1) if request.threshold > 0 else 100.0
        budget_status_val = "within" if utilization < 80.0 else "near" if utilization <= 100.0 else "exceeded"
        remaining = round(max(0.0, request.threshold - monthly_cost), 2)

        budget = BudgetStatus(
            monthly_threshold=request.threshold,
            current_estimate=monthly_cost,
            remaining_budget=remaining,
            utilization_percent=utilization,
            status=budget_status_val
        )

        # Optimization Opportunity Score
        # If running 24/7 in dev, high opportunity. If running scheduled dev, 0-10% opportunity.
        single_az_cost = 58.44
        opp_pct = max(0.0, round(((monthly_cost - 13.98) / max(0.01, monthly_cost)) * 100, 1)) if monthly_cost > 14.0 and request.current_workload.environment_tier == "development" else 0.0

        # 4. Invoke Gemini Cost Analyst (or Fallback)
        raw_json_text = None
        gemini_keys = settings.get_gemini_api_keys()

        if gemini_keys:
            prompt = self._build_gemini_monitoring_prompt(
                request.current_workload,
                estimate,
                drivers,
                anomaly,
                budget,
                request.baseline_workload
            )
            for api_key in gemini_keys:
                if not genai:
                    break
                try:
                    client = genai.Client(api_key=api_key)
                    resp = client.models.generate_content(
                        model=settings.GEMINI_MODEL,
                        contents=prompt,
                        config={
                            "system_instruction": GEMINI_MONITORING_SYSTEM_PROMPT,
                            "temperature": 0.1,
                            "response_mime_type": "application/json"
                        }
                    )
                    if resp and resp.text:
                        raw_json_text = resp.text
                        break
                except Exception as e:
                    logger.warning(f"Gemini cost monitoring analysis failed with key: {e}")

        if raw_json_text:
            parsed_ai = self._parse_gemini_monitoring_response(raw_json_text)
            return CostMonitoringAnalysisResponse(
                analysis_id=analysis_id,
                timestamp=timestamp,
                summary=parsed_ai.get("summary", f"Estimated AWS DocumentDB cost is ${monthly_cost:.2f}/mo (${daily_cost:.2f}/day), currently {budget_status_val} configured budget."),
                current_monthly_cost=monthly_cost,
                daily_cost=daily_cost,
                projected_monthly_cost=projected_cost,
                optimization_opportunity_percent=opp_pct,
                budget_status=budget,
                breakdown=breakdown,
                top_cost_drivers=drivers,
                anomaly_detection=anomaly,
                cost_drivers=parsed_ai.get("cost_drivers", [d.driver_name for d in drivers[:3]]),
                changes_detected=parsed_ai.get("changes_detected", anomaly.contributing_factors),
                optimization_opportunities=parsed_ai.get("optimization_opportunities", []),
                deployment_considerations=parsed_ai.get("deployment_considerations", []),
                risks=parsed_ai.get("risks", []),
                recommended_actions=parsed_ai.get("recommended_actions", []),
                is_ai_powered=True,
                pricing_assumptions=estimate.pricing_metadata
            )

        # Deterministic Fallback Response
        return self._build_deterministic_monitoring_response(
            analysis_id,
            timestamp,
            monthly_cost,
            daily_cost,
            projected_cost,
            opp_pct,
            budget,
            breakdown,
            drivers,
            anomaly,
            estimate.pricing_metadata
        )

    def _build_gemini_monitoring_prompt(
        self,
        workload: WorkloadInput,
        estimate: Any,
        drivers: List[Any],
        anomaly: Any,
        budget: BudgetStatus,
        baseline: Optional[WorkloadInput]
    ) -> str:
        prompt_data = {
            "current_workload": workload.model_dump(),
            "monthly_cost": estimate.selected_deployment.monthly_cost,
            "breakdown": estimate.selected_deployment.breakdown.model_dump(),
            "top_cost_drivers": [d.model_dump() for d in drivers],
            "budget_threshold": budget.model_dump(),
            "anomaly_analysis": anomaly.model_dump(),
            "baseline_workload": baseline.model_dump() if baseline else None
        }
        return f"Deterministic AWS DocumentDB Cost Monitoring Input for Interpretation:\n{json.dumps(prompt_data, indent=2)}"

    def _parse_gemini_monitoring_response(self, raw_text: str) -> Dict[str, Any]:
        try:
            cleaned = raw_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except Exception as e:
            logger.error(f"Failed to parse Gemini monitoring JSON: {e}")
            return {}

    def _build_deterministic_monitoring_response(
        self,
        analysis_id: str,
        timestamp: str,
        monthly_cost: float,
        daily_cost: float,
        projected_cost: float,
        opp_pct: float,
        budget: BudgetStatus,
        breakdown: Any,
        drivers: List[Any],
        anomaly: Any,
        pricing_metadata: Dict[str, Any]
    ) -> CostMonitoringAnalysisResponse:
        summary = (
            f"Current estimated DocumentDB workload costs ${monthly_cost:.2f}/month (${daily_cost:.2f}/day). "
            f"Budget utilization is {budget.utilization_percent:.1f}% against the ${budget.monthly_threshold:.2f}/mo threshold "
            f"({budget.status.upper()}). "
            f"Primary cost driver is {drivers[0].driver_name if drivers else 'Compute Runtime'} ({drivers[0].percentage_of_total if drivers else 85}% of total)."
        )

        opt_opps = []
        for d in drivers:
            if d.potential_savings > 0:
                opt_opps.append({
                    "title": f"Optimize {d.driver_name}",
                    "reason": f"Current allocation contributes ${d.monthly_cost_contribution:.2f}/mo ({d.percentage_of_total}% of total).",
                    "impact": f"Estimated savings of up to ${d.potential_savings:.2f}/mo under current pricing assumptions.",
                    "tradeoff": "Requires operational configuration adjustment or scheduled availability window.",
                    "action": d.optimization_opportunity,
                    "suggested_change": d.optimization_opportunity,
                    "priority": d.impact_level
                })

        if not opt_opps:
            opt_opps.append({
                "title": "Maintain Scheduled Optimization Posture",
                "reason": "Workload is currently operating near maximum cost efficiency for development.",
                "impact": "Stable low-cost operations ($0.00 mock / ~$13.98/mo scheduled).",
                "tradeoff": "None.",
                "action": "Continue monitoring monthly storage growth and request I/O expansion.",
                "suggested_change": "No immediate changes required.",
                "priority": "Low"
            })

        recs = [
            {"step": 1, "action": "Verify scheduled instance auto-start/stop cron configuration (160h/mo).", "rationale": "Prevents idle 24/7 compute accumulation outside business hours."},
            {"step": 2, "action": "Audit TTL indexes on high-frequency IoT inspection telemetry.", "rationale": "Prevents active document storage bloat beyond free backup thresholds."},
            {"step": 3, "action": "Set AWS CloudWatch Billing Alarm at $20.00/month.", "rationale": "Provides proactive notification before budget thresholds are exceeded."}
        ]

        return CostMonitoringAnalysisResponse(
            analysis_id=analysis_id,
            timestamp=timestamp,
            summary=summary,
            current_monthly_cost=monthly_cost,
            daily_cost=daily_cost,
            projected_monthly_cost=projected_cost,
            optimization_opportunity_percent=opp_pct,
            budget_status=budget,
            breakdown=breakdown,
            top_cost_drivers=drivers,
            anomaly_detection=anomaly,
            cost_drivers=[d.driver_name for d in drivers[:3]],
            changes_detected=anomaly.contributing_factors,
            optimization_opportunities=opt_opps,
            deployment_considerations=[
                "Local simulation allows full schema and query testing without incurring AWS cluster charges.",
                "Single-instance db.t3.medium scheduled development provides optimal balance of wire-compatibility and cost."
            ],
            risks=[
                "Unmonitored continuous runtime on provisioned clusters can accumulate ~$58.44/mo per instance.",
                "High-frequency unindexed nested queries can increase monthly I/O charges."
            ],
            recommended_actions=recs,
            is_ai_powered=False,
            pricing_assumptions=pricing_metadata
        )

    async def compare_snapshots(
        self,
        baseline_snapshot_id: str,
        current_snapshot_id: str
    ) -> CostComparisonReport:
        """
        Compares two stored snapshots deterministically and provides structured explanations.
        """
        baseline = self._snapshots.get(baseline_snapshot_id)
        current = self._snapshots.get(current_snapshot_id)

        if not baseline or not current:
            raise ValueError("One or both snapshots could not be found.")

        cost_diff = round(current.monthly_cost - baseline.monthly_cost, 2)
        pct_diff = round(((cost_diff / baseline.monthly_cost) * 100) if baseline.monthly_cost > 0 else 0.0, 1)

        breakdown_diff = {
            "compute_diff": round(current.breakdown.compute_cost - baseline.breakdown.compute_cost, 2),
            "storage_diff": round(current.breakdown.storage_cost - baseline.breakdown.storage_cost, 2),
            "io_diff": round(current.breakdown.io_cost - baseline.breakdown.io_cost, 2),
            "backup_diff": round(current.breakdown.backup_cost - baseline.breakdown.backup_cost, 2)
        }

        deterministic_reasons = []
        if current.workload.monthly_uptime_hours != baseline.workload.monthly_uptime_hours:
            sign = "+" if breakdown_diff['compute_diff'] >= 0 else "-"
            deterministic_reasons.append(f"Compute runtime changed from {baseline.workload.monthly_uptime_hours}h to {current.workload.monthly_uptime_hours}h/mo ({sign}${abs(breakdown_diff['compute_diff']):.2f}/mo)")
        if current.workload.data_storage_gb != baseline.workload.data_storage_gb:
            sign = "+" if breakdown_diff['storage_diff'] >= 0 else "-"
            deterministic_reasons.append(f"Storage allocation changed from {baseline.workload.data_storage_gb} GB to {current.workload.data_storage_gb} GB ({sign}${abs(breakdown_diff['storage_diff']):.2f}/mo)")
        if current.workload.selected_deployment != baseline.workload.selected_deployment:
            deterministic_reasons.append(f"Deployment tier changed from '{baseline.workload.selected_deployment}' to '{current.workload.selected_deployment}'")

        if not deterministic_reasons:
            deterministic_reasons.append("No material workload configuration differences between snapshots.")

        cost_sign = "+" if cost_diff >= 0 else "-"
        explanation = f"Estimated monthly cost changed from ${baseline.monthly_cost:.2f} to ${current.monthly_cost:.2f} ({cost_sign}${abs(cost_diff):.2f} / {pct_diff:+0.1f}%). Primary delta is driven by {deterministic_reasons[0]}."

        return CostComparisonReport(
            baseline_title=baseline.title,
            current_title=current.title,
            baseline_cost=baseline.monthly_cost,
            current_cost=current.monthly_cost,
            cost_difference=cost_diff,
            percentage_difference=pct_diff,
            breakdown_diff=breakdown_diff,
            deterministic_reasons=deterministic_reasons,
            gemini_explanation=explanation
        )

cost_monitoring_service = CostMonitoringService()
