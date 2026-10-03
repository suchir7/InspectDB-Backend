import math
from typing import List, Dict, Any, Optional, Tuple
from app.schemas.cost import (
    WorkloadInput,
    CostBreakdown,
    DeploymentOptionEstimate,
    CostHealth,
    CostEstimateResponse,
    CostTrendPoint,
    CostTrendResponse,
    CostDriverDetail,
    CostAnomalyReport,
    BudgetStatus,
    OptimizationSimulationRequest,
    OptimizationSimulationResponse
)

# Centralized AWS DocumentDB Rate Cards & Baseline Pricing Constants (Q1 2026)
DOCUMENTDB_PRICING: Dict[str, Any] = {
    "version": "2026.1",
    "source": "AWS DocumentDB Standard Rate Card (US East N. Virginia baseline)",
    "currency": "USD",
    "compute_hourly": {
        "local": 0.00,
        "db.t3.medium": 0.078,  # 2 vCPU, 4.0 GiB RAM ($0.078/hr)
        "db.r5.large": 0.277,   # 2 vCPU, 16.0 GiB RAM ($0.277/hr)
        "db.r6g.large": 0.245,  # 2 vCPU, 16.0 GiB RAM AWS Graviton2 ($0.245/hr)
        "elastic_dcu": 0.12     # DocumentDB Compute Unit (DCU) per hour ($0.12/DCU-hr)
    },
    "storage_per_gb_month": 0.10,     # $0.10 per GB-month (6-way replicated across 3 AZs)
    "io_per_million": 0.20,           # $0.20 per 1,000,000 I/O requests
    "backup_per_gb_month": 0.095,     # $0.095 per GB-month for storage beyond 100% of cluster storage
    "region_multipliers": {
        "us-east-1": 1.00,
        "us-west-2": 1.00,
        "eu-west-1": 1.10,
        "eu-central-1": 1.12,
        "ap-south-1": 1.05,
        "ap-southeast-1": 1.10
    }
}

class CostCalculator:
    """
    Deterministic AWS DocumentDB pricing estimator.
    Calculates exact compute, storage, I/O, backup components, and data-backed CostHealth
    based on documented AWS DocumentDB rate cards.
    """

    @staticmethod
    def calculate_workload_costs(workload: WorkloadInput) -> CostEstimateResponse:
        region_mult = DOCUMENTDB_PRICING["region_multipliers"].get(workload.region.lower(), 1.00)
        
        # Monthly requests & I/O calculation (30 days/month average)
        monthly_requests = workload.requests_per_day * 30
        read_ratio = workload.read_percentage / 100.0
        write_ratio = workload.write_percentage / 100.0
        # Write operations average 2.0 I/Os (data + journal/index), reads average 1.1 I/Os
        weighted_io_per_req = (read_ratio * 1.1) + (write_ratio * 2.0)
        total_monthly_ios = monthly_requests * weighted_io_per_req
        io_cost = round((total_monthly_ios / 1_000_000.0) * DOCUMENTDB_PRICING["io_per_million"] * region_mult, 2)

        # Storage cost (Active BSON data storage)
        storage_cost = round(workload.data_storage_gb * DOCUMENTDB_PRICING["storage_per_gb_month"] * region_mult, 2)

        # Backup cost: First 100% of cluster storage is free. Additional incremental daily changes (~5% daily turnover)
        estimated_total_backup_gb = workload.data_storage_gb + (workload.data_storage_gb * 0.05 * workload.backup_retention_days)
        billable_backup_gb = max(0.0, estimated_total_backup_gb - workload.data_storage_gb)
        backup_cost = round(billable_backup_gb * DOCUMENTDB_PRICING["backup_per_gb_month"] * region_mult, 2)

        # Calculate 5 Standard Deployment Tiers
        options: List[DeploymentOptionEstimate] = [
            CostCalculator._build_local_dev(workload),
            CostCalculator._build_scheduled_dev(workload, storage_cost, io_cost, backup_cost, region_mult),
            CostCalculator._build_provisioned_single_az(workload, storage_cost, io_cost, backup_cost, region_mult),
            CostCalculator._build_provisioned_multi_az(workload, storage_cost, io_cost, backup_cost, region_mult),
            CostCalculator._build_serverless_elastic(workload, storage_cost, io_cost, backup_cost, region_mult)
        ]

        # Select target option matching user's selected_deployment
        selected_option = next(
            (opt for opt in options if opt.id == workload.selected_deployment),
            options[1]  # Default to scheduled_dev
        )

        # Potential savings: difference between 24/7 Provisioned Single-AZ / Multi-AZ and the selected or recommended scheduled tier
        continuous_single_az_cost = options[2].monthly_cost
        selected_cost = selected_option.monthly_cost
        potential_savings = max(0.0, round(continuous_single_az_cost - selected_cost, 2))

        # Build CostHealth analysis
        cost_health = CostCalculator.calculate_cost_health(workload, selected_option, continuous_single_az_cost)

        return CostEstimateResponse(
            workload=workload,
            selected_deployment=selected_option,
            comparison_options=options,
            potential_monthly_savings=potential_savings,
            cost_health=cost_health,
            pricing_metadata={
                "pricing_version": DOCUMENTDB_PRICING["version"],
                "pricing_source": DOCUMENTDB_PRICING["source"],
                "baseline_region": workload.region,
                "region_multiplier": region_mult,
                "hourly_rate_db_t3_medium": DOCUMENTDB_PRICING["compute_hourly"]["db.t3.medium"] * region_mult,
                "storage_rate_per_gb": DOCUMENTDB_PRICING["storage_per_gb_month"] * region_mult,
                "io_rate_per_million": DOCUMENTDB_PRICING["io_per_million"] * region_mult,
                "backup_rate_per_gb": DOCUMENTDB_PRICING["backup_per_gb_month"] * region_mult,
                "disclaimer": "Academic Project Simulation Mode. Pricing calculations are computed deterministically from AWS rate cards. No live AWS resources are billed or provisioned."
            }
        )

    @staticmethod
    def calculate_cost_health(
        workload: WorkloadInput,
        selected_option: DeploymentOptionEstimate,
        baseline_247_cost: float
    ) -> CostHealth:
        total = max(0.01, selected_option.monthly_cost)
        breakdown = selected_option.breakdown
        
        # Determine dominant cost component
        components = [
            ("Compute", breakdown.compute_cost),
            ("Storage", breakdown.storage_cost),
            ("I/O", breakdown.io_cost),
            ("Backup", breakdown.backup_cost)
        ]
        components.sort(key=lambda x: x[1], reverse=True)
        dominant_driver = components[0][0]
        dominant_percent = round((components[0][1] / total) * 100)

        # Potential optimization percent vs 24/7 continuous baseline
        if baseline_247_cost > 0 and selected_option.monthly_cost < baseline_247_cost:
            opt_percent = round(((baseline_247_cost - selected_option.monthly_cost) / baseline_247_cost) * 100, 1)
        elif selected_option.monthly_uptime_hours >= 700:
            # If currently 24/7, potential savings from scheduling to 160h
            opt_percent = round(((730 - 160) / 730.0) * (breakdown.compute_cost / total) * 100, 1)
        else:
            opt_percent = 0.0

        # Pattern identification
        if selected_option.id == "local_dev":
            usage_pattern = "Local Development (Zero Cloud Cost)"
        elif selected_option.monthly_uptime_hours <= 160:
            usage_pattern = "Scheduled Development / Intermittent"
        elif workload.environment_tier == "production" or selected_option.high_availability:
            usage_pattern = "Production Continuous High-Availability"
        else:
            usage_pattern = "Continuous Single-AZ (Non-HA)"

        # Data-backed deterministic insights
        insights = []
        if breakdown.compute_cost > 0:
            insights.append(f"{dominant_driver} is the largest cost driver, contributing {dominant_percent}% (${breakdown.compute_cost:.2f}/mo) based on {selected_option.monthly_uptime_hours} running hours.")
        if breakdown.storage_cost > 0:
            insights.append(f"Storage contributes ${breakdown.storage_cost:.2f}/mo for {workload.data_storage_gb} GB active cluster volume.")
        if breakdown.io_cost > 0:
            insights.append(f"Request I/O generates ${breakdown.io_cost:.2f}/mo from {workload.requests_per_day:,} requests/day with {workload.write_percentage}% write ratio.")
        if breakdown.backup_cost > 0:
            insights.append(f"Backup retention ({workload.backup_retention_days} days) accrues ${breakdown.backup_cost:.2f}/mo for storage exceeding free 100% cluster size.")
        else:
            insights.append("Backup storage is 100% free (within cluster size allowance).")

        summary = f"Estimated monthly commitment is ${selected_option.monthly_cost:.2f}. {dominant_driver} represents {dominant_percent}% of the total estimate."

        return CostHealth(
            current_cost=selected_option.monthly_cost,
            potential_optimization_percent=opt_percent,
            main_cost_driver=dominant_driver,
            usage_pattern=usage_pattern,
            health_summary=summary,
            deterministic_insights=insights
        )

    @staticmethod
    def _build_local_dev(workload: WorkloadInput) -> DeploymentOptionEstimate:
        breakdown = CostBreakdown(
            compute_cost=0.0,
            storage_cost=0.0,
            io_cost=0.0,
            backup_cost=0.0,
            total_monthly_cost=0.0,
            hourly_rate_effective=0.0,
            assumptions=[
                "Runs purely inside local in-memory mock repository or Docker container",
                "Zero AWS cloud infrastructure used",
                "Local persistence stored on developer workstation SSD"
            ],
            included_components=["Local developer execution", "Zero-cost simulation"],
            excluded_components=["AWS Cloud infrastructure", "Managed backups", "Multi-AZ replication"]
        )
        return DeploymentOptionEstimate(
            id="local_dev",
            name="1. Local Development (In-Memory / Docker)",
            instance_type="None (Localhost)",
            node_count=0,
            monthly_uptime_hours=0,
            high_availability=False,
            monthly_cost=0.0,
            breakdown=breakdown,
            suitability="Initial development, UI building, query experimentation, and unit testing.",
            scalability="None (Single developer machine)",
            operational_complexity="Low",
            pros=["Zero AWS cluster costs ($0.00)", "Instant query response", "No AWS networking or IAM configuration needed"],
            cons=["No cloud multi-user persistence", "No automated AWS snapshots", "Not accessible from cloud environments"],
            recommended_for="Phase 1 development, student coursework, and local validation."
        )

    @staticmethod
    def _build_scheduled_dev(
        workload: WorkloadInput,
        storage_cost: float,
        io_cost: float,
        backup_cost: float,
        region_mult: float
    ) -> DeploymentOptionEstimate:
        uptime_hours = workload.monthly_uptime_hours if workload.monthly_uptime_hours < 730 else 160
        hourly_rate = DOCUMENTDB_PRICING["compute_hourly"]["db.t3.medium"] * region_mult
        compute_cost = round(uptime_hours * hourly_rate, 2)
        total_cost = round(compute_cost + storage_cost + io_cost + backup_cost, 2)

        breakdown = CostBreakdown(
            compute_cost=compute_cost,
            storage_cost=storage_cost,
            io_cost=io_cost,
            backup_cost=backup_cost,
            total_monthly_cost=total_cost,
            hourly_rate_effective=round(total_cost / max(1, uptime_hours), 3),
            assumptions=[
                f"Cluster scheduled to run {uptime_hours} hours/month (e.g., 8 hours/weekday during lab hours)",
                "Cluster stopped during nights and weekends via AWS EventBridge / Lambda",
                "Storage and backup costs continue to accrue while cluster compute is stopped",
                f"Instance: 1x db.t3.medium (${hourly_rate:.3f}/hr)"
            ],
            included_components=["Compute (scheduled)", "Active storage", "Request I/Os", "Incremental backups"],
            excluded_components=["Data transfer egress", "VPC Endpoint charges", "NAT Gateway"]
        )

        return DeploymentOptionEstimate(
            id="scheduled_dev",
            name="2. Scheduled Development Tier (db.t3.medium)",
            instance_type="1x db.t3.medium (Scheduled)",
            node_count=1,
            monthly_uptime_hours=uptime_hours,
            high_availability=False,
            monthly_cost=total_cost,
            breakdown=breakdown,
            suitability="College AWS projects, staging environments, and scheduled developer testing.",
            scalability="Manual vertical scaling to larger instance sizes if needed",
            operational_complexity="Medium (Requires CloudWatch / Lambda stop schedule)",
            pros=[
                f"Saves ~{round((1.0 - (uptime_hours / 730.0)) * 100)}% on compute costs vs 24/7 continuous operation",
                "Full Amazon DocumentDB MongoDB 4.0/5.0 wire protocol compatibility",
                "Automated nightly backups preserved while stopped"
            ],
            cons=[
                "Cluster requires ~5-8 minutes startup delay upon resuming",
                "Single-AZ: No automatic failover if AZ encounters an outage",
                "Storage costs continue to accrue 24/7 while stopped"
            ],
            recommended_for="College AWS project evaluation and team testing without paying for idle nights/weekends."
        )

    @staticmethod
    def _build_provisioned_single_az(
        workload: WorkloadInput,
        storage_cost: float,
        io_cost: float,
        backup_cost: float,
        region_mult: float
    ) -> DeploymentOptionEstimate:
        uptime_hours = 730
        hourly_rate = DOCUMENTDB_PRICING["compute_hourly"]["db.t3.medium"] * region_mult
        compute_cost = round(uptime_hours * hourly_rate, 2)
        total_cost = round(compute_cost + storage_cost + io_cost + backup_cost, 2)

        breakdown = CostBreakdown(
            compute_cost=compute_cost,
            storage_cost=storage_cost,
            io_cost=io_cost,
            backup_cost=backup_cost,
            total_monthly_cost=total_cost,
            hourly_rate_effective=round(total_cost / 730, 3),
            assumptions=[
                "Runs 24 hours/day, 7 days/week (730 hours/month)",
                "Single primary instance: 1x db.t3.medium in single Availability Zone",
                "Continuous automated backup window enabled"
            ],
            included_components=["24/7 Compute", "Active cluster storage", "Request I/Os", "Continuous backups"],
            excluded_components=["Multi-AZ replica instances", "Inter-AZ data replication"]
        )

        return DeploymentOptionEstimate(
            id="provisioned_single_az",
            name="3. Continuous Single-AZ Tier (db.t3.medium)",
            instance_type="1x db.t3.medium (24/7)",
            node_count=1,
            monthly_uptime_hours=730,
            high_availability=False,
            monthly_cost=total_cost,
            breakdown=breakdown,
            suitability="Light production workloads, continuous 24/7 API backends with modest request traffic.",
            scalability="Vertical instance resize or read replica additions",
            operational_complexity="Low",
            pros=[
                "Continuous availability with zero startup delay",
                "Consistent baseline performance for scheduled batch jobs",
                "Simple architecture with single connection endpoint"
            ],
            cons=[
                "Incurs charges during inactive night/weekend hours",
                "No automatic multi-AZ failover (downtime during underlying host maintenance)",
                "Burst credits may deplete under prolonged continuous heavy CPU spikes"
            ],
            recommended_for="Small departmental applications requiring 24/7 uptime without high-availability redundancy."
        )

    @staticmethod
    def _build_provisioned_multi_az(
        workload: WorkloadInput,
        storage_cost: float,
        io_cost: float,
        backup_cost: float,
        region_mult: float
    ) -> DeploymentOptionEstimate:
        uptime_hours = 730
        hourly_rate = DOCUMENTDB_PRICING["compute_hourly"]["db.t3.medium"] * region_mult
        compute_cost = round(uptime_hours * hourly_rate * 2, 2)
        total_cost = round(compute_cost + storage_cost + io_cost + backup_cost, 2)

        breakdown = CostBreakdown(
            compute_cost=compute_cost,
            storage_cost=storage_cost,
            io_cost=io_cost,
            backup_cost=backup_cost,
            total_monthly_cost=total_cost,
            hourly_rate_effective=round(total_cost / 730, 3),
            assumptions=[
                "2x db.t3.medium instances across 2 distinct Availability Zones (1 Primary + 1 Read Replica)",
                "Automated sub-minute failover via Amazon DocumentDB cluster endpoint",
                "Shared distributed storage layer (6-way replication across 3 AZs)"
            ],
            included_components=["2x Compute instances (Multi-AZ)", "6-way replicated storage", "Read/Write I/Os", "Daily backups"],
            excluded_components=["Cross-region replication", "Direct Connect"]
        )

        return DeploymentOptionEstimate(
            id="provisioned_multi_az",
            name="4. Multi-AZ High Availability Tier (2x db.t3.medium)",
            instance_type="2x db.t3.medium (Primary + Replica)",
            node_count=2,
            monthly_uptime_hours=730,
            high_availability=True,
            monthly_cost=total_cost,
            breakdown=breakdown,
            suitability="Production enterprise inspection systems requiring strict SLA uptime and zero data loss.",
            scalability="High (Up to 15 read replicas, read scaling across replica endpoint)",
            operational_complexity="Medium",
            pros=[
                "Automated failover with typical recovery in < 30 seconds",
                "Read workload offloading to secondary replica endpoint",
                "High fault tolerance against AZ infrastructure disruptions"
            ],
            cons=[
                f"Double the compute cost of Single-AZ (~${compute_cost:.2f}/mo on compute)",
                "Over-provisioned for small college development workloads",
                "Requires careful VPC subnet group routing across multiple AZs"
            ],
            recommended_for="Mission-critical enterprise deployments where downtime carries severe financial or safety penalties."
        )

    @staticmethod
    def _build_serverless_elastic(
        workload: WorkloadInput,
        storage_cost: float,
        io_cost: float,
        backup_cost: float,
        region_mult: float
    ) -> DeploymentOptionEstimate:
        base_dcus = 1.5 if workload.traffic_pattern in ["bursty", "intermittent"] else 2.0
        hourly_rate = DOCUMENTDB_PRICING["compute_hourly"]["elastic_dcu"] * base_dcus * region_mult
        compute_cost = round(730 * hourly_rate, 2)
        total_cost = round(compute_cost + storage_cost + io_cost + backup_cost, 2)

        breakdown = CostBreakdown(
            compute_cost=compute_cost,
            storage_cost=storage_cost,
            io_cost=io_cost,
            backup_cost=backup_cost,
            total_monthly_cost=total_cost,
            hourly_rate_effective=round(hourly_rate, 3),
            assumptions=[
                f"Dynamic capacity scaling based on demand ({base_dcus} DCUs average baseline)",
                "Automatic scaling up during heavy inspection uploads and scaling down during idle periods",
                "Storage and I/O metered identically to standard DocumentDB clusters"
            ],
            included_components=["Elastic compute scaling", "Distributed storage", "Request I/Os", "Backup snapshots"],
            excluded_components=["Dedicated reserved instance discounts"]
        )

        return DeploymentOptionEstimate(
            id="serverless_elastic",
            name="5. Elastic Scalable Tier (Amazon DocumentDB Elastic)",
            instance_type="Dynamic DCUs (Elastic Scaling)",
            node_count=1,
            monthly_uptime_hours=730,
            high_availability=True,
            monthly_cost=total_cost,
            breakdown=breakdown,
            suitability="Unpredictable or highly variable inspection telemetry traffic with sharp peak spikes.",
            scalability="Automatic seamless horizontal & vertical scaling",
            operational_complexity="Low",
            pros=[
                "Zero manual instance resizing needed",
                "Handles sudden massive bursts (e.g. hundreds of simultaneous field inspectors)",
                "Built-in multi-AZ high availability"
            ],
            cons=[
                "Higher cost per compute unit than scheduled provisioned instances",
                "Minimum baseline capacity floor charges",
                "Regional availability may be more limited than standard provisioned clusters"
            ],
            recommended_for="Production workloads with spiky, unpredictable traffic patterns where operational simplicity is paramount."
        )

    # ---------------------------------------------------------
    # COST MONITORING & TREND METHODS (ACADEMIC SIMULATION MODE)
    # ---------------------------------------------------------

    @staticmethod
    def calculate_cost_trend(workload: WorkloadInput, timeframe: str = "30d") -> CostTrendResponse:
        """
        Generates deterministic daily and cumulative cost points over the requested timeframe
        based on the user's workload parameters.
        """
        days_map = {"7d": 7, "30d": 30, "90d": 90}
        total_days = days_map.get(timeframe, 30)

        estimate = CostCalculator.calculate_workload_costs(workload)
        monthly_total = estimate.selected_deployment.monthly_cost
        breakdown = estimate.selected_deployment.breakdown

        base_daily_compute = breakdown.compute_cost / 30.0
        base_daily_storage = breakdown.storage_cost / 30.0
        base_daily_io = breakdown.io_cost / 30.0
        base_daily_backup = breakdown.backup_cost / 30.0
        base_daily_total = monthly_total / 30.0

        points: List[CostTrendPoint] = []
        cumulative_sum = 0.0

        # Simulate day-by-day pattern (e.g. slight weekday vs weekend variation if scheduled dev)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=total_days - 1)

        is_scheduled = workload.monthly_uptime_hours < 500

        for day_idx in range(total_days):
            current_day = start_date + timedelta(days=day_idx)
            is_weekend = current_day.weekday() in [5, 6]  # Saturday, Sunday

            if is_scheduled and is_weekend:
                # Weekend reduction for scheduled development workloads
                day_compute = base_daily_compute * 0.15
                day_io = base_daily_io * 0.25
                day_storage = base_daily_storage
                day_backup = base_daily_backup
            else:
                day_compute = base_daily_compute
                day_io = base_daily_io
                day_storage = base_daily_storage
                day_backup = base_daily_backup

            day_total = round(day_compute + day_storage + day_io + day_backup, 4)
            cumulative_sum += day_total

            points.append(
                CostTrendPoint(
                    date=current_day.strftime("%Y-%m-%d"),
                    day_number=day_idx + 1,
                    daily_cost=round(day_total, 3),
                    cumulative_cost=round(cumulative_sum, 2),
                    projected_monthly_cost=round(monthly_total, 2),
                    compute_cost=round(day_compute, 3),
                    storage_cost=round(day_storage, 3),
                    io_cost=round(day_io, 3),
                    backup_cost=round(day_backup, 3)
                )
            )

        return CostTrendResponse(
            timeframe=timeframe,
            current_daily_cost=round(base_daily_total, 2),
            projected_monthly_cost=round(monthly_total, 2),
            points=points,
            assumptions=f"Simulated {total_days}-day cost trajectory based on {workload.selected_deployment} deployment ({workload.monthly_uptime_hours}h/mo uptime)."
        )

    @staticmethod
    def calculate_cost_drivers(workload: WorkloadInput, estimate: CostEstimateResponse) -> List[CostDriverDetail]:
        """
        Deconstructs workload factors and attributes exact dollar contributions and % impacts.
        """
        breakdown = estimate.selected_deployment.breakdown
        total = max(0.01, breakdown.total_monthly_cost)

        drivers: List[CostDriverDetail] = []

        # 1. Instance Runtime
        compute_pct = round((breakdown.compute_cost / total) * 100, 1)
        compute_impact = "High" if breakdown.compute_cost > 10.0 or compute_pct > 60 else "Medium" if breakdown.compute_cost > 3.0 else "Low"
        compute_savings = max(0.0, round(breakdown.compute_cost - (160 * DOCUMENTDB_PRICING["compute_hourly"]["db.t3.medium"] * estimate.pricing_metadata.get("region_multiplier", 1.0)), 2)) if workload.monthly_uptime_hours > 200 else 0.0

        drivers.append(
            CostDriverDetail(
                driver_name="Instance Runtime & Compute Hours",
                current_value=f"{workload.monthly_uptime_hours} hrs/mo ({estimate.selected_deployment.instance_type})",
                monthly_cost_contribution=breakdown.compute_cost,
                percentage_of_total=compute_pct,
                impact_level=compute_impact,
                optimization_opportunity="Adopt scheduled 8h/weekday automated shutdown (160 hrs/mo) for non-production environments." if workload.monthly_uptime_hours > 200 else "Optimal scheduled runtime already configured.",
                potential_savings=compute_savings
            )
        )

        # 2. Document Storage Allocation
        storage_pct = round((breakdown.storage_cost / total) * 100, 1)
        storage_impact = "High" if breakdown.storage_cost > 15.0 else "Medium" if breakdown.storage_cost > 2.0 else "Low"
        drivers.append(
            CostDriverDetail(
                driver_name="Active Document Storage Size",
                current_value=f"{workload.data_storage_gb} GB (6-Way Replicated across 3 AZs)",
                monthly_cost_contribution=breakdown.storage_cost,
                percentage_of_total=storage_pct,
                impact_level=storage_impact,
                optimization_opportunity="Enable automated TTL indexing on raw telemetry logs and archive old inspection reports to S3.",
                potential_savings=round(breakdown.storage_cost * 0.3, 2) if workload.data_storage_gb > 20 else 0.0
            )
        )

        # 3. Read/Write Request I/O
        io_pct = round((breakdown.io_cost / total) * 100, 1)
        io_impact = "Medium" if breakdown.io_cost > 5.0 else "Low"
        drivers.append(
            CostDriverDetail(
                driver_name="Database Request I/O Volume",
                current_value=f"{workload.requests_per_day:,} req/day ({workload.read_percentage}% read / {workload.write_percentage}% write)",
                monthly_cost_contribution=breakdown.io_cost,
                percentage_of_total=io_pct,
                impact_level=io_impact,
                optimization_opportunity="Create compound indexes on nested query attributes (e.g. findings.severity) to prevent full collection scans.",
                potential_savings=round(breakdown.io_cost * 0.4, 2) if breakdown.io_cost > 1.0 else 0.0
            )
        )

        # 4. Backup Retention Window
        backup_pct = round((breakdown.backup_cost / total) * 100, 1)
        backup_impact = "Medium" if breakdown.backup_cost > 2.0 else "Low"
        drivers.append(
            CostDriverDetail(
                driver_name="Automated Backup Retention",
                current_value=f"{workload.backup_retention_days} days retention (First 100% free)",
                monthly_cost_contribution=breakdown.backup_cost,
                percentage_of_total=backup_pct,
                impact_level=backup_impact,
                optimization_opportunity="Cap backup retention to 7 days for dev/demo clusters; use manual S3 exports for long-term historical records.",
                potential_savings=breakdown.backup_cost if workload.backup_retention_days > 7 else 0.0
            )
        )

        # Sort by cost contribution descending
        drivers.sort(key=lambda d: d.monthly_cost_contribution, reverse=True)
        return drivers

    @staticmethod
    def detect_cost_anomalies(
        current_workload: WorkloadInput,
        baseline_workload: Optional[WorkloadInput] = None
    ) -> CostAnomalyReport:
        """
        Compares the current workload with a previous baseline or standard rules
        to detect significant cost variations, spikes, or structural changes.
        """
        current_est = CostCalculator.calculate_workload_costs(current_workload)
        current_cost = current_est.selected_deployment.monthly_cost

        if baseline_workload is None:
            # Rule-based detection against ideal development posture
            factors = []
            has_anomaly = False
            severity = "info"

            if current_workload.monthly_uptime_hours >= 700 and current_workload.environment_tier == "development":
                factors.append("24/7 continuous uptime configured for development tier (adds ~$44.46/mo compute over scheduled dev)")
                has_anomaly = True
                severity = "warning"

            if current_workload.backup_retention_days > 14 and current_workload.environment_tier == "development":
                factors.append(f"Excessive backup retention ({current_workload.backup_retention_days} days) for non-production environment")
                has_anomaly = True

            if current_workload.availability_tier == "multi_az" and current_workload.environment_tier == "development":
                factors.append("Multi-AZ high availability provisioned for development (doubles compute instance count)")
                has_anomaly = True
                severity = "warning"

            return CostAnomalyReport(
                has_anomaly=has_anomaly,
                change_direction="stable" if not has_anomaly else "increase",
                change_percent=0.0,
                dollar_difference=0.0,
                baseline_cost=current_cost,
                current_cost=current_cost,
                contributing_factors=factors if factors else ["Workload parameters are within standard baseline ranges."],
                severity=severity,
                detected_rule="Workload Optimization Best-Practice Rule Check"
            )

        # Compare current with baseline
        baseline_est = CostCalculator.calculate_workload_costs(baseline_workload)
        baseline_cost = baseline_est.selected_deployment.monthly_cost

        diff = round(current_cost - baseline_cost, 2)
        pct_change = round(((diff / baseline_cost) * 100) if baseline_cost > 0 else 0.0, 1)

        factors = []
        if current_workload.monthly_uptime_hours != baseline_workload.monthly_uptime_hours:
            uptime_diff = current_workload.monthly_uptime_hours - baseline_workload.monthly_uptime_hours
            factors.append(f"Instance runtime changed from {baseline_workload.monthly_uptime_hours}h to {current_workload.monthly_uptime_hours}h/mo ({uptime_diff:+d} hrs)")

        if current_workload.data_storage_gb != baseline_workload.data_storage_gb:
            storage_diff = round(current_workload.data_storage_gb - baseline_workload.data_storage_gb, 1)
            factors.append(f"Storage allocation changed from {baseline_workload.data_storage_gb} GB to {current_workload.data_storage_gb} GB ({storage_diff:+0.1f} GB)")

        if current_workload.selected_deployment != baseline_workload.selected_deployment:
            factors.append(f"Deployment tier switched from '{baseline_workload.selected_deployment}' to '{current_workload.selected_deployment}'")

        if current_workload.requests_per_day != baseline_workload.requests_per_day:
            req_diff = current_workload.requests_per_day - baseline_workload.requests_per_day
            factors.append(f"Daily request volume changed from {baseline_workload.requests_per_day:,} to {current_workload.requests_per_day:,} req/day ({req_diff:+d})")

        has_anomaly = abs(pct_change) >= 10.0 or abs(diff) >= 5.0
        direction = "increase" if diff > 0.01 else "decrease" if diff < -0.01 else "stable"
        severity = "critical" if pct_change >= 40.0 else "warning" if pct_change >= 15.0 else "info"

        return CostAnomalyReport(
            has_anomaly=has_anomaly,
            change_direction=direction,
            change_percent=pct_change,
            dollar_difference=diff,
            baseline_cost=baseline_cost,
            current_cost=current_cost,
            contributing_factors=factors if factors else ["No significant parameter deviations between periods."],
            severity=severity,
            detected_rule=f"Period-over-Period Delta Analysis ({direction.capitalize()} of {abs(pct_change)}%)"
        )

    @staticmethod
    def simulate_optimization(request: OptimizationSimulationRequest) -> OptimizationSimulationResponse:
        """
        Calculates what-if difference when applying proposed architectural changes.
        """
        current_est = CostCalculator.calculate_workload_costs(request.current_workload)
        current_cost = current_est.selected_deployment.monthly_cost

        # Create simulated workload copy
        sim_data = request.current_workload.model_dump()
        if request.proposed_uptime_hours is not None:
            sim_data["monthly_uptime_hours"] = request.proposed_uptime_hours
        if request.proposed_storage_gb is not None:
            sim_data["data_storage_gb"] = request.proposed_storage_gb
        if request.proposed_deployment is not None:
            sim_data["selected_deployment"] = request.proposed_deployment
        if request.proposed_backup_days is not None:
            sim_data["backup_retention_days"] = request.proposed_backup_days

        simulated_workload = WorkloadInput(**sim_data)
        sim_est = CostCalculator.calculate_workload_costs(simulated_workload)
        sim_cost = sim_est.selected_deployment.monthly_cost

        diff = round(current_cost - sim_cost, 2)
        pct_savings = round(((diff / current_cost) * 100) if current_cost > 0 else 0.0, 1)

        tradeoffs = []
        if request.proposed_uptime_hours is not None and request.proposed_uptime_hours < request.current_workload.monthly_uptime_hours:
            tradeoffs.append(f"Database will be stopped outside business hours (~{730 - request.proposed_uptime_hours} hrs/mo idle downtime).")
        if request.proposed_backup_days is not None and request.proposed_backup_days < request.current_workload.backup_retention_days:
            tradeoffs.append(f"Point-in-time recovery window reduced to {request.proposed_backup_days} days.")

        explanation = f"Simulating changes reduces estimated cost from ${current_cost:.2f}/mo to ${sim_cost:.2f}/mo, yielding ${diff:.2f}/mo ({pct_savings}%) estimated difference under current pricing assumptions."

        return OptimizationSimulationResponse(
            current_cost=current_cost,
            simulated_cost=sim_cost,
            dollar_difference=diff,
            percentage_savings=pct_savings,
            explanation=explanation,
            tradeoffs=tradeoffs if tradeoffs else ["No significant operational trade-off detected."],
            simulated_breakdown=sim_est.selected_deployment.breakdown
        )

