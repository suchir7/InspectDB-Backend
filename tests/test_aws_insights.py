import unittest.mock as mock

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi.testclient import TestClient

from app.services.aws_insights import (
    STATIC_RATES,
    WEEKS_PER_MONTH,
    AwsInsightsService,
    describe_aws_error,
    parse_cron,
    weekly_running_hours,
)

CLUSTER = {
    "cluster_id": "inspectdb", "region": "us-east-1", "status": "available", "engine_version": "5.0.0",
    "storage_type": "standard", "backup_retention_days": 1, "storage_encrypted": True,
    "deletion_protection": True, "created_at": "2026-10-03T18:21:14+00:00",
    "instances": [{"id": "inspectdb", "instance_class": "db.t3.medium", "status": "available", "availability_zone": "us-east-1a"}],
}
SCHEDULE = {
    "start": {"expression": "cron(0 9 ? * MON-FRI *)", "timezone": "Asia/Kolkata", "state": "ENABLED"},
    "stop": {"expression": "cron(0 21 ? * MON-FRI *)", "timezone": "Asia/Kolkata", "state": "ENABLED"},
    "active": True, "weekly_hours": 60.0, "monthly_hours": round(60 * WEEKS_PER_MONTH, 1),
    "description": "Mon–Fri 09:00–21:00 (Asia/Kolkata)",
}
METRICS = {
    "running_hours": 60, "billed_ios": 60_000, "operations": 12_000, "storage_gb": 0.0562,
    "cpu_avg_percent": 14.5, "cpu_peak_percent": 52.7, "cpu_credits_charged": 0.0, "daily": [],
}


@pytest.fixture
def service(tmp_path):
    with mock.patch.multiple("app.config.settings.settings", AWS_INSIGHTS_ENABLED=True,
                             DOCUMENTDB_CLUSTER_ID="inspectdb", INSIGHTS_CACHE_DIR=str(tmp_path)):
        yield AwsInsightsService()


def test_weekday_schedule_hours():
    assert parse_cron("cron(0 9 ? * MON-FRI *)") == (0, 9, {2, 3, 4, 5, 6})
    weekly = weekly_running_hours("cron(0 9 ? * MON-FRI *)", "cron(0 21 ? * MON-FRI *)")
    assert weekly == 60
    assert round(weekly * WEEKS_PER_MONTH, 1) == 260.9


def test_overnight_and_unsupported_schedules():
    assert weekly_running_hours("cron(0 22 ? * * *)", "cron(0 6 ? * * *)") == 56
    assert parse_cron("cron(0 9 15 * ? *)") is None  # day-of-month schedules are not weekly
    assert parse_cron("rate(1 hour)") is None


def test_billing_lines_map_to_project_components():
    classify = AwsInsightsService.classify_usage
    docdb = "Amazon DocumentDB (with MongoDB compatibility)"
    assert classify(docdb, "InstanceUsage:db.t3.medium") == "docdb_instance"
    assert classify(docdb, "StorageIOUsage") == "docdb_io"
    assert classify(docdb, "BackupUsage") == "docdb_backup"
    assert classify(docdb, "StorageUsage") == "docdb_storage"
    # Snapshots of other RDS engines (MySQL, Aurora) are not DocumentDB spend
    assert classify("Amazon Relational Database Service", "RDS:ChargedBackupUsage") == "rds_other"
    assert classify("Amazon Elastic Compute Cloud - Compute", "BoxUsage:t3.micro") == "ec2_instance"
    assert classify("EC2 - Other", "EBS:VolumeUsage.gp3") == "ec2_storage"
    assert classify("Amazon Virtual Private Cloud", "USE1-PublicIPv4:InUseAddress") == "public_ipv4"
    assert classify("AWS Glue", "USE1-Catalog-Request") == "other"


def test_cost_model_uses_real_usage_and_list_prices(service):
    profile = service.build_profile(CLUSTER, SCHEDULE, METRICS)
    assert profile["monthly_hours"] == 260.9
    assert profile["billed_ios_per_running_hour"] == 1000
    model = service.cost_model(profile, STATIC_RATES)
    parts = {c["key"]: c["monthly"] for c in model["components"]}
    assert parts["instance"] == round(0.078 * 260.9, 2)
    assert parts["io"] == round(1000 * 260.9 / 1_000_000 * 0.20, 4)
    assert parts["storage"] == round(0.0562 * 0.10, 4)
    # I/O-Optimized storage includes I/O
    assert next(c for c in service.cost_model(profile, STATIC_RATES, storage_type="iopt1")["components"] if c["key"] == "io")["monthly"] == 0


def test_recommendations_reflect_the_live_setup(service):
    profile = service.build_profile(CLUSTER, SCHEDULE, METRICS)
    recs = {r["id"]: r for r in service.build_recommendations(profile, STATIC_RATES, SCHEDULE, METRICS)}
    assert recs["schedule"]["status"] == "applied"
    # Savings = instance hours plus the I/O not incurred while the cluster is stopped
    assert recs["schedule"]["monthly_savings"] == round(0.078 * (730 - 260.9) + 1000 * (730 - 260.9) / 1e6 * 0.20, 2)
    assert recs["instance_class"]["status"] == "optional"  # db.t4g.medium is cheaper and CPU is low
    assert recs["storage_type"]["status"] == "not_needed"
    assert recs["replicas"]["status"] == "not_needed"
    assert recs["backups"]["status"] == "not_needed"


def test_always_on_cluster_gets_a_schedule_recommendation(service):
    profile = service.build_profile(CLUSTER, None, METRICS)
    assert profile["monthly_hours"] == 730
    recs = {r["id"]: r for r in service.build_recommendations(profile, STATIC_RATES, None, METRICS)}
    assert recs["schedule"]["status"] == "recommended"
    assert recs["schedule"]["monthly_savings"] > 30


def test_disabled_insights_explain_how_to_enable():
    with mock.patch.multiple("app.config.settings.settings", AWS_INSIGHTS_ENABLED=False, DOCUMENTDB_CLUSTER_ID=""):
        overview = AwsInsightsService().overview()
    assert overview["enabled"] is False and overview["available"] is False
    assert "AWS_INSIGHTS_ENABLED" in overview["reason"]
    assert overview["recommendations"] == [] and overview["model"] is None


def test_overview_reports_failing_sections_without_inventing_data(service):
    with mock.patch.object(service, "_fetch_cluster", return_value=CLUSTER), \
         mock.patch.object(service, "_fetch_schedule", return_value=SCHEDULE), \
         mock.patch.object(service, "_fetch_metrics", return_value=METRICS), \
         mock.patch.object(service, "_fetch_pricing", side_effect=NoCredentialsError()), \
         mock.patch.object(service, "_fetch_costs", side_effect=NoCredentialsError()):
        overview = service.overview()
    assert overview["available"] is True
    assert overview["sections"]["costs"]["data"] is None
    assert "no AWS credentials" in overview["sections"]["costs"]["error"]
    assert overview["rates"]["source"].startswith("Static rate card")
    assert overview["model"]["monthly_total"] > 0


def test_cached_sections_are_reused(service):
    with mock.patch.object(service, "_fetch_cluster", return_value=CLUSTER) as fetch:
        service._section("cluster", service._fetch_cluster)
        service._section("cluster", service._fetch_cluster)
    assert fetch.call_count == 1


def test_aws_errors_are_actionable():
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetCostAndUsage")
    assert "not allowed to call GetCostAndUsage" in describe_aws_error(denied)
    assert "Attach the read-only InspectDB role" in describe_aws_error(NoCredentialsError())


def test_live_endpoint_requires_login_and_respects_allow_list():
    from app.main import app
    from app.api.deps import get_current_user

    client = TestClient(app)
    assert client.get("/api/cost/live").status_code == 401

    app.dependency_overrides[get_current_user] = lambda: mock.MagicMock(email="viewer@example.com")
    try:
        with mock.patch.multiple("app.config.settings.settings", AWS_INSIGHTS_ENABLED=False, INSIGHTS_ALLOWED_EMAILS=""):
            body = client.get("/api/cost/live").json()
            assert body["enabled"] is False
        with mock.patch("app.config.settings.settings.INSIGHTS_ALLOWED_EMAILS", "owner@example.com"):
            assert client.get("/api/cost/live").status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
