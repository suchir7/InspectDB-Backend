import pytest
from unittest.mock import patch, MagicMock
from app.schemas.cost import (
    WorkloadInput,
    CostMonitoringAnalysisRequest,
    OptimizationSimulationRequest
)
from app.services.cost_calculator import CostCalculator
from app.services.cost_monitoring_service import cost_monitoring_service
from app.config.settings import settings

@pytest.fixture
def standard_dev_workload():
    return WorkloadInput(
        requests_per_day=5000,
        read_percentage=85.0,
        write_percentage=15.0,
        avg_document_size_kb=8.0,
        data_storage_gb=15.0,
        backup_retention_days=7,
        monthly_uptime_hours=160,
        environment_tier="development",
        selected_deployment="scheduled_dev"
    )

@pytest.fixture
def unoptimized_24_7_workload():
    return WorkloadInput(
        requests_per_day=15000,
        read_percentage=70.0,
        write_percentage=30.0,
        avg_document_size_kb=12.0,
        data_storage_gb=50.0,
        backup_retention_days=21,
        monthly_uptime_hours=730,
        environment_tier="development",
        selected_deployment="provisioned_single_az"
    )

def test_cost_drivers_attribution(unoptimized_24_7_workload):
    estimate = CostCalculator.calculate_workload_costs(unoptimized_24_7_workload)
    drivers = CostCalculator.calculate_cost_drivers(unoptimized_24_7_workload, estimate)

    assert len(drivers) >= 4
    # Highest contributor should be first
    assert drivers[0].monthly_cost_contribution >= drivers[1].monthly_cost_contribution
    driver_names = [d.driver_name for d in drivers]
    assert any("Runtime" in name for name in driver_names)
    assert any("Storage" in name for name in driver_names)
    assert any("I/O" in name for name in driver_names)

def test_cost_anomaly_detection(standard_dev_workload, unoptimized_24_7_workload):
    # Comparing 24/7 vs standard dev
    anomaly = CostCalculator.detect_cost_anomalies(
        current_workload=unoptimized_24_7_workload,
        baseline_workload=standard_dev_workload
    )
    assert anomaly.has_anomaly is True
    assert anomaly.change_direction == "increase"
    assert anomaly.change_percent > 50.0
    assert anomaly.dollar_difference > 0
    assert len(anomaly.contributing_factors) > 0

    # Comparing same workload should be stable
    stable = CostCalculator.detect_cost_anomalies(
        current_workload=standard_dev_workload,
        baseline_workload=standard_dev_workload
    )
    assert stable.has_anomaly is False
    assert stable.change_direction == "stable"
    assert stable.change_percent == 0.0

def test_optimization_simulation(unoptimized_24_7_workload):
    req = OptimizationSimulationRequest(
        current_workload=unoptimized_24_7_workload,
        proposed_uptime_hours=160,
        proposed_deployment="scheduled_dev"
    )
    res = CostCalculator.simulate_optimization(req)
    assert res.simulated_cost < res.current_cost
    assert res.dollar_difference > 0
    assert res.percentage_savings > 0
    assert "reduces estimated cost" in res.explanation

@pytest.mark.asyncio
async def test_monitoring_analysis_deterministic_fallback(standard_dev_workload):
    req = CostMonitoringAnalysisRequest(
        current_workload=standard_dev_workload,
        threshold=20.0
    )
    with patch.object(settings, "GEMINI_API_KEY", ""), patch.object(settings, "GEMINI_BACKUP_API_KEY", ""):
        res = await cost_monitoring_service.analyze_monitoring(req)
        assert res.is_ai_powered is False
        assert res.current_monthly_cost > 0
        assert res.daily_cost > 0
        assert res.budget_status.status in ["within", "near", "exceeded"]
        assert len(res.top_cost_drivers) >= 4
        assert len(res.recommended_actions) >= 1

@pytest.mark.asyncio
async def test_monitoring_analysis_mocked_gemini(standard_dev_workload):
    mock_ai_json = """{
      "summary": "Mocked AI evaluation: Workload is cost-effective under scheduled mode.",
      "cost_drivers": ["Scheduled Compute Runtime", "Document Storage"],
      "changes_detected": ["Stable scheduled baseline"],
      "optimization_opportunities": [
        {
          "title": "Enable Storage Archiving",
          "reason": "Old documents accumulate storage",
          "impact": "Low",
          "tradeoff": "Requires S3 export",
          "action": "Archive older reports",
          "suggested_change": "Archive after 90 days",
          "priority": "Low"
        }
      ],
      "deployment_considerations": ["Scheduled dev instance"],
      "risks": ["Cold start delay"],
      "recommended_actions": [{"step": 1, "action": "Verify cron", "rationale": "Uptime guarantee"}]
    }"""
    mock_response = MagicMock()
    mock_response.text = mock_ai_json

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    req = CostMonitoringAnalysisRequest(
        current_workload=standard_dev_workload,
        threshold=20.0
    )

    with patch.object(settings, "GEMINI_API_KEY", "mock-key-123"), \
         patch.object(settings, "GEMINI_BACKUP_API_KEY", ""), \
         patch("app.services.cost_monitoring_service.genai.Client", return_value=mock_client):
        res = await cost_monitoring_service.analyze_monitoring(req)
        assert res.is_ai_powered is True
        assert "Mocked AI evaluation" in res.summary
        assert len(res.optimization_opportunities) == 1


