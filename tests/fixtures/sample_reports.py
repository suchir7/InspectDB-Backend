"""
Sample inspection reports isolated strictly as test fixtures for unit and integration testing.
This file must NEVER be imported or used by production application endpoints or default repositories.
"""

from typing import List, Dict, Any

SAMPLE_REPORTS: List[Dict[str, Any]] = [
    {
        "id": "RPT-2026-0891",
        "title": "Substation Alpha-3 High-Voltage Grid & Transformer Diagnostics",
        "inspector_name": "Marcus Vance, PE",
        "location": "North Sector Grid Substation 4B, Austin TX",
        "inspection_date": "2026-03-28",
        "category": "Electrical",
        "status": "action_required",
        "overall_severity": "high",
        "description": "Comprehensive thermographic and electrical impedance inspection of Substation Alpha-3 primary feed transformers and switchgear line-up.",
        "findings": [
            {
                "finding_id": "FND-ELEC-01",
                "category": "Thermal Anomaly",
                "severity": "high",
                "description": "Hotspot detected on Phase B busbar junction showing delta-T of 38.4°C over ambient.",
                "location_details": "Main Switchgear Enclosure SG-02, Bay 3",
                "issues": [
                    {
                        "issue_id": "ISS-0101",
                        "title": "Loose bolted connection on secondary terminal",
                        "severity": "high",
                        "code_reference": "NEC 110.14(D)",
                        "status": "open",
                        "notes": "Torque check required immediately; re-torque to 50 ft-lbs per manufacturer spec."
                    },
                    {
                        "issue_id": "ISS-0102",
                        "title": "Thermal degradation of phase insulator",
                        "severity": "medium",
                        "code_reference": "IEEE 141-1993",
                        "status": "open",
                        "notes": "Surface pitting observed on porcelain bushing."
                    }
                ],
                "custom_metrics": {
                    "ambient_temp_c": 24.2,
                    "measured_temp_c": 62.6,
                    "delta_t_c": 38.4,
                    "emissivity_setting": 0.95
                }
            },
            {
                "finding_id": "FND-ELEC-02",
                "category": "Grounding System",
                "severity": "medium",
                "description": "Ground grid impedance higher than recommended safety threshold.",
                "location_details": "Perimeter Ground Ring Test Well #4",
                "issues": [
                    {
                        "issue_id": "ISS-0103",
                        "title": "Corroded ground rod clamp connection",
                        "severity": "medium",
                        "code_reference": "NEC 250.53",
                        "status": "in_progress",
                        "notes": "Exothermic weld replacement scheduled."
                    }
                ],
                "custom_metrics": {
                    "ground_resistance_ohms": 8.4,
                    "max_allowable_ohms": 5.0
                }
            }
        ],
        "custom_fields": [
            {"key": "transformer_kilo_volt_amperes", "value": 2500, "field_type": "number"},
            {"key": "grid_frequency_hz", "value": 60.02, "field_type": "number"},
            {"key": "infrared_camera_serial", "value": "FLIR-T865-98311", "field_type": "string"},
            {"key": "arc_flash_hazard_category", "value": "Category 4 (40 cal/cm²)", "field_type": "string"}
        ],
        "dynamic_attributes": {
            "electrical_telemetry": {
                "phases": {
                    "phase_a": {"voltage_kv": 13.82, "current_amps": 104.5, "power_factor": 0.94},
                    "phase_b": {"voltage_kv": 13.78, "current_amps": 118.2, "power_factor": 0.91},
                    "phase_c": {"voltage_kv": 13.81, "current_amps": 102.1, "power_factor": 0.95}
                },
                "dielectric_breakdown_voltage_kv": 32.5,
                "dissolved_gas_analysis_ppm": {"hydrogen": 12, "methane": 4, "acetylene": 0.2, "ethylene": 2}
            }
        },
        "created_at": "2026-03-28T14:30:00Z",
        "updated_at": "2026-03-28T16:45:00Z",
        "is_sample": True
    },
    {
        "id": "RPT-2026-0892",
        "title": "Commercial High-Rise Fire Safety & Life Protection Audit",
        "inspector_name": "Elena Rostova",
        "location": "Pinnacle Tower (Floors 1-42), Chicago IL",
        "inspection_date": "2026-03-25",
        "category": "Fire Safety",
        "status": "action_required",
        "overall_severity": "critical",
        "description": "Annual NFPA life safety inspection, standpipe flow testing, and emergency egress verification across all operational levels.",
        "findings": [
            {
                "finding_id": "FND-FIRE-01",
                "category": "Means of Egress",
                "severity": "critical",
                "description": "Emergency exit stairway obstructed by commercial pallet storage on Floor 14.",
                "location_details": "Stairwell B, 14th Floor Service Corridor",
                "issues": [
                    {
                        "issue_id": "ISS-0201",
                        "title": "Blocked egress path violating minimum clearance",
                        "severity": "critical",
                        "code_reference": "NFPA 101 Sec 7.1.10",
                        "status": "open",
                        "notes": "Immediate notice of violation issued to tenant; 24hr abatement order."
                    }
                ],
                "custom_metrics": {
                    "measured_corridor_width_inches": 22.0,
                    "required_width_inches": 44.0
                }
            },
            {
                "finding_id": "FND-FIRE-02",
                "category": "Suppression Systems",
                "severity": "high",
                "description": "Fire pump emergency backup generator failed automated transfer switch test.",
                "location_details": "Sub-Basement Pump Room B2",
                "issues": [
                    {
                        "issue_id": "ISS-0202",
                        "title": "ATS Controller Fault on transfer command",
                        "severity": "high",
                        "code_reference": "NFPA 20 Sec 10.8",
                        "status": "in_progress",
                        "notes": "Emergency diesel service contractor dispatched."
                    }
                ],
                "custom_metrics": {
                    "sprinkler_static_psi": 142.0,
                    "residual_flow_gpm": 980.0
                }
            }
        ],
        "custom_fields": [
            {"key": "building_occupancy_load", "value": 3450, "field_type": "number"},
            {"key": "sprinkler_coverage_pct", "value": 100, "field_type": "number"},
            {"key": "fire_alarm_panel_model", "value": "Notifier NFS2-3030", "field_type": "string"},
            {"key": "evacuation_drill_completed", "value": True, "field_type": "boolean"}
        ],
        "dynamic_attributes": {
            "fire_safety_matrix": {
                "extinguisher_inventory_count": 184,
                "inspected_extinguishers": 184,
                "expired_tags_count": 3,
                "standpipe_zones": [
                    {"zone_name": "Low Zone (L1-L15)", "pressure_psi": 165, "status": "nominal"},
                    {"zone_name": "Mid Zone (L16-L30)", "pressure_psi": 158, "status": "nominal"},
                    {"zone_name": "High Zone (L31-L42)", "pressure_psi": 142, "status": "marginal"}
                ],
                "smoke_damper_test_passed_pct": 98.4
            }
        },
        "created_at": "2026-03-25T10:15:00Z",
        "updated_at": "2026-03-26T09:00:00Z",
        "is_sample": True
    },
    {
        "id": "RPT-2026-0893",
        "title": "Heavy Production Line Turbine & Centrifugal Pump Health Assessment",
        "inspector_name": "David Sterling, CMRP",
        "location": "Apex Advanced Manufacturing Facility, Detroit MI",
        "inspection_date": "2026-03-22",
        "category": "Equipment",
        "status": "passed",
        "overall_severity": "low",
        "description": "Routine quarterly predictive maintenance inspection using tri-axial vibration, ultrasound, and lubricant spectrometry.",
        "findings": [
            {
                "finding_id": "FND-EQP-01",
                "category": "Bearing Condition",
                "severity": "low",
                "description": "Non-drive end bearing shows slight high-frequency micro-peaking indicative of early stage 1 lubrication film shearing.",
                "location_details": "Boiler Feed Pump #3, Motor Drive End",
                "issues": [
                    {
                        "issue_id": "ISS-0301",
                        "title": "Scheduled re-greasing recommended",
                        "severity": "low",
                        "code_reference": "ISO 10816-3",
                        "status": "resolved",
                        "notes": "Synthetic polyurea grease applied; vibration baseline restored to normal."
                    }
                ],
                "custom_metrics": {
                    "overall_rms_velocity_mms": 1.45,
                    "iso_alarm_limit_mms": 4.50,
                    "crest_factor": 3.1
                }
            }
        ],
        "custom_fields": [
            {"key": "operating_hours_total", "value": 14820, "field_type": "number"},
            {"key": "nominal_rpm", "value": 3550, "field_type": "number"},
            {"key": "motor_horsepower", "value": 450, "field_type": "number"},
            {"key": "oil_sampling_iso_cleanliness", "value": "16/14/11", "field_type": "string"}
        ],
        "dynamic_attributes": {
            "vibration_spectral_peaks_hz": [59.2, 118.4, 355.0, 710.0],
            "oil_analysis_ppm": {
                "iron": 14,
                "copper": 3,
                "lead": 1,
                "silicon": 8,
                "water_pct": 0.02
            },
            "warranty_valid_until": "2027-12-31"
        },
        "created_at": "2026-03-22T08:00:00Z",
        "updated_at": "2026-03-22T12:30:00Z",
        "is_sample": True
    },
    {
        "id": "RPT-2026-0894",
        "title": "Interstate Bridge Pier Structural Integrity & Concrete Core Analysis",
        "inspector_name": "Dr. Aris Thorne, SE",
        "location": "Route 9 River Crossing, Bridge ID #BR-8820, Seattle WA",
        "inspection_date": "2026-03-20",
        "category": "Structural",
        "status": "in_review",
        "overall_severity": "medium",
        "description": "Biennial underwater and above-water structural inspection examining pier settlement, concrete spalling, scour, and rebar corrosion.",
        "findings": [
            {
                "finding_id": "FND-STR-01",
                "category": "Substructure Pier Spalling",
                "severity": "medium",
                "description": "Concrete delamination and exposed longitudinal rebar with surface oxidation.",
                "location_details": "Pier 4 Splash Zone, Downstream Face",
                "issues": [
                    {
                        "issue_id": "ISS-0401",
                        "title": "Chloride-induced corrosion of reinforcing steel",
                        "severity": "medium",
                        "code_reference": "AASHTO Manual for Bridge Evaluation",
                        "status": "in_progress",
                        "notes": "Core extraction completed. Chloride ion penetration depth is 38mm."
                    },
                    {
                        "issue_id": "ISS-0402",
                        "title": "Diagonal shear crack propagation",
                        "severity": "medium",
                        "code_reference": "ACI 318-19",
                        "status": "open",
                        "notes": "Crack width monitored via telltale gauge at 1.8mm."
                    }
                ],
                "custom_metrics": {
                    "max_crack_width_mm": 1.8,
                    "spall_area_sq_meters": 2.4,
                    "scour_depth_meters": 0.65
                }
            }
        ],
        "custom_fields": [
            {"key": "bridge_national_inventory_id", "value": "WA-DOT-BR8820", "field_type": "string"},
            {"key": "year_constructed", "value": 1978, "field_type": "number"},
            {"key": "average_daily_traffic", "value": 48500, "field_type": "number"},
            {"key": "sufficiency_rating_score", "value": 72.4, "field_type": "number"}
        ],
        "dynamic_attributes": {
            "concrete_core_samples": [
                {"sample_id": "C-01", "compressive_strength_psi": 4620, "depth_inches": 6.0, "chloride_ppm": 420},
                {"sample_id": "C-02", "compressive_strength_psi": 4890, "depth_inches": 6.0, "chloride_ppm": 310}
            ],
            "laser_scan_point_cloud_url": "s3://inspectdb-demo-scans/bridge-8820/cloud.laz",
            "load_rating_summary": {
                "inventory_rating_hs_tons": 28.5,
                "operating_rating_hs_tons": 44.0
            }
        },
        "created_at": "2026-03-20T11:00:00Z",
        "updated_at": "2026-03-21T15:10:00Z",
        "is_sample": True
    },
    {
        "id": "RPT-2026-0895",
        "title": "Chemical Processing Unit Environmental Safety & HazMat Containment",
        "inspector_name": "Amina Al-Mansoor, CIH",
        "location": "BioChem Refining Plant, Baytown TX",
        "inspection_date": "2026-03-18",
        "category": "Environmental",
        "status": "action_required",
        "overall_severity": "high",
        "description": "OSHA PSM and EPA Title V air emissions compliance inspection covering secondary containment, scrubber efficiency, and vapor recovery.",
        "findings": [
            {
                "finding_id": "FND-ENV-01",
                "category": "Vapor Recovery Failure",
                "severity": "high",
                "description": "Volatile Organic Compound (VOC) fugitive emissions exceeding permitted 500 ppm limit at flange seal.",
                "location_details": "Reactor Feed Loop RF-101, Valve Block 12",
                "issues": [
                    {
                        "issue_id": "ISS-0501",
                        "title": "VOC leak detected via Optical Gas Imaging (OGI)",
                        "severity": "high",
                        "code_reference": "EPA Method 21 / 40 CFR Part 60",
                        "status": "in_progress",
                        "notes": "Flange re-torquing attempted; gasket replacement ordered."
                    }
                ],
                "custom_metrics": {
                    "measured_voc_ppm": 1240.0,
                    "regulatory_threshold_ppm": 500.0,
                    "background_air_ppm": 12.5
                }
            }
        ],
        "custom_fields": [
            {"key": "epa_facility_id", "value": "TXD008129921", "field_type": "string"},
            {"key": "secondary_containment_capacity_gal", "value": 55000, "field_type": "number"},
            {"key": "hazmat_un_numbers", "value": ["UN1203", "UN1993", "UN1075"], "field_type": "json"},
            {"key": "tier_ii_reporting_compliant", "value": True, "field_type": "boolean"}
        ],
        "dynamic_attributes": {
            "chemical_inventory": [
                {"chemical": "Toluene", "cas_number": "108-88-3", "storage_volume_liters": 12500, "containment_status": "adequate"},
                {"chemical": "Benzene", "cas_number": "71-43-2", "storage_volume_liters": 4200, "containment_status": "inspected"}
            ],
            "air_quality_sensors": {
                "sensor_bay_1_ppm": 4.2,
                "sensor_bay_2_ppm": 18.9,
                "sensor_bay_3_ppm": 2.1,
                "exhaust_scrubber_efficiency_pct": 99.1
            }
        },
        "created_at": "2026-03-18T09:30:00Z",
        "updated_at": "2026-03-19T14:00:00Z",
        "is_sample": True
    },
    {
        "id": "RPT-2026-0896",
        "title": "HVAC Chilled Water Loop & Indoor Air Quality Assessment",
        "inspector_name": "Liam Gallagher",
        "location": "Metro Hospital South Pavilion, Atlanta GA",
        "inspection_date": "2026-03-15",
        "category": "HVAC",
        "status": "passed",
        "overall_severity": "none",
        "description": "Hospital cleanroom positive pressure airflow test, HEPA filter integrity, and central chiller efficiency validation.",
        "findings": [],
        "custom_fields": [
            {"key": "total_air_handlers", "value": 12, "field_type": "number"},
            {"key": "hepa_filter_certification_passed", "value": True, "field_type": "boolean"},
            {"key": "chiller_cop_efficiency", "value": 5.82, "field_type": "number"}
        ],
        "dynamic_attributes": {
            "cleanroom_pressure_differentials_pa": {
                "surgical_suite_1": 28.5,
                "surgical_suite_2": 26.2,
                "isolation_ward_a": -14.8,
                "pharmacy_compounding": 32.0
            }
        },
        "created_at": "2026-03-15T08:15:00Z",
        "updated_at": "2026-03-15T11:45:00Z",
        "is_sample": True
    }
]
