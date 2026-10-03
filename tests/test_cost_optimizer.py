import pytest
import json
from unittest.mock import MagicMock, patch

from app.schemas.cost import (
    WorkloadInput,
    CostEstimateResponse,
    CostAnalysisResult,
    Recommendation
)
from app.services.cost_calculator import CostCalculator
from app.services.cost_optimizer_service import CostOptimizerService

def test_cost_calculator_local_dev_is_zero():
    workload = WorkloadInput(
        requests_per_day=10000,
        data_storage_gb=50.0,
        selected_deployment="local_dev"
    )
    result = CostCalculator.calculate_workload_costs(workload)
    local_opt = next(opt for opt in result.comparison_options if opt.id == "local_dev")
    assert local_opt.monthly_cost == 0.0
    assert local_opt.breakdown.compute_cost == 0.0
    assert local_opt.breakdown.storage_cost == 0.0
    assert local_opt.breakdown.io_cost == 0.0
    assert local_opt.breakdown.backup_cost == 0.0

def test_cost_calculator_scheduled_dev_vs_continuous():
    workload = WorkloadInput(
        requests_per_day=5000,
        read_percentage=80.0,
        write_percentage=20.0,
        data_storage_gb=20.0,
        backup_retention_days=7,
        monthly_uptime_hours=160,
        region="us-east-1"
    )
    result = CostCalculator.calculate_workload_costs(workload)
    
    scheduled_opt = next(opt for opt in result.comparison_options if opt.id == "scheduled_dev")
    continuous_opt = next(opt for opt in result.comparison_options if opt.id == "provisioned_single_az")
    multi_az_opt = next(opt for opt in result.comparison_options if opt.id == "provisioned_multi_az")

    # 160h compute vs 730h compute
    assert scheduled_opt.breakdown.compute_cost < continuous_opt.breakdown.compute_cost
    assert round(160 * 0.078, 2) == scheduled_opt.breakdown.compute_cost
    assert round(730 * 0.078, 2) == continuous_opt.breakdown.compute_cost
    
    # Multi-AZ compute is 2x continuous compute
    assert multi_az_opt.breakdown.compute_cost == round(continuous_opt.breakdown.compute_cost * 2, 2)

    # Storage and I/O are calculated deterministically
    assert continuous_opt.breakdown.storage_cost == 2.00  # 20GB * $0.10
    assert continuous_opt.breakdown.io_cost > 0.0

    # Potential savings calculated properly
    assert result.potential_monthly_savings >= 0.0

def test_cost_calculator_regional_multiplier():
    workload_us = WorkloadInput(region="us-east-1", data_storage_gb=10.0)
    workload_eu = WorkloadInput(region="eu-west-1", data_storage_gb=10.0)

    res_us = CostCalculator.calculate_workload_costs(workload_us)
    res_eu = CostCalculator.calculate_workload_costs(workload_eu)

    single_us = next(opt for opt in res_us.comparison_options if opt.id == "provisioned_single_az")
    single_eu = next(opt for opt in res_eu.comparison_options if opt.id == "provisioned_single_az")

    # EU is 1.10x US
    assert single_eu.monthly_cost > single_us.monthly_cost
    assert round(single_us.breakdown.compute_cost * 1.10, 2) == single_eu.breakdown.compute_cost

@pytest.mark.asyncio
async def test_cost_optimizer_caching_and_history():
    service = CostOptimizerService()
    workload = WorkloadInput(requests_per_day=5000, data_storage_gb=10.0)

    # Patch is_configured to False for instant unit test execution
    with patch("app.services.cost_optimizer_service.ai_query_service.is_configured", return_value=False):
        # 1. First run generates fresh analysis and adds to history
        res1 = await service.analyze_workload(workload)
        assert res1.is_cached is False
        assert len(service.get_history()) == 1
        analysis_id = res1.analysis_id

        # 2. Subsequent run with same inputs hits cache
        res2 = await service.analyze_workload(workload, force_refresh=False)
        assert res2.is_cached is True
        assert res2.analysis_id == analysis_id

        # 3. Force refresh bypasses cache
        res3 = await service.analyze_workload(workload, force_refresh=True)
        assert res3.is_cached is False

        # 4. History tracks analyses
        history = service.get_history()
        assert len(history) >= 2

        # 5. Recommendation status update
        first_rec = history[0].recommendations[0]
        rec_id = first_rec.id
        assert first_rec.status == "pending"

        updated = service.update_recommendation_status(rec_id, "applied")
        assert updated is True
        assert history[0].recommendations[0].status == "applied"

        # 6. Clear history
        service.clear_history()
        assert len(service.get_history()) == 0

@pytest.mark.asyncio
async def test_cost_optimizer_mocked_gemini_parsing():
    service = CostOptimizerService()
    workload = WorkloadInput(requests_per_day=8000, data_storage_gb=30.0)
    estimate = CostCalculator.calculate_workload_costs(workload)

    mock_gemini_json = json.dumps({
        "cost_drivers": ["Idle nightly compute hours", "Document BSON storage"],
        "identified_waste": ["Running 24/7 during zero-traffic weekends"],
        "gemini_summary": "Implementing a scheduled cluster saves significant cost for student project testing.",
        "recommendations": [
            {
                "id": "rec-gemini-01",
                "title": "Use Lambda Scheduled Stop",
                "category": "scheduling",
                "impact": "high",
                "explanation": "Stops cluster compute after hours.",
                "proposed_action": "Set up EventBridge rule.",
                "estimated_monthly_savings": 44.50,
                "trade_offs": ["Startup delay"],
                "implementation_steps": ["Deploy Lambda function"]
            }
        ],
        "missing_information": ["Peak inspector concurrency"]
    })

    result = service._parse_gemini_response(
        raw_text=mock_gemini_json,
        analysis_id="TEST-001",
        timestamp="2026-10-03T11:00:00Z",
        workload=workload,
        estimate=estimate
    )

    assert result.analysis_id == "TEST-001"
    assert len(result.recommendations) == 1
    assert result.recommendations[0].id == "rec-gemini-01"
    assert result.recommendations[0].estimated_monthly_savings == 44.50
    assert result.cost_drivers[0] == "Idle nightly compute hours"
    assert "scheduled" in result.gemini_summary.lower()
    assert "runtime_hours" in result.recommendations[0].why_context

def test_cost_health_calculation():
    workload = WorkloadInput(requests_per_day=25000, monthly_uptime_hours=730, selected_deployment="provisioned_single_az")
    res = CostCalculator.calculate_workload_costs(workload)
    assert res.cost_health is not None
    assert res.cost_health.main_cost_driver == "Compute"
    assert res.cost_health.current_cost > 50.0
    assert len(res.cost_health.deterministic_insights) >= 2
    assert "Continuous" in res.cost_health.usage_pattern

