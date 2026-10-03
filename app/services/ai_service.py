import json
import logging
import os
import re
from typing import Dict, Any, Optional

from app.config.settings import settings
from app.schemas.ai import GenerateQueryResponse
from app.services.query_validator import query_validator
from app.services.documentdb_compatibility import compatibility_analyzer, CompatibilityReport
from app.services.mongodb_executor import mongo_executor, MongoTestResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert database AI engineer specializing in Amazon DocumentDB and MongoDB document modeling for InspectDB, an enterprise Inspection Report Management System.

Your task is to translate natural language user questions into accurate, performant, and secure MongoDB-compatible query filter objects for the `inspection_reports` collection in Amazon DocumentDB.

### InspectDB Document Schema:
The `inspection_reports` collection stores documents with variable schemas and the following hierarchical structure:
- `id` (string): Report identifier, e.g. "RPT-2026-0891"
- `title` (string): Descriptive title of the inspection
- `inspector_name` (string): Full name or credentials of the lead inspector
- `location` (string): Facility, plant, building, or address, e.g. "Building A", "Substation 4B, Austin TX"
- `inspection_date` (string, ISO format "YYYY-MM-DD"): Date the inspection occurred
- `category` (string): Inspection domain: "Electrical" | "Fire Safety" | "Structural" | "HVAC" | "Equipment" | "Environmental" | "General"
- `status` (string): "passed" | "action_required" | "in_review" | "failed" | "draft"
- `overall_severity` (string): "critical" | "high" | "medium" | "low" | "none"
- `description` (string): Executive summary or inspection observations
- `findings` (array of objects):
  - `finding_id` (string): e.g. "FND-ELEC-01"
  - `category` (string): Finding classification e.g. "Thermal Anomaly", "Means of Egress"
  - `severity` (string): "critical" | "high" | "medium" | "low"
  - `description` (string): Specific finding observation
  - `location_details` (string): Specific area or bay
  - `issues` (array of nested sub-issues):
    - `issue_id` (string): e.g. "ISS-0101"
    - `title` (string): Issue description
    - `severity` (string): "critical" | "high" | "medium" | "low"
    - `code_reference` (string): Regulatory standard (e.g. "NEC 110.14", "NFPA 101", "OSHA 1910")
    - `status` (string): "open" | "in_progress" | "resolved"
    - `notes` (string): Action directive or contractor notes
  - `custom_metrics` (object): Domain diagnostics (e.g. `ambient_temp_c`, `measured_voc_ppm`)
- `custom_fields` (array of objects):
  - `key` (string), `value` (any), `field_type` (string: "string"|"number"|"boolean"|"json")
- `dynamic_attributes` (object): Arbitrary nested telemetry (e.g. `electrical_telemetry`, `chemical_inventory`, `fire_safety_matrix`)

### Query Generation Rules for Amazon DocumentDB:
1. Generate ONLY a valid JSON query filter for `collection.find(filter)`. Do NOT generate update, delete, or aggregation stages.
2. For matching nested array items (e.g. finding severity or issues within findings), use `$elemMatch` appropriately:
   - To match a finding with category "Electrical" and severity "high":
     `{"findings": {"$elemMatch": {"category": {"$regex": "^electrical$", "$options": "i"}, "severity": "high"}}}`
   - To match reports where overall status is failed or action required:
     `{"status": {"$in": ["failed", "action_required"]}}`
3. Case-Insensitive Matching: Use `{"$regex": "text", "$options": "i"}` for loose text matches on string fields like location or title.
4. Date Comparisons: Use ISO format strings with `$gte`, `$lte`, `$gt`, `$lt` on `inspection_date`.
5. Off-Topic Inquiries: If the user asks something completely unrelated to inspection reports or database queries, set `is_off_topic` to true and provide a courteous explanation.
6. Unknown Fields: If the user refers to a field not in the known schema, explain that the field may reside in `custom_fields` or `dynamic_attributes` and provide the relevant query.

### Output JSON Format:
You MUST respond with a single JSON object with this exact structure:
{
  "query": { ... },
  "collection": "inspection_reports",
  "operation": "find",
  "explanation": "Clear, concise explanation of how this query works.",
  "is_off_topic": false
}
"""

class AiQueryService:
    """
    Translates natural language questions into MongoDB-compatible
    queries for Amazon DocumentDB using Google Gemini API and
    validates them against the DocumentDB Compatibility Rules Engine.
    """

    def __init__(self):
        self.model_name = settings.GEMINI_MODEL or "gemini-2.5-flash"
        self._client = None

    def _get_client(self, key: str):
        if self._client is not None:
            return self._client
        from google import genai
        return genai.Client(api_key=key)

    def get_api_keys(self) -> list:
        keys = []
        primary = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        if primary:
            keys.append(primary.strip())
        backup = settings.GEMINI_BACKUP_API_KEY or os.environ.get("GEMINI_BACKUP_API_KEY", "")
        if backup and backup.strip() not in keys:
            keys.append(backup.strip())
        return keys

    def is_configured(self) -> bool:
        return len(self.get_api_keys()) > 0

    async def generate_query(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        target_version: Optional[str] = None,
        execute_local_test: bool = True,
        user_id: Optional[str] = None
    ) -> GenerateQueryResponse:
        version = target_version or getattr(settings, "DOCUMENTDB_TARGET_VERSION", "5.0")
        trimmed_question = question.strip()
        if not trimmed_question:
            return GenerateQueryResponse(
                query=None,
                explanation="Please provide a non-empty question describing the inspection reports you would like to find.",
                is_validated=False,
                error="Empty question provided.",
                api_key_configured=self.is_configured(),
                compatibility=None
            )

        keys = self.get_api_keys()
        if not keys:
            return await self._handle_unconfigured_api(
                trimmed_question,
                target_version=version,
                execute_local_test=execute_local_test,
                user_id=user_id
            )

        last_error = ""
        for key in keys:
            for attempt in range(2):
                try:
                    from google import genai
                    from google.genai import types

                    client = self._get_client(key)
                    
                    prompt = f"User Request: {trimmed_question}\nTarget Database Engine: Amazon DocumentDB {version}"
                    if context:
                        prompt += f"\nAdditional Context: {json.dumps(context)}"

                    response = client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            response_mime_type="application/json",
                            temperature=0.1
                        )
                    )

                    raw_text = response.text or ""
                    return await self._process_ai_response(
                        raw_text,
                        trimmed_question,
                        target_version=version,
                        execute_local_test=execute_local_test,
                        user_id=user_id
                    )

                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Gemini generation attempt {attempt + 1} with key failed: {e}")
                    import time
                    time.sleep(0.5)

        logger.error(f"All Gemini API keys failed: {last_error}")
        return await self._handle_unconfigured_api(
            trimmed_question,
            target_version=version,
            execute_local_test=execute_local_test,
            user_id=user_id
        )

    async def _process_ai_response(
        self,
        raw_text: str,
        question: str,
        target_version: str = "5.0",
        execute_local_test: bool = True,
        user_id: Optional[str] = None
    ) -> GenerateQueryResponse:
        try:
            # Parse JSON
            cleaned_text = raw_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()

            parsed = json.loads(cleaned_text)

            if parsed.get("is_off_topic", False):
                return GenerateQueryResponse(
                    query=None,
                    collection="inspection_reports",
                    operation="find",
                    explanation=parsed.get(
                        "explanation",
                        "I am designed specifically to generate Amazon DocumentDB queries for the inspection reports repository. Please ask an inspection-related question."
                    ),
                    is_validated=False,
                    warnings=["User inquiry was classified as off-topic."],
                    api_key_configured=True,
                    compatibility=None
                )

            query_obj = parsed.get("query", {})
            explanation = parsed.get("explanation", f"Generated query filter for '{question}'.")
            collection = parsed.get("collection", "inspection_reports")
            operation = parsed.get("operation", "find")

            # 1. Run strict AST safety validation
            is_valid_syntax, syntax_errors, syntax_warnings = query_validator.validate_query(query_obj, collection=collection)

            # 2. Run Amazon DocumentDB Compatibility Analyzer
            compat_report = compatibility_analyzer.analyze_query(query_obj, target_version=target_version)

            combined_warnings = list(dict.fromkeys(syntax_warnings + compat_report.warnings))

            # 3. Optional Local MongoDB Execution Test (strictly scoped to user_id)
            mongo_result = None
            if execute_local_test and is_valid_syntax:
                mongo_result = await mongo_executor.execute_test(
                    query=query_obj,
                    operation=operation,
                    collection_name=collection,
                    user_id=user_id
                )

            # If syntax is invalid, reject
            if not is_valid_syntax:
                return GenerateQueryResponse(
                    query=None,
                    collection=collection,
                    operation=operation,
                    explanation=f"The AI proposed a query that did not pass safety validation: {'; '.join(syntax_errors)}",
                    is_validated=False,
                    compatibility=compat_report,
                    mongo_test_result=mongo_result,
                    warnings=combined_warnings,
                    error=f"Validation failed: {'; '.join(syntax_errors)}",
                    api_key_configured=True
                )

            # The query is validated ONLY if syntax is safe AND it is not incompatible with DocumentDB
            is_fully_validated = is_valid_syntax and (compat_report.status in ["COMPATIBLE", "PARTIALLY_COMPATIBLE", "BEHAVIOR_DIFFERENCE"])

            return GenerateQueryResponse(
                query=query_obj,
                collection=collection,
                operation=operation,
                explanation=explanation,
                is_validated=is_fully_validated,
                compatibility=compat_report,
                mongo_test_result=mongo_result,
                warnings=combined_warnings,
                api_key_configured=True
            )

        except Exception as e:
            logger.error(f"Failed to parse AI output: {raw_text} - Error: {e}")
            return GenerateQueryResponse(
                query=None,
                collection="inspection_reports",
                operation="find",
                explanation="The AI model generated an invalid response structure. Please rephrase your question.",
                is_validated=False,
                error=f"Malformed AI response format: {str(e)}",
                api_key_configured=True,
                compatibility=None
            )

    async def _handle_unconfigured_api(
        self,
        question: str,
        target_version: str = "5.0",
        execute_local_test: bool = True,
        user_id: Optional[str] = None
    ) -> GenerateQueryResponse:
        """
        Provides deterministic schema-guided query generation with full compatibility analysis
        and local MongoDB testing when GEMINI_API_KEY is not yet supplied.
        """
        q_lower = question.lower()
        query: Optional[Dict[str, Any]] = None
        explanation: str = ""
        warnings: list = [
            "GEMINI_API_KEY is not configured on the backend server. Displaying deterministic pattern template. To enable live AI generation, set GEMINI_API_KEY in backend/.env."
        ]

        if "enclosure" in q_lower or "switchgear" in q_lower:
            query = {
                "findings.location_details": "Main Switchgear Enclosure SG-02, Bay 3"
            }
            explanation = "Queries nested findings array for subdocuments with location_details matching 'Main Switchgear Enclosure SG-02, Bay 3'."
        elif "all tags" in q_lower or "match all" in q_lower or "$all" in q_lower:
            query = {
                "findings": {
                    "$all": [
                        {"$elemMatch": {"severity": "critical"}},
                        {"$elemMatch": {"remediation_status": "open"}}
                    ]
                }
            }
            explanation = "Attempts to match reports using $elemMatch nested within an $all array operator (MongoDB supported; Amazon DocumentDB incompatible)."
        elif "sarah" in q_lower or "inspector named" in q_lower:
            query = {
                "inspector_name": {
                    "$regex": "sarah",
                    "$options": "i"
                }
            }
            explanation = "Performs a case-insensitive regular expression match for inspector named Sarah using the '$options: i' flag."
        elif "nec" in q_lower or "110.14" in q_lower:
            query = {
                "findings.issues": {
                    "$elemMatch": {
                        "code_reference": "NEC 110.14(D)",
                        "status": "open"
                    }
                }
            }
            explanation = "Queries deeply nested 'findings.issues' array using $elemMatch for open issues under regulatory standard NEC 110.14(D)."
        elif "high" in q_lower and "severity" in q_lower:
            query = {
                "findings": {
                    "$elemMatch": {
                        "severity": {"$in": ["high", "critical"]}
                    }
                }
            }
            explanation = "Finds all inspection reports containing at least one nested finding with high or critical severity using the $elemMatch operator on the 'findings' array."
        elif "building a" in q_lower or "building" in q_lower:
            query = {
                "location": {
                    "$regex": "Building A",
                    "$options": "i"
                }
            }
            explanation = "Performs a case-insensitive regular expression match on the 'location' string field for 'Building A'."
        elif "electrical" in q_lower:
            query = {
                "$or": [
                    {"category": "Electrical"},
                    {"findings": {"$elemMatch": {"category": {"$regex": "electrical", "$options": "i"}}}}
                ]
            }
            explanation = "Finds reports where either the top-level category is 'Electrical' or at least one nested finding belongs to the electrical category."
        elif "fail" in q_lower or "failed" in q_lower:
            query = {
                "status": "failed"
            }
            explanation = "Matches inspection reports where the overall report status equals 'failed'."
        elif "after" in q_lower or "date" in q_lower:
            query = {
                "inspection_date": {
                    "$gte": "2026-03-01"
                }
            }
            explanation = "Compares the ISO 8601 'inspection_date' string using the $gte operator for reports on or after March 1, 2026."
        elif "maintenance" in q_lower or "action" in q_lower:
            query = {
                "status": {
                    "$in": ["action_required", "in_review"]
                }
            }
            explanation = "Finds reports requiring maintenance or follow-up by checking if status is in ['action_required', 'in_review']."
        else:
            query = {
                "category": {
                    "$exists": True
                }
            }
            explanation = f"Template query for: '{question}'. Configure GEMINI_API_KEY in backend/.env for generative responses across all variable schemas."

        is_valid, errors, val_warnings = query_validator.validate_query(query)
        compat_report = compatibility_analyzer.analyze_query(query, target_version=target_version)
        warnings.extend(val_warnings)
        warnings.extend(compat_report.warnings)

        mongo_result = None
        if execute_local_test and is_valid and query:
            mongo_result = await mongo_executor.execute_test(
                query=query,
                operation="find",
                collection_name="inspection_reports",
                user_id=user_id
            )

        is_fully_validated = is_valid and (compat_report.status in ["COMPATIBLE", "PARTIALLY_COMPATIBLE", "BEHAVIOR_DIFFERENCE"])

        return GenerateQueryResponse(
            query=query,
            collection="inspection_reports",
            operation="find",
            explanation=explanation,
            is_validated=is_fully_validated,
            compatibility=compat_report,
            mongo_test_result=mongo_result,
            warnings=list(dict.fromkeys(warnings)),
            api_key_configured=False
        )

    def _handle_generation_fallback(self, question: str, error_detail: str) -> GenerateQueryResponse:
        return GenerateQueryResponse(
            query=None,
            collection="inspection_reports",
            operation="find",
            explanation="Encountered an issue connecting to the Gemini AI API. Please verify your internet connection or GEMINI_API_KEY in backend/.env.",
            is_validated=False,
            error=f"Gemini API connection error: {error_detail}",
            api_key_configured=self.is_configured(),
            compatibility=None
        )

ai_query_service = AiQueryService()
