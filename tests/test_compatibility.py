import pytest
from app.services.documentdb_compatibility import (
    compatibility_analyzer,
    DocumentDbCompatibilityAnalyzer,
    CompatibilityReport
)
from app.services.ai_service import ai_query_service
from app.schemas.ai import GenerateQueryRequest

def test_1_normal_find_query_compatible():
    query = {"status": "passed", "overall_severity": "low"}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "COMPATIBLE"
    assert report.mongodb_supported is True
    assert report.documentdb_supported is True
    assert len(report.issues) == 0

def test_2_nested_document_query_compatible():
    query = {
        "facility.environment.temperature_c": {"$gte": 25.0},
        "location": "Building A"
    }
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "COMPATIBLE"
    assert report.documentdb_supported is True
    assert len(report.issues) == 0

def test_3_elemmatch_on_nested_array_compatible():
    query = {
        "findings": {
            "$elemMatch": {
                "severity": {"$in": ["critical", "high"]},
                "status": "open"
            }
        }
    }
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "COMPATIBLE"
    assert report.documentdb_supported is True
    assert len(report.issues) == 0

def test_4_unsupported_mongodb_feature_incompatible():
    # $where executes arbitrary JS on MongoDB server, which DocumentDB explicitly forbids
    query = {"$where": "this.findings.length > 5"}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"
    assert report.mongodb_supported is True
    assert report.documentdb_supported is False
    assert any(i.feature == "$where" for i in report.issues)

def test_5_elemmatch_inside_all_incompatible_with_alternative():
    # Real documented AWS DocumentDB incompatibility: $elemMatch cannot be nested inside $all
    query = {
        "tags": {
            "$all": [
                {"$elemMatch": {"tag_name": "safety", "verified": True}}
            ]
        }
    }
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"
    assert report.documentdb_supported is False
    
    # Check issue details
    issue = next(i for i in report.issues if "$elemMatch inside $all" in i.feature)
    assert issue.mongodb_supported is True
    assert issue.documentdb_supported is False
    assert issue.alternative_available is True
    assert issue.suggested_alternative is not None
    
    # Check alternative query
    assert "$and" in report.alternative_query
    # Ensure alternative passes compatibility
    assert report.alternative_status in ["COMPATIBLE", "PARTIALLY_COMPATIBLE", "BEHAVIOR_DIFFERENCE"]

def test_6_known_behavioral_difference():
    # $regex pattern matching with options
    query = {"inspector_name": {"$regex": "^john", "$options": "i"}}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "BEHAVIOR_DIFFERENCE"
    assert report.documentdb_supported is True
    assert len(report.behavioral_differences) > 0
    assert any("$regex" in diff.feature for diff in report.behavioral_differences)

def test_7_unknown_operator():
    query = {"field": {"$customUnsupportedOperator": 123}}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "UNKNOWN"
    assert any("$customUnsupportedOperator" in i.feature for i in report.issues)

def test_8_compatible_alternative_passes_validation():
    # Given an incompatible $all/$elemMatch query
    incompat_query = {
        "findings": {
            "$all": [
                {"$elemMatch": {"severity": "critical"}}
            ]
        }
    }
    report = compatibility_analyzer.analyze_query(incompat_query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"
    assert report.alternative_query is not None
    
    # The generated alternative must be compatible
    alt_report = compatibility_analyzer.analyze_query(report.alternative_query, target_version="5.0")
    assert alt_report.status == "COMPATIBLE"

def test_9_incompatible_alternative_rejection():
    # Test that an invalid alternative is NOT marked compatible
    invalid_alt = {"$where": "this.a == 1"}
    alt_report = compatibility_analyzer.analyze_query(invalid_alt, target_version="5.0")
    assert alt_report.status != "COMPATIBLE"

def test_10_documentdb_version_specific_behavior():
    # Test across 3.6, 4.0, 5.0, 8.0
    analyzer = DocumentDbCompatibilityAnalyzer()
    
    query = {"title": "Fire Audit"}
    rep_36 = analyzer.analyze_query(query, target_version="3.6")
    rep_40 = analyzer.analyze_query(query, target_version="4.0")
    rep_50 = analyzer.analyze_query(query, target_version="5.0")
    rep_80 = analyzer.analyze_query(query, target_version="8.0")
    
    assert rep_36.documentdb_version == "3.6" and rep_36.status == "COMPATIBLE"
    assert rep_40.documentdb_version == "4.0" and rep_40.status == "COMPATIBLE"
    assert rep_50.documentdb_version == "5.0" and rep_50.status == "COMPATIBLE"
    assert rep_80.documentdb_version == "8.0" and rep_80.status == "COMPATIBLE"

    # Test unknown version returns UNKNOWN
    rep_unrec = analyzer.analyze_query(query, target_version="99.9")
    assert rep_unrec.status == "UNKNOWN"

def test_11_read_only_query_remains_read_only():
    # Update mutation operator should be caught
    mutation_query = {"$set": {"status": "resolved"}}
    report = compatibility_analyzer.analyze_query(mutation_query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"

@pytest.mark.asyncio
async def test_12_ai_service_integration_with_compatibility():
    # Test deterministic unconfigured mode produces valid response with compatibility report
    resp = await ai_query_service.generate_query("Find reports with high severity findings", target_version="5.0")
    assert resp.is_validated is True
    assert resp.compatibility is not None
    assert resp.compatibility.status in ["COMPATIBLE", "PARTIALLY_COMPATIBLE", "BEHAVIOR_DIFFERENCE"]
    assert resp.compatibility.documentdb_version == "5.0"
