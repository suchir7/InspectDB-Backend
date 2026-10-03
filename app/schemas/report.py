from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class Issue(BaseModel):
    issue_id: Optional[str] = None
    title: str
    severity: str = Field(default="medium", description="critical | high | medium | low | info")
    code_reference: Optional[str] = None
    status: str = Field(default="open", description="open | in_progress | resolved")
    notes: Optional[str] = None

class Finding(BaseModel):
    finding_id: Optional[str] = None
    category: str
    severity: str = Field(default="medium", description="critical | high | medium | low | info")
    description: str
    location_details: Optional[str] = None
    issues: List[Issue] = Field(default_factory=list)
    custom_metrics: Optional[Dict[str, Any]] = None

class CustomField(BaseModel):
    key: str
    value: Any
    field_type: str = Field(default="string", description="string | number | boolean | json | date")

class ReportBase(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    inspector_name: str = Field(..., min_length=2, max_length=100)
    location: str = Field(..., min_length=2, max_length=200)
    inspection_date: str = Field(..., description="ISO 8601 Date string YYYY-MM-DD")
    category: str = Field(..., description="Electrical | Fire Safety | Structural | HVAC | Equipment | Environmental | General")
    status: str = Field(default="in_review", description="passed | action_required | in_review | failed | draft")
    overall_severity: str = Field(default="low", description="critical | high | medium | low | none")
    description: Optional[str] = None
    findings: List[Finding] = Field(default_factory=list)
    custom_fields: Optional[List[CustomField]] = Field(default_factory=list)
    dynamic_attributes: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Arbitrary nested document fields demonstrating variable schema")

class ReportCreate(ReportBase):
    pass

class ReportUpdate(BaseModel):
    title: Optional[str] = None
    inspector_name: Optional[str] = None
    location: Optional[str] = None
    inspection_date: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    overall_severity: Optional[str] = None
    description: Optional[str] = None
    findings: Optional[List[Finding]] = None
    custom_fields: Optional[List[CustomField]] = None
    dynamic_attributes: Optional[Dict[str, Any]] = None

class ReportResponse(ReportBase):
    id: str
    user_id: Optional[str] = None
    created_at: str
    updated_at: str
    is_sample: bool = False

class ReportListResponse(BaseModel):
    total: int
    page: int
    limit: int
    reports: List[ReportResponse]

class QueryCondition(BaseModel):
    field: str = Field(..., description="Dot notation nested path e.g. 'findings.severity', 'dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv'")
    operator: str = Field(..., description="equals | not_equals | greater_than | less_than | greater_than_or_equal | less_than_or_equal | contains | in | not_in | exists | starts_with | ends_with | regex | array_size | is_true | is_false | between")
    value: Any = None
    value_type: str = Field(default="string", description="string | number | boolean | array | categorical | date")
    sub_conditions: Optional[List[Dict[str, Any]]] = Field(default=None, description="Optional nested sub-conditions for elemMatch or nested groups")

class QueryRequest(BaseModel):
    match_type: str = Field(default="and", description="and | or | not")
    conditions: List[QueryCondition] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)

class QueryResponse(BaseModel):
    total_matches: int
    execution_time_ms: float
    query_ast: Dict[str, Any]
    mongo_equivalent_query: Dict[str, Any]
    matched_reports: List[ReportResponse]
    explanation: str
    complexity: Optional[str] = "Moderate"
    nested_depth: Optional[int] = 2

class SchemaFieldInfo(BaseModel):
    path: str
    display_name: str
    field_type: str = Field(..., description="string | number | boolean | array | object | categorical | date")
    is_array: bool = False
    is_nested: bool = False
    is_variable_schema: bool = False
    occurrence_count: int
    total_documents: int
    example_value: Optional[Any] = None

class SchemaOverviewResponse(BaseModel):
    total_documents: int
    nested_fields_count: int
    arrays_count: int
    fields: List[SchemaFieldInfo]
    variable_schema_groups: List[str]

class RawQueryRequest(BaseModel):
    query: Dict[str, Any] = Field(default_factory=dict, description="Raw MongoDB filter object e.g. {'findings': {'$elemMatch': {'severity': 'high'}}}")
    limit: int = Field(default=20, ge=1, le=100)

class RawQueryResponse(BaseModel):
    total_matches: int
    execution_time_ms: float
    query: Dict[str, Any]
    matched_reports: List[ReportResponse]
    is_valid: bool = True
    warnings: List[str] = Field(default_factory=list)
    complexity: str = "Simple"
    nested_depth: int = 1

class ExplainQueryRequest(BaseModel):
    query: Dict[str, Any]
    collection: str = "inspection_reports"

class ExplainQueryResponse(BaseModel):
    summary: str
    nested_paths: List[str]
    uses_elem_match: bool
    complexity: str
    explanation: str
    index_recommendations: List[str] = Field(default_factory=list)

class DashboardStats(BaseModel):
    total_reports: int
    high_severity_findings: int
    reports_requiring_attention: int
    completed_inspections: int
    status_distribution: Dict[str, int]
    category_distribution: Dict[str, int]
    severity_distribution: Dict[str, int]
    is_demonstration: bool = True
    storage_mode: str = "Local In-Memory Repository (Phase 1)"
    aws_connected: bool = False
    schema_fields_count: int = 0
    nested_fields_count: int = 0
    array_fields_count: int = 0
    data_source: str = "Local In-Memory Repository"
