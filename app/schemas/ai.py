from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from app.services.documentdb_compatibility import (
    CompatibilityReport,
    CompatibilityIssue,
    BehavioralDifference
)
from app.services.mongodb_executor import MongoTestResult

class GenerateQueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description="Natural language question describing desired inspection reports to retrieve."
    )
    target_version: Optional[str] = Field(
        default="5.0",
        description="Target Amazon DocumentDB version to check compatibility against ('3.6', '4.0', '5.0', '8.0')."
    )
    execute_local_test: bool = Field(
        default=True,
        description="Whether to safely execute the generated read-only query against the local MongoDB instance."
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional client context or domain hints."
    )

class CheckCompatibilityRequest(BaseModel):
    query: Dict[str, Any] = Field(..., description="MongoDB query filter dictionary to evaluate.")
    target_version: Optional[str] = Field(default="5.0", description="Target DocumentDB version (3.6, 4.0, 5.0, 8.0).")

class TestLocalQueryRequest(BaseModel):
    query: Dict[str, Any] = Field(..., description="MongoDB query filter dictionary to test locally.")
    operation: str = Field(default="find", description="Read-only operation: 'find', 'countDocuments', 'distinct', or 'aggregate'.")
    collection: str = Field(default="inspection_reports", description="Target collection name.")
    target_version: Optional[str] = Field(default="5.0", description="Target DocumentDB version for simultaneous compatibility analysis.")

class QueryHistoryItem(BaseModel):
    id: str = Field(..., description="Unique ID for history entry.")
    timestamp: str = Field(..., description="ISO 8601 timestamp when query was executed.")
    user_request: str = Field(..., description="Original natural-language question or prompt.")
    generated_query: Dict[str, Any] = Field(..., description="Generated or tested MongoDB query filter.")
    operation: str = Field(default="find", description="Executed operation.")
    mongo_execution_status: str = Field(..., description="'success', 'unavailable', 'rejected', or 'error'.")
    mongo_execution_time_ms: float = Field(default=0.0, description="Execution time in milliseconds.")
    documents_matched: int = Field(default=0, description="Documents matched in local MongoDB.")
    documentdb_compatibility_status: str = Field(..., description="'COMPATIBLE', 'PARTIALLY_COMPATIBLE', 'INCOMPATIBLE', 'BEHAVIOR_DIFFERENCE', or 'UNKNOWN'.")
    detected_issues: List[str] = Field(default_factory=list, description="Titles of detected compatibility issues.")
    has_alternative: bool = Field(default=False, description="Whether a validated DocumentDB alternative was generated.")

class GenerateQueryResponse(BaseModel):
    query: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Generated MongoDB-compatible query filter object for Amazon DocumentDB."
    )
    collection: str = Field(
        default="inspection_reports",
        description="Target DocumentDB collection name."
    )
    operation: str = Field(
        default="find",
        description="Read-only query operation (e.g., 'find' or 'countDocuments')."
    )
    explanation: str = Field(
        ...,
        description="Plain-English explanation of how the generated query filters documents."
    )
    is_validated: bool = Field(
        default=False,
        description="Indicates whether the query has passed strict safety, AST validation, and DocumentDB compatibility."
    )
    mongo_test_result: Optional[MongoTestResult] = Field(
        default=None,
        description="Result of executing the read-only query against local MongoDB test database."
    )
    compatibility: Optional[CompatibilityReport] = Field(
        default=None,
        description="Comprehensive Amazon DocumentDB compatibility analysis report."
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="List of validation warnings or schema advisories."
    )
    error: Optional[str] = Field(
        default=None,
        description="Error detail if generation failed or API key is missing."
    )
    api_key_configured: bool = Field(
        default=True,
        description="Indicates if GEMINI_API_KEY is configured on the backend server."
    )

class AiServiceStatus(BaseModel):
    status: str
    gemini_configured: bool
    model: str
    supported_operations: List[str]
    target_collection: str
    target_engine: str
    documentdb_target_version: str = "5.0"
    supported_documentdb_versions: List[str] = Field(default_factory=lambda: ["3.6", "4.0", "5.0", "8.0"])
    local_mongodb_available: bool = False
    storage_mode: str = "memory"
    max_query_results: int = 20
