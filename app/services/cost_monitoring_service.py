import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.schemas.cost import (
    WorkloadInput,
    CostMonitoringAnalysisRequest,
    CostMonitoringAnalysisResponse,
    OptimizationSimulationRequest,
    BudgetStatus
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
    Gemini cost analysis of a workload profile (built from live AWS data on the Cost
    Monitoring page), with a deterministic fallback when Gemini is not configured.
    """

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

        # Optimization opportunity: savings from a weekday 09:00-21:00 schedule versus the current uptime
        always_on_cost = next((o.monthly_cost for o in estimate.comparison_options if o.id == "provisioned_single_az"), monthly_cost)
        weekday_hours = round(12 * 5 * 365.25 / 12 / 7)
        if request.current_workload.monthly_uptime_hours > weekday_hours:
            opp_pct = CostCalculator.simulate_optimization(OptimizationSimulationRequest(
                current_workload=request.current_workload, proposed_uptime_hours=weekday_hours
            )).percentage_savings
        else:
            opp_pct = 0.0

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
            estimate.pricing_metadata,
            uptime_hours=request.current_workload.monthly_uptime_hours,
            threshold=request.threshold,
            always_on_cost=always_on_cost
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
        pricing_metadata: Dict[str, Any],
        uptime_hours: int,
        threshold: float,
        always_on_cost: float
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
                "impact": f"Current estimate ${monthly_cost:.2f}/mo at {uptime_hours} running hours.",
                "tradeoff": "None.",
                "action": "Continue monitoring monthly storage growth and request I/O expansion.",
                "suggested_change": "No immediate changes required.",
                "priority": "Low"
            })

        recs = [
            {"step": 1, "action": f"Keep the start/stop schedule in place ({uptime_hours} h/mo).", "rationale": "Prevents idle 24/7 compute accumulation outside working hours."},
            {"step": 2, "action": "Audit TTL indexes on high-frequency IoT inspection telemetry.", "rationale": "Prevents active document storage bloat beyond free backup thresholds."},
            {"step": 3, "action": f"Set an AWS Budgets alert at ${threshold:.2f}/month.", "rationale": "Provides proactive notification before the budget threshold is exceeded."}
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
                "Local MongoDB or the in-memory store allows schema and query testing without cluster charges.",
                "A single scheduled db.t3.medium instance balances DocumentDB compatibility testing and cost."
            ],
            risks=[
                f"Running this workload 24/7 would cost about ${always_on_cost:.2f}/mo per instance.",
                "High-frequency unindexed nested queries can increase monthly I/O charges."
            ],
            recommended_actions=recs,
            is_ai_powered=False,
            pricing_assumptions=pricing_metadata
        )

cost_monitoring_service = CostMonitoringService()
