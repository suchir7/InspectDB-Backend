"""
Live AWS data for the cost pages.

Reads the DocumentDB cluster configuration, its EventBridge start/stop schedule,
CloudWatch usage metrics, AWS list prices and Cost Explorer spend. Every AWS call is
read-only and cached. When AWS access is not configured, each section reports why it
is unavailable instead of falling back to invented numbers.
"""
import json
import logging
import re
import statistics
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.config.settings import settings

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730
WEEKS_PER_MONTH = 365.25 / 12 / 7

# Official AWS list prices for Amazon DocumentDB in us-east-1 (AWS Price List API, October 2026).
# Used only when the Price List API cannot be reached; responses always name the source.
STATIC_RATES: Dict[str, Any] = {
    "source": "Static rate card (AWS Price List, us-east-1, October 2026)",
    "region": "us-east-1",
    "instance_hourly": {
        "standard": {"db.t3.medium": 0.078, "db.t4g.medium": 0.07566, "db.r6g.large": 0.26315},
        "iopt1": {"db.t3.medium": 0.0858, "db.t4g.medium": 0.083226, "db.r6g.large": 0.289465},
    },
    "storage_gb_month": {"standard": 0.10, "iopt1": 0.30},
    "io_per_million": 0.20,
    "backup_gb_month": 0.021,
    "cpu_credit_vcpu_hour": 0.09,
}

CACHE_TTL_SECONDS = {"cluster": 300, "schedule": 900, "metrics": 900, "pricing": 86400, "costs": 12 * 3600}
# Cost Explorer charges $0.01 per request, so a manual refresh only re-queries it after this long
COSTS_MIN_REFRESH_SECONDS = 3600

COMPONENT_LABELS = {
    "docdb_instance": "DocumentDB instance hours",
    "docdb_storage": "DocumentDB storage",
    "docdb_io": "DocumentDB I/O requests",
    "docdb_backup": "DocumentDB backup storage",
    "docdb_cpu_credits": "DocumentDB CPU credits",
    "docdb_other": "DocumentDB (other)",
    "rds_other": "Amazon RDS (other databases and snapshots)",
    "ec2_instance": "API server (EC2)",
    "ec2_storage": "API server disk (EBS)",
    "public_ipv4": "Public IPv4 address",
    "other": "Other AWS services",
}
DOCUMENTDB_SERVICES = {"Amazon DocumentDB (with MongoDB compatibility)"}

DAY_NUMBERS = {"SUN": 1, "MON": 2, "TUE": 3, "WED": 4, "THU": 5, "FRI": 6, "SAT": 7}
DAY_NAMES = {v: k.title() for k, v in DAY_NUMBERS.items()}


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def describe_aws_error(error: Exception) -> str:
    """Turns boto3 errors into an actionable message for the UI."""
    name = type(error).__name__
    if name in ("NoCredentialsError", "PartialCredentialsError"):
        return ("The API server has no AWS credentials. Attach the read-only InspectDB role to the "
                "EC2 instance (deploy/backend-insights-role.yaml).")
    if name == "ClientError":
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
        operation = getattr(error, "operation_name", "this AWS call")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            return f"The server's AWS role is not allowed to call {operation}."
        if code in ("DBClusterNotFoundFault", "ResourceNotFoundException"):
            return f"AWS could not find the resource ({code})."
        return f"AWS returned {code or 'an error'} for {operation}."
    if name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"):
        return "Could not reach the AWS API from the server."
    if name == "ModuleNotFoundError":
        return "boto3 is not installed on the API server."
    return f"{name}: {str(error)[:160]}"


class _SharedCache:
    """TTL cache stored on local disk, so every uvicorn worker in the container shares one copy."""

    def __init__(self, directory: str):
        self._path = Path(directory) / "inspectdb-aws-insights-cache.json"
        self._lock = threading.Lock()
        self._memory: Dict[str, Dict[str, Any]] = {}

    def _read_disk(self) -> Dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            candidates = [e for e in (self._memory.get(key), self._read_disk().get(key)) if e]
        return max(candidates, key=lambda e: e["stored_at"]) if candidates else None

    def set(self, key: str, value: Any) -> Dict[str, Any]:
        entry = {"stored_at": time.time(), "value": value}
        with self._lock:
            self._memory[key] = entry
            data = self._read_disk()
            data[key] = entry
            try:
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
                tmp.replace(self._path)
            except Exception as e:
                logger.debug(f"Insights cache write skipped: {e}")
        return entry


# ---------------------------------------------------------------------------
# EventBridge Scheduler cron expressions -> running hours
# ---------------------------------------------------------------------------

def _parse_day(token: str) -> int:
    token = token.strip().upper()
    return int(token) if token.isdigit() else DAY_NUMBERS[token[:3]]


def parse_cron(expression: str) -> Optional[Tuple[int, int, Set[int]]]:
    """Parses simple weekly cron(min hour ? * DAYS *) expressions into (minute, hour, days)."""
    match = re.fullmatch(r"\s*cron\((.+)\)\s*", expression or "")
    if not match:
        return None
    fields = match.group(1).split()
    if len(fields) != 6:
        return None
    minute, hour, day_of_month, month, day_of_week, year = fields
    if not (minute.isdigit() and hour.isdigit()) or day_of_month not in ("?", "*") or month != "*" or year != "*":
        return None
    try:
        if day_of_week in ("*", "?"):
            days = set(range(1, 8))
        else:
            days: Set[int] = set()
            for part in day_of_week.split(","):
                if "-" in part:
                    first, last = (_parse_day(x) for x in part.split("-", 1))
                    days |= set(range(first, last + 1)) if first <= last else set(range(first, 8)) | set(range(1, last + 1))
                else:
                    days.add(_parse_day(part))
    except (KeyError, ValueError):
        return None
    return int(minute), int(hour), days


def weekly_running_hours(start_expression: str, stop_expression: str) -> Optional[float]:
    start, stop = parse_cron(start_expression), parse_cron(stop_expression)
    if not start or not stop:
        return None
    start_time = start[1] + start[0] / 60
    stop_time = stop[1] + stop[0] / 60
    total = 0.0
    for day in start[2]:
        if stop_time > start_time and day in stop[2]:
            total += stop_time - start_time
        elif stop_time <= start_time and (day % 7) + 1 in stop[2]:
            total += 24 - start_time + stop_time  # window crosses midnight
    return total


def describe_days(days: Set[int]) -> str:
    ordered = sorted(days, key=lambda d: (d + 5) % 7)  # Monday first
    if ordered == [2, 3, 4, 5, 6]:
        return "Mon–Fri"
    if len(ordered) == 7:
        return "Every day"
    return ", ".join(DAY_NAMES[d] for d in ordered)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AwsInsightsService:
    def __init__(self):
        self._cache = _SharedCache(settings.INSIGHTS_CACHE_DIR or tempfile.gettempdir())

    @property
    def region(self) -> str:
        return settings.AWS_REGION

    @property
    def cluster_id(self) -> str:
        return (settings.DOCUMENTDB_CLUSTER_ID or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(settings.AWS_INSIGHTS_ENABLED and self.cluster_id)

    def user_allowed(self, email: str) -> bool:
        allowed = {e.strip().lower() for e in (settings.INSIGHTS_ALLOWED_EMAILS or "").split(",") if e.strip()}
        return not allowed or (email or "").lower() in allowed

    def _client(self, service: str, region: Optional[str] = None):
        import boto3
        from botocore.config import Config
        return boto3.client(
            service,
            region_name=region or self.region,
            config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"}),
        )

    def _section(self, name: str, fetch: Callable[[], Any], refresh: bool = False, min_refresh_age: int = 0) -> Dict[str, Any]:
        cached = self._cache.get(name)
        age = time.time() - cached["stored_at"] if cached else None
        fresh = cached is not None and age < CACHE_TTL_SECONDS[name]
        if fresh and not (refresh and age > min_refresh_age):
            return {"data": cached["value"], "error": None, "fetched_at": _iso(cached["stored_at"])}
        try:
            entry = self._cache.set(name, fetch())
            return {"data": entry["value"], "error": None, "fetched_at": _iso(entry["stored_at"])}
        except Exception as e:
            logger.warning(f"AWS insights section '{name}' failed: {type(e).__name__}: {e}")
            # Serve the last good value (marked with the error) rather than nothing
            return {
                "data": cached["value"] if cached else None,
                "error": describe_aws_error(e),
                "fetched_at": _iso(cached["stored_at"]) if cached else None,
            }

    # --- fetchers -----------------------------------------------------------

    def _fetch_cluster(self) -> Dict[str, Any]:
        docdb = self._client("docdb")
        cluster = docdb.describe_db_clusters(DBClusterIdentifier=self.cluster_id)["DBClusters"][0]
        instances = docdb.describe_db_instances(
            Filters=[{"Name": "db-cluster-id", "Values": [self.cluster_id]}]
        )["DBInstances"]
        created = cluster.get("ClusterCreateTime")
        return {
            "cluster_id": self.cluster_id,
            "region": self.region,
            "status": cluster.get("Status"),
            "engine_version": cluster.get("EngineVersion"),
            "storage_type": cluster.get("StorageType") or "standard",
            "backup_retention_days": cluster.get("BackupRetentionPeriod"),
            "storage_encrypted": cluster.get("StorageEncrypted"),
            "deletion_protection": cluster.get("DeletionProtection"),
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            "instances": [
                {
                    "id": i["DBInstanceIdentifier"],
                    "instance_class": i["DBInstanceClass"],
                    "status": i.get("DBInstanceStatus"),
                    "availability_zone": i.get("AvailabilityZone"),
                }
                for i in instances
            ],
        }

    def _fetch_schedule(self) -> Dict[str, Any]:
        scheduler = self._client("scheduler")
        found: Dict[str, Optional[Dict[str, Any]]] = {"start": None, "stop": None}
        for page in scheduler.get_paginator("list_schedules").paginate():
            for summary in page.get("Schedules", []):
                arn = summary.get("Target", {}).get("Arn", "")
                kind = "start" if arn.endswith("docdb:startDBCluster") else "stop" if arn.endswith("docdb:stopDBCluster") else None
                if not kind:
                    continue
                detail = scheduler.get_schedule(Name=summary["Name"], GroupName=summary.get("GroupName", "default"))
                if self.cluster_id not in (detail.get("Target", {}).get("Input") or ""):
                    continue
                found[kind] = {
                    "name": detail["Name"],
                    "expression": detail.get("ScheduleExpression"),
                    "timezone": detail.get("ScheduleExpressionTimezone") or "UTC",
                    "state": detail.get("State"),
                }

        start, stop = found["start"], found["stop"]
        active = bool(start and stop and start["state"] == "ENABLED" and stop["state"] == "ENABLED")
        weekly = weekly_running_hours(start["expression"], stop["expression"]) if active else None
        description = None
        if active and weekly is not None:
            s, e = parse_cron(start["expression"]), parse_cron(stop["expression"])
            description = f"{describe_days(s[2])} {s[1]:02d}:{s[0]:02d}–{e[1]:02d}:{e[0]:02d} ({start['timezone']})"
        return {
            "start": start,
            "stop": stop,
            "active": active,
            "weekly_hours": round(weekly, 2) if weekly is not None else None,
            "monthly_hours": round(min(weekly * WEEKS_PER_MONTH, HOURS_PER_MONTH), 1) if weekly is not None else None,
            "description": description,
        }

    def _fetch_metrics(self) -> Dict[str, Any]:
        cloudwatch = self._client("cloudwatch")
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=7)
        stats = {
            "cpu_avg": ("CPUUtilization", "Average"),
            "cpu_max": ("CPUUtilization", "Maximum"),
            "connections": ("DatabaseConnections", "Average"),
            "volume_bytes": ("VolumeBytesUsed", "Average"),
            "read_ios": ("VolumeReadIOPs", "Sum"),
            "write_ios": ("VolumeWriteIOPs", "Sum"),
            "op_query": ("OpcountersQuery", "Sum"),
            "op_insert": ("OpcountersInsert", "Sum"),
            "op_update": ("OpcountersUpdate", "Sum"),
            "op_delete": ("OpcountersDelete", "Sum"),
            "op_getmore": ("OpcountersGetmore", "Sum"),
            "credits_charged": ("CPUSurplusCreditsCharged", "Sum"),
        }
        queries = [
            {
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/DocDB",
                        "MetricName": metric,
                        "Dimensions": [{"Name": "DBClusterIdentifier", "Value": self.cluster_id}],
                    },
                    "Period": 3600,
                    "Stat": stat,
                },
                "ReturnData": True,
            }
            for qid, (metric, stat) in stats.items()
        ]
        series: Dict[str, List[Tuple[datetime, float]]] = {qid: [] for qid in stats}
        token = None
        while True:
            kwargs = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
            if token:
                kwargs["NextToken"] = token
            response = cloudwatch.get_metric_data(**kwargs)
            for result in response.get("MetricDataResults", []):
                series[result["Id"]].extend(zip(result.get("Timestamps", []), result.get("Values", [])))
            token = response.get("NextToken")
            if not token:
                break

        def values(qid: str) -> List[float]:
            return [v for _, v in series[qid]]

        op_ids = ["op_query", "op_insert", "op_update", "op_delete", "op_getmore"]
        running_hours = len(series["cpu_avg"])  # instances only report CPU while running
        billed_ios = sum(values("read_ios")) + sum(values("write_ios"))
        operations = sum(sum(values(q)) for q in op_ids)
        reads = sum(values("op_query")) + sum(values("op_getmore"))
        writes = sum(values("op_insert")) + sum(values("op_update")) + sum(values("op_delete"))
        latest_volume = max(series["volume_bytes"], key=lambda p: p[0])[1] if series["volume_bytes"] else None

        daily: Dict[str, Dict[str, Any]] = {}
        for day_offset in range(7, -1, -1):
            key = (end - timedelta(days=day_offset)).date().isoformat()
            daily[key] = {"date": key, "running_hours": 0, "operations": 0.0, "billed_ios": 0.0, "cpu": []}
        for ts, v in series["cpu_avg"]:
            bucket = daily.setdefault(ts.date().isoformat(), {"date": ts.date().isoformat(), "running_hours": 0, "operations": 0.0, "billed_ios": 0.0, "cpu": []})
            bucket["running_hours"] += 1
            bucket["cpu"].append(v)
        for qid in op_ids + ["read_ios", "write_ios"]:
            for ts, v in series[qid]:
                bucket = daily.get(ts.date().isoformat())
                if bucket is not None:
                    bucket["operations" if qid.startswith("op_") else "billed_ios"] += v
        daily_list = []
        for bucket in sorted(daily.values(), key=lambda b: b["date"]):
            cpu = bucket.pop("cpu")
            bucket["cpu_avg"] = round(statistics.fmean(cpu), 2) if cpu else None
            bucket["operations"] = int(bucket["operations"])
            bucket["billed_ios"] = int(bucket["billed_ios"])
            daily_list.append(bucket)

        return {
            "window_days": 7,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "running_hours": running_hours,
            "cpu_avg_percent": round(statistics.fmean(values("cpu_avg")), 2) if series["cpu_avg"] else None,
            "cpu_peak_percent": round(max(values("cpu_max")), 2) if series["cpu_max"] else None,
            "connections_avg": round(statistics.fmean(values("connections")), 1) if series["connections"] else None,
            "storage_bytes": int(latest_volume) if latest_volume is not None else None,
            "storage_gb": round(latest_volume / 1024 ** 3, 4) if latest_volume is not None else None,
            "billed_ios": int(billed_ios),
            "operations": int(operations),
            "read_operations": int(reads),
            "write_operations": int(writes),
            "cpu_credits_charged": round(sum(values("credits_charged")), 4),
            "daily": daily_list,
        }

    def _fetch_pricing(self) -> Dict[str, Any]:
        pricing = self._client("pricing", region="us-east-1")
        rates: Dict[str, Any] = {
            "source": "AWS Price List API",
            "region": self.region,
            "instance_hourly": {"standard": {}, "iopt1": {}},
            "storage_gb_month": {},
            "io_per_million": None,
            "backup_gb_month": None,
            "cpu_credit_vcpu_hour": None,
        }
        pages = pricing.get_paginator("get_products").paginate(
            ServiceCode="AmazonDocDB",
            Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": self.region}],
        )
        for page in pages:
            for raw in page.get("PriceList", []):
                product = json.loads(raw) if isinstance(raw, str) else raw
                attrs = product.get("product", {}).get("attributes", {})
                usage = attrs.get("usagetype", "")
                price = None
                for term in product.get("terms", {}).get("OnDemand", {}).values():
                    for dimension in term.get("priceDimensions", {}).values():
                        price = float(dimension.get("pricePerUnit", {}).get("USD", 0) or 0)
                if not price:
                    continue
                if "InstanceUsageIOOptimized:" in usage:
                    rates["instance_hourly"]["iopt1"][attrs.get("instanceType")] = price
                elif "InstanceUsage:" in usage:
                    rates["instance_hourly"]["standard"][attrs.get("instanceType")] = price
                elif usage.endswith("IO-OptimizedStorageUsage"):
                    rates["storage_gb_month"]["iopt1"] = price
                elif usage.endswith("StorageUsage") and "Elastic" not in usage:
                    rates["storage_gb_month"]["standard"] = price
                elif usage.endswith("StorageIOUsage"):
                    rates["io_per_million"] = round(price * 1_000_000, 4)
                elif usage.endswith("BackupUsage") and "Elastic" not in usage:
                    rates["backup_gb_month"] = price
                elif "CPUCredits:db.t3" in usage:
                    rates["cpu_credit_vcpu_hour"] = price
        if "db.t3.medium" not in rates["instance_hourly"]["standard"] or "standard" not in rates["storage_gb_month"]:
            raise ValueError(f"Price List API returned no DocumentDB prices for {self.region}")
        return rates

    def _fetch_costs(self) -> Dict[str, Any]:
        ce = self._client("ce", region="us-east-1")
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=30)
        end = today + timedelta(days=1)  # End is exclusive; include today's partial data
        month_start = today.replace(day=1)

        rows: List[Dict[str, Any]] = []
        token = None
        while True:
            kwargs = {
                "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "Filter": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}},
                "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
            }
            if token:
                kwargs["NextPageToken"] = token
            response = ce.get_cost_and_usage(**kwargs)
            rows.extend(response.get("ResultsByTime", []))
            token = response.get("NextPageToken")
            if not token:
                break

        record_types = ce.get_cost_and_usage(
            TimePeriod={"Start": month_start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}],
        )
        totals_by_type: Dict[str, float] = {}
        for period in record_types.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                totals_by_type[group["Keys"][0]] = totals_by_type.get(group["Keys"][0], 0.0) + float(group["Metrics"]["UnblendedCost"]["Amount"])

        daily: Dict[str, Dict[str, Any]] = {}
        for period in rows:
            day = period["TimePeriod"]["Start"]
            bucket = daily.setdefault(day, {"date": day, "total": 0.0, "components": {}})
            for group in period.get("Groups", []):
                service, usage_type = group["Keys"]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if abs(amount) < 1e-7:
                    continue
                component = self.classify_usage(service, usage_type)
                bucket["components"][component] = bucket["components"].get(component, 0.0) + amount
                bucket["total"] += amount
        daily_list = sorted(daily.values(), key=lambda d: d["date"])
        for d in daily_list:
            d["total"] = round(d["total"], 4)
            d["components"] = {k: round(v, 4) for k, v in d["components"].items()}

        mtd_components: Dict[str, float] = {}
        last30_components: Dict[str, float] = {}
        for d in daily_list:
            for k, v in d["components"].items():
                last30_components[k] = last30_components.get(k, 0.0) + v
                if d["date"] >= month_start.isoformat():
                    mtd_components[k] = mtd_components.get(k, 0.0) + v
        components = [
            {
                "key": k,
                "label": COMPONENT_LABELS.get(k, k),
                "month_to_date": round(mtd_components.get(k, 0.0), 4),
                "last_30_days": round(last30_components.get(k, 0.0), 4),
            }
            for k in COMPONENT_LABELS
            if k in last30_components
        ]
        components.sort(key=lambda c: c["last_30_days"], reverse=True)

        complete_days = [d for d in daily_list if d["date"] < today.isoformat()][-7:]
        avg_daily = round(statistics.fmean([d["total"] for d in complete_days]), 4) if complete_days else 0.0
        usage_mtd = round(sum(mtd_components.values()), 4)
        days_in_month = ((month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - month_start).days
        days_remaining = days_in_month - today.day + 1  # includes the rest of today
        costed_days = [d["date"] for d in daily_list if d["total"] > 0.0001]

        return {
            "currency": "USD",
            "daily": daily_list,
            "components": components,
            "month_to_date": {
                "usage": usage_mtd,
                "credits": round(totals_by_type.get("Credit", 0.0), 4),
                "tax": round(totals_by_type.get("Tax", 0.0), 4),
                "net": round(sum(totals_by_type.values()), 4),
            },
            "documentdb_month_to_date": round(sum(v for k, v in mtd_components.items() if k.startswith("docdb_")), 4),
            "average_daily_last_7_days": avg_daily,
            "projected_month_usage": round(usage_mtd + avg_daily * days_remaining, 2),
            "days_in_month": days_in_month,
            "days_elapsed": today.day,
            "latest_cost_date": costed_days[-1] if costed_days else None,
            "note": ("Cost Explorer refreshes a few times a day and can lag up to 24 hours. "
                     "Amounts are usage before credits; credits and net appear separately."),
        }

    @staticmethod
    def classify_usage(service: str, usage_type: str) -> str:
        if service in DOCUMENTDB_SERVICES:
            if "InstanceUsage" in usage_type:
                return "docdb_instance"
            if "StorageIOUsage" in usage_type or "IOUsage" in usage_type:
                return "docdb_io"
            if "BackupUsage" in usage_type:
                return "docdb_backup"
            if "StorageUsage" in usage_type:
                return "docdb_storage"
            if "CPUCredits" in usage_type:
                return "docdb_cpu_credits"
            return "docdb_other"
        if service == "Amazon Relational Database Service":
            return "rds_other"
        if service == "Amazon Elastic Compute Cloud - Compute":
            return "ec2_instance"
        if "PublicIPv4" in usage_type or "ElasticIP" in usage_type:
            return "public_ipv4"
        if service == "EC2 - Other" and "EBS" in usage_type:
            return "ec2_storage"
        return "other"

    # --- derived views ------------------------------------------------------

    @staticmethod
    def instance_rate(rates: Dict[str, Any], instance_class: str, storage_type: str) -> Optional[float]:
        table = rates.get("instance_hourly", {}).get(storage_type, {})
        return table.get(instance_class) or STATIC_RATES["instance_hourly"].get(storage_type, {}).get(instance_class)

    def build_profile(self, cluster: Dict[str, Any], schedule: Optional[Dict[str, Any]],
                      metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        instances = cluster.get("instances") or []
        scheduled = bool(schedule and schedule.get("active") and schedule.get("monthly_hours") is not None)
        monthly_hours = schedule["monthly_hours"] if scheduled else float(HOURS_PER_MONTH)
        running_hours = (metrics or {}).get("running_hours") or 0
        ios_per_hour = (metrics["billed_ios"] / running_hours) if metrics and running_hours else 0.0
        ops_per_hour = (metrics["operations"] / running_hours) if metrics and running_hours else 0.0
        return {
            "instance_class": instances[0]["instance_class"] if instances else None,
            "instance_count": len(instances),
            "storage_type": cluster.get("storage_type") or "standard",
            "region": cluster.get("region"),
            "backup_retention_days": cluster.get("backup_retention_days"),
            "monthly_hours": monthly_hours,
            "monthly_hours_source": "EventBridge start/stop schedule" if scheduled else "No start/stop schedule (always on)",
            "storage_gb": (metrics or {}).get("storage_gb"),
            "billed_ios_per_running_hour": round(ios_per_hour, 2),
            "operations_per_running_hour": round(ops_per_hour, 2),
            "monthly_io_requests": round(ios_per_hour * monthly_hours),
            "cpu_avg_percent": (metrics or {}).get("cpu_avg_percent"),
            "cpu_peak_percent": (metrics or {}).get("cpu_peak_percent"),
        }

    def cost_model(self, profile: Dict[str, Any], rates: Dict[str, Any], *, instance_class: Optional[str] = None,
                   instance_count: Optional[int] = None, monthly_hours: Optional[float] = None,
                   storage_type: Optional[str] = None) -> Dict[str, Any]:
        cls = instance_class or profile["instance_class"]
        count = profile["instance_count"] if instance_count is None else instance_count
        hours = profile["monthly_hours"] if monthly_hours is None else monthly_hours
        stype = storage_type or profile["storage_type"]
        hourly = self.instance_rate(rates, cls, stype) or 0.0
        storage_rate = rates.get("storage_gb_month", {}).get(stype) or STATIC_RATES["storage_gb_month"][stype]
        io_rate = rates.get("io_per_million") or STATIC_RATES["io_per_million"]
        storage_gb = profile.get("storage_gb") or 0.0
        io_requests = profile["billed_ios_per_running_hour"] * hours

        instance_cost = hourly * hours * count
        storage_cost = storage_gb * storage_rate
        io_cost = 0.0 if stype == "iopt1" else io_requests / 1_000_000 * io_rate
        return {
            "instance_class": cls,
            "instance_count": count,
            "monthly_hours": round(hours, 1),
            "storage_type": stype,
            "components": [
                {"key": "instance", "label": "Instance hours", "monthly": round(instance_cost, 2),
                 "formula": f"${hourly:g}/h × {hours:.1f} h × {count} instance{'s' if count != 1 else ''}"},
                {"key": "storage", "label": "Storage", "monthly": round(storage_cost, 4),
                 "formula": f"{storage_gb:.3f} GB × ${storage_rate:g}/GB-month"},
                {"key": "io", "label": "I/O requests", "monthly": round(io_cost, 4),
                 "formula": "Included with I/O-Optimized storage" if stype == "iopt1"
                 else f"{io_requests:,.0f} requests × ${io_rate:g} per million"},
                {"key": "backup", "label": "Backup storage", "monthly": 0.0,
                 "formula": "Backups up to the cluster's size are free"},
            ],
            "monthly_total": round(instance_cost + storage_cost + io_cost, 2),
        }

    def build_scenarios(self, profile: Dict[str, Any], rates: Dict[str, Any]) -> List[Dict[str, Any]]:
        current = self.cost_model(profile, rates)
        candidates = [
            ("current", "Current configuration", {}),
            ("always_on", "Always on (no schedule)", {"monthly_hours": HOURS_PER_MONTH}),
            ("weekdays_12h", "Weekdays 09:00–21:00", {"monthly_hours": round(12 * 5 * WEEKS_PER_MONTH, 1)}),
            ("weekdays_8h", "Weekdays, 8 hours a day", {"monthly_hours": round(8 * 5 * WEEKS_PER_MONTH, 1)}),
            ("demo_only", "Demo days only (3 × 4 h a week)", {"monthly_hours": round(12 * WEEKS_PER_MONTH, 1)}),
        ]
        if profile["instance_class"] != "db.t4g.medium" and self.instance_rate(rates, "db.t4g.medium", profile["storage_type"]):
            candidates.append(("graviton", "db.t4g.medium (Graviton2), same schedule", {"instance_class": "db.t4g.medium"}))
        if profile["storage_type"] != "iopt1":
            candidates.append(("io_optimized", "I/O-Optimized storage, same schedule", {"storage_type": "iopt1"}))
        scenarios = []
        for sid, label, overrides in candidates:
            model = current if sid == "current" else self.cost_model(profile, rates, **overrides)
            scenarios.append({
                "id": sid,
                "label": label,
                "monthly_hours": model["monthly_hours"],
                "instance_class": model["instance_class"],
                "storage_type": model["storage_type"],
                "monthly_total": model["monthly_total"],
                "difference_vs_current": round(model["monthly_total"] - current["monthly_total"], 2),
            })
        return scenarios

    def build_recommendations(self, profile: Dict[str, Any], rates: Dict[str, Any],
                              schedule: Optional[Dict[str, Any]], metrics: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        current = self.cost_model(profile, rates)
        always_on = self.cost_model(profile, rates, monthly_hours=HOURS_PER_MONTH)
        cpu_avg = profile.get("cpu_avg_percent")

        # 1. Start/stop schedule (the main lever for "cluster cost is high for a small workload")
        if schedule and schedule.get("active"):
            recs.append({
                "id": "schedule", "category": "scheduling", "status": "applied",
                "title": "Stop the cluster outside working hours",
                "monthly_savings": round(always_on["monthly_total"] - current["monthly_total"], 2),
                "evidence": [
                    f"Schedule: {schedule.get('description') or 'custom'}",
                    f"Runs {profile['monthly_hours']:.0f} of {HOURS_PER_MONTH} hours a month "
                    f"({100 - profile['monthly_hours'] / HOURS_PER_MONTH * 100:.0f}% fewer instance hours)",
                ],
                "action": "Already in place: EventBridge Scheduler starts and stops the cluster automatically.",
                "tradeoff": "The app shows 'DocumentDB paused' outside the window; start the cluster manually for off-hours demos.",
            })
        else:
            weekdays = self.cost_model(profile, rates, monthly_hours=round(12 * 5 * WEEKS_PER_MONTH, 1))
            recs.append({
                "id": "schedule", "category": "scheduling", "status": "recommended",
                "title": "Stop the cluster outside working hours",
                "monthly_savings": round(current["monthly_total"] - weekdays["monthly_total"], 2),
                "evidence": [f"The cluster runs {profile['monthly_hours']:.0f} hours a month with no start/stop schedule"],
                "action": "Deploy deploy/docdb-schedule.yaml (weekdays 09:00–21:00 by default).",
                "tradeoff": "The database is unavailable outside the schedule unless started manually.",
            })

        # 2. Instance class
        cls = profile["instance_class"]
        graviton_rate = self.instance_rate(rates, "db.t4g.medium", profile["storage_type"])
        current_rate = self.instance_rate(rates, cls, profile["storage_type"])
        if cls == "db.t4g.medium":
            recs.append({
                "id": "instance_class", "category": "compute", "status": "not_needed",
                "title": "Use the most cost-efficient instance class", "monthly_savings": 0.0,
                "evidence": ["Already on db.t4g.medium, the lowest-priced DocumentDB instance class"],
                "action": "No change needed.", "tradeoff": None,
            })
        elif graviton_rate and current_rate and graviton_rate < current_rate:
            switched = self.cost_model(profile, rates, instance_class="db.t4g.medium")
            busy = cpu_avg is not None and cpu_avg > 60
            recs.append({
                "id": "instance_class", "category": "compute", "status": "optional" if not busy else "not_needed",
                "title": f"Switch {cls} to db.t4g.medium (Graviton2)",
                "monthly_savings": round(current["monthly_total"] - switched["monthly_total"], 2),
                "evidence": [
                    f"List price ${current_rate:g}/h vs ${graviton_rate:g}/h",
                    f"Average CPU {cpu_avg:.1f}% over the last 7 days" if cpu_avg is not None else "CPU data not available yet",
                ],
                "action": "Modify the instance class in the DocumentDB console during a quiet period (brief restart).",
                "tradeoff": "Same 2 vCPU / 4 GiB size; a short restart is needed to apply the change.",
            })

        # 3. Storage type
        if profile["storage_type"] == "standard":
            io_opt = self.cost_model(profile, rates, storage_type="iopt1")
            io_cost = next(c["monthly"] for c in current["components"] if c["key"] == "io")
            worth_it = io_opt["monthly_total"] < current["monthly_total"]
            recs.append({
                "id": "storage_type", "category": "storage", "status": "recommended" if worth_it else "not_needed",
                "title": "Keep Standard storage unless I/O dominates the bill",
                "monthly_savings": round(max(current["monthly_total"] - io_opt["monthly_total"], 0.0), 2),
                "evidence": [
                    f"I/O costs about ${io_cost:.4f} a month at {profile['billed_ios_per_running_hour']:.0f} billed I/Os per running hour",
                    f"I/O-Optimized would cost ${io_opt['monthly_total']:.2f} vs ${current['monthly_total']:.2f} a month",
                ],
                "action": "No change needed." if not worth_it else "Switch the cluster storage type to I/O-Optimized.",
                "tradeoff": "I/O-Optimized raises instance and storage prices in exchange for free I/O.",
            })

        # 4. Replicas
        if profile["instance_count"] > 1:
            single = self.cost_model(profile, rates, instance_count=1)
            recs.append({
                "id": "replicas", "category": "architecture", "status": "recommended",
                "title": "Remove replica instances for development",
                "monthly_savings": round(current["monthly_total"] - single["monthly_total"], 2),
                "evidence": [f"{profile['instance_count']} instances are running"],
                "action": "Delete reader instances; keep one primary for development and demos.",
                "tradeoff": "No automatic failover while only one instance runs.",
            })
        else:
            recs.append({
                "id": "replicas", "category": "architecture", "status": "not_needed",
                "title": "Run a single instance for development", "monthly_savings": 0.0,
                "evidence": ["1 instance (no read replicas)"], "action": "No change needed.", "tradeoff": None,
            })

        # 5. Backup retention
        retention = profile.get("backup_retention_days") or 1
        recs.append({
            "id": "backups", "category": "storage", "status": "not_needed" if retention <= 1 else "optional",
            "title": "Keep backup retention short", "monthly_savings": 0.0,
            "evidence": [f"Backup retention is {retention} day{'s' if retention != 1 else ''}"],
            "action": "No change needed." if retention <= 1 else "Reduce retention to 1 day for non-production data.",
            "tradeoff": None if retention <= 1 else "Fewer point-in-time restore days.",
        })

        # 6. Burstable CPU credits
        credits = (metrics or {}).get("cpu_credits_charged") or 0.0
        if credits > 0:
            rate = rates.get("cpu_credit_vcpu_hour") or STATIC_RATES["cpu_credit_vcpu_hour"]
            recs.append({
                "id": "cpu_credits", "category": "monitoring", "status": "warning",
                "title": "Burst CPU usage is being charged",
                "monthly_savings": 0.0,
                "evidence": [f"{credits:.2f} surplus vCPU-hours charged in 7 days (≈ ${credits * rate:.2f})"],
                "action": "Investigate heavy queries or move to a larger instance if this persists.",
                "tradeoff": None,
            })
        return recs

    def overview(self, refresh: bool = False) -> Dict[str, Any]:
        base = {"enabled": self.enabled, "region": self.region, "cluster_id": self.cluster_id or None}
        if not self.enabled:
            return {**base, "available": False, "sections": {}, "profile": None, "model": None,
                    "scenarios": [], "recommendations": [], "rates": None,
                    "reason": "Live AWS data is turned off. Set AWS_INSIGHTS_ENABLED=true and DOCUMENTDB_CLUSTER_ID on the API server."}

        sections = {
            "cluster": self._section("cluster", self._fetch_cluster, refresh),
            "schedule": self._section("schedule", self._fetch_schedule, refresh),
            "metrics": self._section("metrics", self._fetch_metrics, refresh),
            "pricing": self._section("pricing", self._fetch_pricing),
            "costs": self._section("costs", self._fetch_costs, refresh, min_refresh_age=COSTS_MIN_REFRESH_SECONDS),
        }
        cluster = sections["cluster"]["data"]
        rates = sections["pricing"]["data"] or STATIC_RATES
        profile = model = None
        scenarios: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []
        if cluster and cluster.get("instances"):
            profile = self.build_profile(cluster, sections["schedule"]["data"], sections["metrics"]["data"])
            model = self.cost_model(profile, rates)
            scenarios = self.build_scenarios(profile, rates)
            recommendations = self.build_recommendations(profile, rates, sections["schedule"]["data"], sections["metrics"]["data"])
        return {
            **base,
            "available": cluster is not None,
            "reason": None if cluster is not None else sections["cluster"]["error"],
            "sections": sections,
            "rates": rates,
            "profile": profile,
            "model": model,
            "scenarios": scenarios,
            "recommendations": recommendations,
        }


aws_insights_service = AwsInsightsService()
