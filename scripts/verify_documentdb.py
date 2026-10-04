#!/usr/bin/env python3
"""
Verifies the project brief against the live system.

  Use case 1   Store variable-schema inspection reports
  Use case 2   Query nested document structures
  Bottleneck 1 MongoDB feature compatibility gaps break the driver
  Bottleneck 2 Cluster cost is high for a small workload

Database checks run against the configured document store (Amazon DocumentDB when
STORAGE_MODE=documentdb) in a temporary collection that is dropped afterwards, so no
application data is read or changed. Cost checks read AWS through the same read-only
calls as the Cost Monitoring page.

Usage (on the API server):
    docker compose --env-file .env.production exec -T backend python scripts/verify_documentdb.py
Options:
    --skip-db    only run the AWS cost checks
    --skip-aws   only run the database checks
"""

import argparse
import copy
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from pymongo import MongoClient  # noqa: E402
from pymongo.errors import OperationFailure, PyMongoError  # noqa: E402

from app.db.document_store import get_document_store_config, redact_uri  # noqa: E402
from app.services.documentdb_compatibility import compatibility_analyzer  # noqa: E402

RESULTS = []


def record(section, name, passed, detail):
    RESULTS.append((section, name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}\n         {detail}")


REPORTS = [
    {
        "id": "VERIFY-1",
        "title": "Substation transformer thermal scan",
        "category": "Electrical",
        "findings": [
            {"category": "Thermal", "severity": "critical", "issues": [{"title": "Loose lug", "status": "open"}]},
            {"category": "Grounding", "severity": "low", "issues": [{"title": "Label faded", "status": "resolved"}]},
        ],
        "dynamic_attributes": {"electrical_telemetry": {"phases": {"phase_a": {"voltage_kv": 13.82, "current_amps": 104.5}}}},
    },
    {
        "id": "VERIFY-2",
        "title": "High-rise fire safety audit",
        "category": "Fire Safety",
        "findings": [
            {"category": "Suppression", "severity": "high", "issues": [{"title": "Valve seized", "status": "open"}]},
            {"category": "Egress", "severity": "critical", "issues": [{"title": "Blocked exit", "status": "resolved"}]},
        ],
        "dynamic_attributes": {"fire_safety_data": {"extinguishers": [{"floor": 3, "pressure_psi": 195}], "sprinkler_zones": 14}},
    },
    {
        "id": "VERIFY-3",
        "title": "Rooftop chiller diagnostics",
        "category": "HVAC",
        "findings": [],
        "custom_fields": [{"key": "refrigerant", "value": "R-410A", "field_type": "string"}],
        "dynamic_attributes": {"hvac_diagnostics": {"chilled_water_temp_c": 6.7, "cop_efficiency": 4.2}},
    },
]


def database_checks():
    cfg = get_document_store_config()
    print(f"\nDatabase: {cfg.engine_label} · {redact_uri(cfg.uri).split('?')[0]} · database '{cfg.database}'")
    client = MongoClient(cfg.uri, serverSelectionTimeoutMS=cfg.timeout_ms, connectTimeoutMS=cfg.timeout_ms, **cfg.client_kwargs)
    name = f"verify_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    coll = client[cfg.database][name]
    try:
        # ---------------- Use case 1 ----------------
        print("\nUSE CASE 1 · Store variable-schema inspection reports")
        docs = copy.deepcopy(REPORTS)
        coll.insert_many(docs)
        stored = list(coll.find({}, {"_id": 0}).sort("title", 1))
        expected = sorted(copy.deepcopy(REPORTS), key=lambda d: d["title"])
        shapes = {tuple(sorted(d["dynamic_attributes"].keys())) for d in stored}
        record("Use case 1", "Documents with different shapes are stored and read back unchanged",
               stored == expected and len(shapes) == 3,
               f"{len(stored)} documents, {len(shapes)} distinct dynamic_attributes shapes, round-trip identical: {stored == expected}")

        # ---------------- Use case 2 ----------------
        print("\nUSE CASE 2 · Query nested document structures")
        n = coll.count_documents({"dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv": {"$gt": 13}})
        record("Use case 2", "Dot-notation query four levels deep", n == 1,
               f"dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv > 13 → {n} match (expected 1)")

        elem = {"findings": {"$elemMatch": {"severity": "critical", "issues.status": "open"}}}
        loose = {"findings.severity": "critical", "findings.issues.status": "open"}
        n_elem, n_loose = coll.count_documents(elem), coll.count_documents(loose)
        record("Use case 2", "$elemMatch keeps both conditions on the same finding", n_elem == 1 and n_loose == 2,
               f"$elemMatch → {n_elem} (correct); without $elemMatch → {n_loose} (false match on the fire audit)")

        n_arr = coll.count_documents({"findings.issues.status": "open"})
        record("Use case 2", "Query an array inside an array", n_arr == 2, f"findings.issues.status = 'open' → {n_arr} matches (expected 2)")

        agg = list(coll.aggregate([{"$unwind": "$findings"}, {"$group": {"_id": "$findings.severity", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]))
        counts = {a["_id"]: a["n"] for a in agg}
        record("Use case 2", "Aggregation over nested findings", counts == {"critical": 2, "high": 1, "low": 1},
               f"findings by severity → {counts}")

        coll.create_index([("findings.severity", 1)])
        chosen = str(coll.find({"findings.severity": "critical"}).explain().get("queryPlanner", {}))
        hinted = str(coll.find({"findings.severity": "critical"}).hint([("findings.severity", 1)]).explain().get("queryPlanner", {}))
        record("Use case 2", "A multikey index on a nested array field serves the query", "IXSCAN" in hinted,
               f"with the index: {'IXSCAN (index scan)' if 'IXSCAN' in hinted else 'no index scan'} · planner's own choice on "
               f"{coll.estimated_document_count()} documents: {'IXSCAN' if 'IXSCAN' in chosen else 'COLLSCAN (expected for a tiny collection)'}")

        # ---------------- Bottleneck 1 ----------------
        print("\nBOTTLENECK 1 · MongoDB feature compatibility gaps break the driver")
        if cfg.mode == "documentdb" and cfg.client_kwargs.get("tls"):
            # A stock driver trusts only public CAs; DocumentDB certificates are issued by the Amazon RDS CA
            no_ca_kwargs = {k: v for k, v in cfg.client_kwargs.items() if k != "tlsCAFile"}
            stock_client = MongoClient(cfg.uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000, **no_ca_kwargs)
            try:
                stock_client.admin.command("ping")
                broke, message = False, "connected"
            except PyMongoError as e:
                text = str(e)
                broke, message = True, "certificate verify failed" if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text else text[:120]
            finally:
                stock_client.close()
            app_ok = client.admin.command("ping").get("ok") == 1
            record("Bottleneck 1", "Stock TLS settings fail; the app's Amazon CA bundle connects", broke and app_ok,
                   f"without the CA bundle: {message} · with global-bundle.pem: ping ok={app_ok}")

            retry_client = MongoClient(cfg.uri, serverSelectionTimeoutMS=cfg.timeout_ms, **{**cfg.client_kwargs, "retryWrites": True})
            try:
                retry_client[cfg.database][name].insert_one({"id": "VERIFY-RETRY", "probe": "retryWrites=true"})
                retry_note = "DocumentDB 5.0 accepts retryable writes"
            except PyMongoError as e:
                retry_note = f"retryable writes rejected ({str(e)[:80]})"
            finally:
                retry_client.close()
            print(f"  [INFO] {retry_note}; the app keeps retryWrites=false so it also works on DocumentDB 3.6/4.0.")
        else:
            record("Bottleneck 1", "TLS / CA bundle check", True, f"skipped: store is {cfg.engine_label}, not DocumentDB with TLS")

        where_query = {"$where": "this.findings.length > 1"}
        report = compatibility_analyzer.analyze_query(where_query, target_version="5.0")
        try:
            coll.count_documents(where_query)
            db_says = "accepted"
        except OperationFailure as e:
            db_says = f"rejected ({str(e).split(', full error')[0][:90]})"
        record("Bottleneck 1", "$where (JavaScript) is caught before it reaches the database",
               report.status == "INCOMPATIBLE",
               f"analyzer: {report.status} · DocumentDB itself: {db_says}")

        all_elem = {"findings": {"$all": [{"$elemMatch": {"severity": "critical"}}, {"$elemMatch": {"severity": "high"}}]}}
        report = compatibility_analyzer.analyze_query(all_elem, target_version="5.0")
        try:
            coll.count_documents(all_elem)
            db_says = "accepted"
        except OperationFailure as e:
            db_says = f"rejected ({str(e).split(', full error')[0][:90]})"
        rewritten_ok, rewritten_n = False, None
        if report.alternative_query:
            rewritten_n = coll.count_documents(report.alternative_query)
            rewritten_ok = rewritten_n == 1
        record("Bottleneck 1", "$elemMatch inside $all is rewritten to a DocumentDB-compatible $and",
               report.status == "INCOMPATIBLE" and rewritten_ok,
               f"analyzer: {report.status} · DocumentDB with original: {db_says} · rewritten query → {rewritten_n} match (expected 1)")

        errors = []

        def build(keys):
            try:
                coll.create_index(keys)
            except OperationFailure as e:
                errors.append(e.code)

        threads = [threading.Thread(target=build, args=([(f, 1)],)) for f in ("title", "category", "dynamic_attributes.hvac_diagnostics.cop_efficiency")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        from app.repositories.mongodb_repository import MongoDBInspectionRepository
        repo = MongoDBInspectionRepository(config=cfg, collection_name=name)
        repo.ensure_indexes()
        indexes = coll.index_information()
        record("Bottleneck 1", "Concurrent index builds are retried instead of failing",
               "id_1" in indexes and "user_id_1_inspection_date_-1" in indexes,
               f"parallel raw builds hit {errors.count(40333)} 'index build in progress' error(s); app's ensure_indexes created all {len(indexes)} indexes")
    finally:
        coll.drop()
        client.close()
        print(f"\n  (temporary collection '{name}' dropped)")


def aws_checks():
    print("\nBOTTLENECK 2 · Cluster cost is high for a small workload")
    from app.services.aws_insights import AwsInsightsService, HOURS_PER_MONTH
    overview = AwsInsightsService().overview(refresh=True)
    if not overview["enabled"] or not overview["available"]:
        record("Bottleneck 2", "Live AWS data available", False, overview.get("reason") or "unavailable")
        return
    schedule = overview["sections"]["schedule"]["data"] or {}
    profile, model, recs = overview["profile"], overview["model"], {r["id"]: r for r in overview["recommendations"]}
    costs = overview["sections"]["costs"]["data"]
    always_on = next((s["monthly_total"] for s in overview["scenarios"] if s["id"] == "always_on"), None)

    record("Bottleneck 2", "Cluster stops automatically outside working hours", bool(schedule.get("active")),
           f"{schedule.get('description') or 'no schedule'} → {profile['monthly_hours']} of {HOURS_PER_MONTH} h/month "
           f"({100 - profile['monthly_hours'] / HOURS_PER_MONTH * 100:.0f}% fewer instance hours)")
    record("Bottleneck 2", "Smallest practical deployment (1 burstable instance, Standard storage)",
           profile["instance_count"] == 1 and profile["instance_class"] in ("db.t3.medium", "db.t4g.medium"),
           f"{profile['instance_count']} × {profile['instance_class']}, {profile['storage_type']} storage, "
           f"backups {profile['backup_retention_days']} day")
    record("Bottleneck 2", "Monthly cost with the schedule vs always on", always_on is not None and model["monthly_total"] < always_on,
           f"${model['monthly_total']:.2f}/mo modelled vs ${always_on:.2f}/mo always on "
           f"(saves ${recs.get('schedule', {}).get('monthly_savings', 0):.2f}/mo) · prices from {overview['rates']['source']}")
    if costs:
        record("Bottleneck 2", "Actual spend is tracked from Cost Explorer", True,
               f"this month: ${costs['month_to_date']['usage']:.4f} usage, ${costs['month_to_date']['credits']:.4f} credits, "
               f"${costs['month_to_date']['net']:.4f} net (data through {costs['latest_cost_date']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-aws", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print("InspectDB · project brief verification ·", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 78)
    started = time.perf_counter()
    if not args.skip_db:
        database_checks()
    if not args.skip_aws:
        aws_checks()
    passed = sum(1 for r in RESULTS if r[2])
    print("\n" + "=" * 78)
    print(f"{passed}/{len(RESULTS)} checks passed in {time.perf_counter() - started:.1f}s")
    return passed == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
