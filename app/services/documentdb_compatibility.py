"""
Amazon DocumentDB Compatibility Analyzer Engine.

Provides deterministic analysis of MongoDB queries and operators against
Amazon DocumentDB supported APIs, version-specific capabilities, and
documented functional differences.

References (Official AWS Amazon DocumentDB Documentation):
- Supported MongoDB APIs, operations, and data types:
  https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html
- Functional differences between Amazon DocumentDB and MongoDB:
  https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html
- Amazon DocumentDB compatibility:
  https://docs.aws.amazon.com/documentdb/latest/devguide/compatibility.html
"""

from typing import Dict, Any, List, Optional, Set, Tuple
from pydantic import BaseModel, Field
from app.config.settings import settings

# -----------------------------------------------------------------------------
# Compatibility Models
# -----------------------------------------------------------------------------

class CompatibilityIssue(BaseModel):
    feature: str = Field(..., description="The query operator, command, or construct analyzed.")
    mongodb_supported: bool = Field(True, description="Whether MongoDB natively supports this feature.")
    documentdb_supported: bool = Field(False, description="Whether the selected DocumentDB version supports this feature.")
    severity: str = Field("error", description="error | warning | info")
    message: str = Field(..., description="Detailed explanation of the compatibility status.")
    alternative_available: bool = Field(False, description="Whether a DocumentDB-compatible alternative syntax is available.")
    suggested_alternative: Optional[Dict[str, Any]] = Field(None, description="Suggested DocumentDB-compatible alternative query.")
    alternative_explanation: Optional[str] = Field(None, description="Why this alternative works in DocumentDB.")
    source: str = Field(
        "AWS DocumentDB compatibility documentation (https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html)",
        description="Official AWS documentation citation."
    )

class BehavioralDifference(BaseModel):
    feature: str
    mongodb_behavior: str
    documentdb_behavior: str
    impact: str
    source: str = "AWS DocumentDB functional differences (https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html)"

class CompatibilityReport(BaseModel):
    status: str = Field(..., description="COMPATIBLE | PARTIALLY_COMPATIBLE | INCOMPATIBLE | BEHAVIOR_DIFFERENCE | UNKNOWN")
    documentdb_version: str = Field("5.0", description="The DocumentDB target version evaluated (3.6, 4.0, 5.0, 8.0).")
    mongodb_supported: bool = True
    documentdb_supported: bool = True
    summary: str = ""
    issues: List[CompatibilityIssue] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    behavioral_differences: List[BehavioralDifference] = Field(default_factory=list)
    alternative_query: Optional[Dict[str, Any]] = None
    alternative_explanation: Optional[str] = None
    alternative_status: Optional[str] = None


# -----------------------------------------------------------------------------
# DocumentDB Version Capabilities Registry
# -----------------------------------------------------------------------------

# Supported read filter operators across DocumentDB versions
# Source: AWS DocumentDB Developer Guide - Supported MongoDB Query Operators
SUPPORTED_QUERY_OPERATORS: Dict[str, Set[str]] = {
    "3.6": {
        # Comparison
        "$eq", "$gt", "$gte", "$in", "$lt", "$lte", "$ne", "$nin",
        # Logical
        "$and", "$not", "$nor", "$or",
        # Element
        "$exists", "$type",
        # Evaluation
        "$mod", "$regex", "$options", "$text",
        # Array
        "$all", "$elemMatch", "$size",
        # Comments
        "$comment"
    },
    "4.0": {
        # All 3.6 operators + 4.0 additions
        "$eq", "$gt", "$gte", "$in", "$lt", "$lte", "$ne", "$nin",
        "$and", "$not", "$nor", "$or",
        "$exists", "$type",
        "$mod", "$regex", "$options", "$text",
        "$all", "$elemMatch", "$size",
        "$comment",
        # Geospatial / Index filters (if supported)
        "$near", "$nearSphere", "$geoWithin", "$geoIntersects", "$maxDistance", "$minDistance"
    },
    "5.0": {
        # All 4.0 operators + 5.0 additions (DocumentDB 5.0 with full geospatial, text, vector index)
        "$eq", "$gt", "$gte", "$in", "$lt", "$lte", "$ne", "$nin",
        "$and", "$not", "$nor", "$or",
        "$exists", "$type",
        "$mod", "$regex", "$options", "$text",
        "$all", "$elemMatch", "$size",
        "$comment",
        "$near", "$nearSphere", "$geoWithin", "$geoIntersects", "$maxDistance", "$minDistance",
        "$expr", "$jsonSchema"  # Supported in 5.0 under specific constraints
    },
    "8.0": {
        # Next-gen DocumentDB target version
        "$eq", "$gt", "$gte", "$in", "$lt", "$lte", "$ne", "$nin",
        "$and", "$not", "$nor", "$or",
        "$exists", "$type",
        "$mod", "$regex", "$options", "$text",
        "$all", "$elemMatch", "$size",
        "$comment",
        "$near", "$nearSphere", "$geoWithin", "$geoIntersects", "$maxDistance", "$minDistance",
        "$expr", "$jsonSchema"
    }
}

# Strictly unsupported query operators across DocumentDB versions
UNSUPPORTED_OPERATORS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "$where": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "The '$where' operator executes arbitrary JavaScript on the database server, which is not supported by Amazon DocumentDB for security and architecture isolation.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html#mongo-apis-unsupported"
    },
    "$accumulator": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Custom JavaScript '$accumulator' is not supported in Amazon DocumentDB aggregation pipelines.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$function": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Custom JavaScript '$function' execution is not supported in Amazon DocumentDB.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$natural": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0"],
        "severity": "error",
        "message": "'$natural' sort order is not supported in Amazon DocumentDB 3.6/4.0 because storage is distributed across a log-structured 6-way storage fleet.",
        "alternative_available": True,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html"
    },
    "$set": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Update mutation operator '$set' is prohibited in read-only query filters.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$unset": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Update mutation operator '$unset' is prohibited in read-only query filters.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$inc": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Update mutation operator '$inc' is prohibited in read-only query filters.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$push": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Update mutation operator '$push' is prohibited in read-only query filters.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    },
    "$pull": {
        "mongodb_supported": True,
        "documentdb_supported": False,
        "versions": ["3.6", "4.0", "5.0", "8.0"],
        "severity": "error",
        "message": "Update mutation operator '$pull' is prohibited in read-only query filters.",
        "alternative_available": False,
        "source": "https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html"
    }
}

# Documented Functional & Behavioral Differences
# Source: https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html
BEHAVIORAL_DIFFERENCES_REGISTRY: List[Dict[str, Any]] = [
    {
        "feature": "$regex",
        "condition": "options_i",
        "mongodb_behavior": "MongoDB uses PCRE regex engine with support for full PCRE lookaheads and Perl syntax.",
        "documentdb_behavior": "DocumentDB uses an optimized regex evaluation engine. Lookahead/lookbehind or obscure PCRE constructs may fail or require standard regex patterns.",
        "impact": "Standard regex patterns (^, $, [a-z], i) behave identically. Avoid zero-width assertions or recursive regex."
    },
    {
        "feature": "Collation",
        "condition": "collation",
        "mongodb_behavior": "MongoDB supports ICU collation strings across all languages.",
        "documentdb_behavior": "DocumentDB supports simple binary collation and standard English/case-insensitive indexing.",
        "impact": "Complex multi-language linguistic sorting should be normalized at the application layer."
    },
    {
        "feature": "Null Bytes in Strings",
        "condition": "null_byte",
        "mongodb_behavior": "MongoDB allows embedded null characters ('\\0') inside BSON string fields.",
        "documentdb_behavior": "DocumentDB does not allow strings containing null bytes ('\\0').",
        "impact": "Ensure strings with binary or null characters are encoded using BSON BinData or Base64."
    }
]


# -----------------------------------------------------------------------------
# DocumentDB Compatibility Analyzer Service
# -----------------------------------------------------------------------------

class DocumentDbCompatibilityAnalyzer:
    """
    Deterministic compatibility analyzer evaluating queries against
    Amazon DocumentDB supported features, version matrix, and structural constraints.
    """

    SUPPORTED_VERSIONS = ["3.6", "4.0", "5.0", "8.0"]

    def __init__(self, default_version: Optional[str] = None):
        self.default_version = default_version or getattr(settings, "DOCUMENTDB_TARGET_VERSION", "5.0")
        if self.default_version not in self.SUPPORTED_VERSIONS:
            self.default_version = "5.0"

    def analyze_query(
        self,
        query: Any,
        target_version: Optional[str] = None
    ) -> CompatibilityReport:
        """
        Performs full deterministic compatibility analysis on a query dictionary.
        Returns a structured CompatibilityReport.
        """
        version = target_version or self.default_version
        
        # Check if version is recognized
        if version not in self.SUPPORTED_VERSIONS:
            return CompatibilityReport(
                status="UNKNOWN",
                documentdb_version=str(version),
                mongodb_supported=True,
                documentdb_supported=False,
                summary=f"Compatibility could not be verified for the selected DocumentDB version '{version}'.",
                warnings=[f"DocumentDB version '{version}' is not in the recognized version matrix {self.SUPPORTED_VERSIONS}."]
            )

        if not isinstance(query, dict):
            return CompatibilityReport(
                status="INCOMPATIBLE",
                documentdb_version=version,
                mongodb_supported=False,
                documentdb_supported=False,
                summary="Invalid query format: Root query must be a dictionary/object.",
                issues=[
                    CompatibilityIssue(
                        feature="root_format",
                        mongodb_supported=False,
                        documentdb_supported=False,
                        severity="error",
                        message="Query root must be a JSON/BSON object dictionary."
                    )
                ]
            )

        issues: List[CompatibilityIssue] = []
        warnings: List[str] = []
        behavioral_diffs: List[BehavioralDifference] = []
        
        supported_ops = SUPPORTED_QUERY_OPERATORS.get(version, SUPPORTED_QUERY_OPERATORS["5.0"])

        # 1. Structural / Compound Pattern Checks (e.g., $elemMatch inside $all)
        self._check_compound_incompatibilities(query, issues, warnings)

        # 2. Recursive Operator & Syntax Inspection
        self._inspect_node_compatibility(query, version, supported_ops, issues, warnings, behavioral_diffs, path="")

        # 3. Determine Overall Status
        has_known_error = any(issue.severity == "error" and not issue.documentdb_supported for issue in issues)
        has_unknown = any(issue.severity == "unknown" or (issue.feature.startswith("$") and issue.feature not in supported_ops and issue.feature not in UNSUPPORTED_OPERATORS_REGISTRY) for issue in issues)
        has_behavior_diff = len(behavioral_diffs) > 0
        has_warnings = len(warnings) > 0 or any(issue.severity == "warning" for issue in issues)

        if has_known_error:
            status = "INCOMPATIBLE"
            docdb_supported = False
            summary = f"Amazon DocumentDB {version} does not support one or more features found in this MongoDB query."
        elif has_unknown:
            status = "UNKNOWN"
            docdb_supported = False
            summary = f"Compatibility could not be verified for the selected DocumentDB version {version}."
        elif has_behavior_diff:
            status = "BEHAVIOR_DIFFERENCE"
            docdb_supported = True
            summary = f"Query is supported by Amazon DocumentDB {version}, but has documented functional or behavioral differences."
        elif has_warnings:
            status = "PARTIALLY_COMPATIBLE"
            docdb_supported = True
            summary = f"Query is supported with minor DocumentDB {version} advisories."
        else:
            status = "COMPATIBLE"
            docdb_supported = True
            summary = f"✓ Verified 100% compatible with Amazon DocumentDB {version}."

        # Extract alternative query if available from the first actionable issue
        alt_query = None
        alt_explanation = None
        alt_status = None

        for issue in issues:
            if issue.alternative_available and issue.suggested_alternative:
                alt_query = issue.suggested_alternative
                alt_explanation = issue.alternative_explanation
                # Validate the alternative
                alt_report = self.analyze_query(alt_query, target_version=version)
                alt_status = alt_report.status
                break

        return CompatibilityReport(
            status=status,
            documentdb_version=version,
            mongodb_supported=True,
            documentdb_supported=docdb_supported,
            summary=summary,
            issues=issues,
            warnings=warnings,
            behavioral_differences=behavioral_diffs,
            alternative_query=alt_query,
            alternative_explanation=alt_explanation,
            alternative_status=alt_status
        )

    def _check_compound_incompatibilities(
        self,
        node: Any,
        issues: List[CompatibilityIssue],
        warnings: List[str]
    ) -> None:
        """
        Detects specific compound incompatibilities such as $elemMatch nested inside $all.
        DocumentDB Reference: $elemMatch cannot be placed inside $all in Amazon DocumentDB.
        """
        if isinstance(node, dict):
            for field, value in node.items():
                if isinstance(value, dict):
                    # Check for { field: { "$all": [ { "$elemMatch": ... }, ... ] } }
                    if "$all" in value:
                        all_val = value["$all"]
                        if isinstance(all_val, list):
                            elem_matches = []
                            other_vals = []
                            for item in all_val:
                                if isinstance(item, dict) and "$elemMatch" in item:
                                    elem_matches.append(item["$elemMatch"])
                                else:
                                    other_vals.append(item)

                            if elem_matches:
                                # Detected $elemMatch inside $all incompatibility
                                # Synthesize DocumentDB-compatible alternative using $and with separate $elemMatch
                                alternative_query = self._build_all_elemmatch_alternative(node, field, elem_matches, other_vals)
                                
                                issues.append(
                                    CompatibilityIssue(
                                        feature="$elemMatch inside $all",
                                        mongodb_supported=True,
                                        documentdb_supported=False,
                                        severity="error",
                                        message=(
                                            "Amazon DocumentDB does not support using '$elemMatch' inside an '$all' expression. "
                                            "While MongoDB allows nesting '$elemMatch' within '$all', DocumentDB requires "
                                            "separate '$elemMatch' conditions joined by an '$and' operator."
                                        ),
                                        alternative_available=True,
                                        suggested_alternative=alternative_query,
                                        alternative_explanation=(
                                            f"Rewrote '{field}' filter from '$all' with nested '$elemMatch' into an '$and' "
                                            f"array containing separate '{field}: {{$elemMatch: ...}}' expressions."
                                        ),
                                        source="AWS DocumentDB Compatibility Guide (https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html)"
                                    )
                                )

                    # Recursive check
                    self._check_compound_incompatibilities(value, issues, warnings)
        elif isinstance(node, list):
            for item in node:
                self._check_compound_incompatibilities(item, issues, warnings)

    def _build_all_elemmatch_alternative(
        self,
        original_parent: Dict[str, Any],
        field: str,
        elem_matches: List[Dict[str, Any]],
        other_vals: List[Any]
    ) -> Dict[str, Any]:
        """
        Rewrites { field: { $all: [{ $elemMatch: { ... } }] } } into { $and: [{ field: { $elemMatch: { ... } } }] }
        """
        and_clauses: List[Dict[str, Any]] = []
        for em in elem_matches:
            and_clauses.append({field: {"$elemMatch": em}})
        if other_vals:
            and_clauses.append({field: {"$all": other_vals}})

        alt = {}
        for k, v in original_parent.items():
            if k == field:
                # Replace with $and or merged
                if "$and" in alt:
                    alt["$and"].extend(and_clauses)
                else:
                    alt["$and"] = and_clauses
            else:
                alt[k] = v
        return alt

    def _inspect_node_compatibility(
        self,
        node: Any,
        version: str,
        supported_ops: Set[str],
        issues: List[CompatibilityIssue],
        warnings: List[str],
        behavioral_diffs: List[BehavioralDifference],
        path: str
    ) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                current_path = f"{path}.{key}" if path else key

                if key.startswith("$"):
                    # Check against unsupported registry first
                    if key in UNSUPPORTED_OPERATORS_REGISTRY:
                        reg_info = UNSUPPORTED_OPERATORS_REGISTRY[key]
                        if version in reg_info["versions"]:
                            issues.append(
                                CompatibilityIssue(
                                    feature=key,
                                    mongodb_supported=reg_info["mongodb_supported"],
                                    documentdb_supported=False,
                                    severity=reg_info["severity"],
                                    message=reg_info["message"],
                                    alternative_available=reg_info["alternative_available"],
                                    source=reg_info["source"]
                                )
                            )
                    elif key not in supported_ops:
                        # Unknown or unverified operator
                        issues.append(
                            CompatibilityIssue(
                                feature=key,
                                mongodb_supported=True,
                                documentdb_supported=False,
                                severity="unknown",
                                message=f"The operator '{key}' is unverified for Amazon DocumentDB {version} read-only filters.",
                                alternative_available=False,
                                source="AWS DocumentDB Supported MongoDB APIs (https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html)"
                            )
                        )
                    else:
                        # Operator is in supported_ops -> check behavioral caveats
                        if key == "$regex":
                            # Check regex behavioral notes
                            behavioral_diffs.append(
                                BehavioralDifference(
                                    feature="$regex (Pattern Matching)",
                                    mongodb_behavior="MongoDB uses PCRE with support for full Perl-compatible lookarounds and zero-width assertions.",
                                    documentdb_behavior="DocumentDB uses an optimized pattern matching engine. Standard patterns are 100% compatible; avoid complex zero-width lookaheads.",
                                    impact="Standard string searches and case-insensitive flags ('i') execute without modification."
                                )
                            )
                        elif key == "$text":
                            warnings.append("The '$text' search operator requires an active Text Index on the DocumentDB collection.")

                # Check string values for null byte edge case
                if isinstance(val, str) and "\x00" in val:
                    issues.append(
                        CompatibilityIssue(
                            feature="Null Byte in String",
                            mongodb_supported=True,
                            documentdb_supported=False,
                            severity="error",
                            message="Amazon DocumentDB does not support null byte ('\\0') characters in BSON strings.",
                            alternative_available=False,
                            source="https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html"
                        )
                    )

                # Recurse
                self._inspect_node_compatibility(val, version, supported_ops, issues, warnings, behavioral_diffs, current_path)

        elif isinstance(node, list):
            for i, item in enumerate(node):
                self._inspect_node_compatibility(item, version, supported_ops, issues, warnings, behavioral_diffs, f"{path}[{i}]")


# Global singleton instance
compatibility_analyzer = DocumentDbCompatibilityAnalyzer()
