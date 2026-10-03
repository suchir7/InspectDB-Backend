import pytest
import json
from unittest.mock import MagicMock, patch

from app.services.ai_service import AiQueryService, SYSTEM_PROMPT
from app.services.query_validator import query_validator, QueryValidator
from app.schemas.ai import GenerateQueryRequest

def test_query_validator_allowed_operators():
    valid_query = {
        "findings": {
            "$elemMatch": {
                "category": "Electrical",
                "severity": {"$in": ["high", "critical"]}
            }
        },
        "status": {"$ne": "passed"},
        "inspection_date": {"$gte": "2026-01-01"}
    }
    is_valid, errors, warnings = query_validator.validate_query(valid_query)
    assert is_valid is True
    assert len(errors) == 0

def test_query_validator_forbidden_operator_where():
    dangerous_query = {
        "$where": "this.findings.length > 2"
    }
    is_valid, errors, warnings = query_validator.validate_query(dangerous_query)
    assert is_valid is False
    assert any("forbidden" in err.lower() or "$where" in err for err in errors)

def test_query_validator_forbidden_operator_set():
    mutation_query = {
        "$set": {"status": "passed"}
    }
    is_valid, errors, warnings = query_validator.validate_query(mutation_query)
    assert is_valid is False
    assert any("forbidden" in err.lower() or "$set" in err for err in errors)

def test_query_validator_non_dict_root():
    is_valid, errors, warnings = query_validator.validate_query(["invalid", "list"])
    assert is_valid is False
    assert any("dictionary" in err.lower() or "format" in err.lower() for err in errors)

def test_query_validator_target_collection_safety():
    is_valid, errors, warnings = query_validator.validate_query({}, collection="users_secret")
    assert is_valid is False
    assert any("inspection_reports" in err for err in errors)

def test_query_validator_max_depth():
    # Construct deep nested query exceeding depth limit of 16
    deep_query: dict = {}
    curr = deep_query
    for i in range(20):
        curr["nested"] = {}
        curr = curr["nested"]
    is_valid, errors, warnings = query_validator.validate_query(deep_query)
    assert is_valid is False
    assert any("depth" in err.lower() for err in errors)

@pytest.mark.asyncio
async def test_ai_service_empty_question():
    service = AiQueryService()
    res = await service.generate_query("   ")
    assert res.query is None
    assert res.is_validated is False
    assert res.error == "Empty question provided."

@pytest.mark.asyncio
async def test_ai_service_unconfigured_key_handling():
    service = AiQueryService()
    with patch.object(service, "get_api_keys", return_value=[]):
        res = await service.generate_query("Find reports with high severity findings")
        assert res.api_key_configured is False
        assert res.query is not None
        assert "findings" in res.query
        assert res.is_validated is True
        assert len(res.warnings) > 0

@pytest.mark.asyncio
async def test_ai_service_mocked_gemini_success():
    service = AiQueryService()
    service.api_key = "mock_api_key_test_123"

    mock_gemini_json = json.dumps({
        "query": {
            "findings": {
                "$elemMatch": {
                    "category": "Electrical",
                    "severity": "high"
                }
            }
        },
        "collection": "inspection_reports",
        "operation": "find",
        "explanation": "Finds reports with high severity electrical findings.",
        "is_off_topic": False
    })

    mock_response = MagicMock()
    mock_response.text = f"```json\n{mock_gemini_json}\n```"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    service._client = mock_client

    res = await service.generate_query("Find reports with high severity electrical findings")
    assert res.is_validated is True
    assert res.query == {"findings": {"$elemMatch": {"category": "Electrical", "severity": "high"}}}
    assert res.collection == "inspection_reports"
    assert "electrical" in res.explanation.lower()

@pytest.mark.asyncio
async def test_ai_service_mocked_gemini_malformed_json():
    service = AiQueryService()
    service.api_key = "mock_api_key_test_123"

    mock_response = MagicMock()
    mock_response.text = "This is not valid JSON output from LLM."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    service._client = mock_client

    res = await service.generate_query("Find some reports")
    assert res.is_validated is False
    assert res.query is None
    assert "malformed" in res.error.lower() or "invalid" in res.explanation.lower()

@pytest.mark.asyncio
async def test_ai_service_mocked_gemini_dangerous_query_rejected():
    service = AiQueryService()
    service.api_key = "mock_api_key_test_123"

    # AI maliciously or accidentally generated $where
    mock_gemini_json = json.dumps({
        "query": {
            "$where": "this.findings.length > 5"
        },
        "collection": "inspection_reports",
        "operation": "find",
        "explanation": "Executes JS to check length.",
        "is_off_topic": False
    })

    mock_response = MagicMock()
    mock_response.text = mock_gemini_json

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    service._client = mock_client

    res = await service.generate_query("Find reports with > 5 findings")
    assert res.is_validated is False
    assert res.query is None
    assert "validation failed" in res.error.lower()

@pytest.mark.asyncio
async def test_ai_service_off_topic_handling():
    service = AiQueryService()
    service.api_key = "mock_api_key_test_123"

    mock_gemini_json = json.dumps({
        "query": None,
        "collection": "inspection_reports",
        "operation": "find",
        "explanation": "I am designed specifically to generate Amazon DocumentDB queries for inspection reports. I cannot assist with general cooking recipes.",
        "is_off_topic": True
    })

    mock_response = MagicMock()
    mock_response.text = mock_gemini_json

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    service._client = mock_client

    res = await service.generate_query("What is the recipe for chocolate chip cookies?")
    assert res.query is None
    assert "cooking" in res.explanation.lower() or "off-topic" in res.warnings[0].lower()
