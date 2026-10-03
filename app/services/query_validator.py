from typing import Dict, Any, List, Tuple, Set

# Amazon DocumentDB verified read-only filter operators
ALLOWED_OPERATORS: Set[str] = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte",
    "$in", "$nin", "$and", "$or", "$nor", "$not",
    "$elemMatch", "$exists", "$regex", "$options",
    "$size", "$type", "$all", "$mod"
}

# Strictly forbidden operators (modifications, arbitrary JS, server-side execution, pipelines)
FORBIDDEN_OPERATORS: Set[str] = {
    "$where", "$accumulator", "$function", "$expr",
    "$set", "$unset", "$push", "$pull", "$inc",
    "$currentDate", "$rename", "$out", "$merge",
    "$lookup", "$facet", "$graphLookup", "$sample"
}

# Known top-level and nested schema properties
KNOWN_ROOT_FIELDS: Set[str] = {
    "id", "reportId", "title", "inspector_name", "inspector",
    "location", "inspection_date", "inspectionDate", "category",
    "status", "overall_severity", "severity", "description",
    "findings", "custom_fields", "customFields", "dynamic_attributes",
    "created_at", "updated_at", "is_sample"
}

KNOWN_FINDING_FIELDS: Set[str] = {
    "finding_id", "category", "severity", "description",
    "location_details", "issues", "custom_metrics"
}

KNOWN_ISSUE_FIELDS: Set[str] = {
    "issue_id", "title", "severity", "code_reference",
    "status", "notes"
}

class QueryValidator:
    """
    Validates AI-generated MongoDB/DocumentDB query filters for security,
    syntax correctness, operator compatibility, and maximum nesting depth.
    """

    MAX_DEPTH: int = 16

    def validate_query(
        self,
        query: Any,
        collection: str = "inspection_reports"
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Validates a query object.
        Returns (is_valid, error_list, warning_list).
        """
        errors: List[str] = []
        warnings: List[str] = []

        if collection != "inspection_reports":
            errors.append(f"Security violation: Query target must be 'inspection_reports', got '{collection}'.")

        if not isinstance(query, dict):
            errors.append("Invalid query format: Query root must be a JSON/BSON object dictionary.")
            return False, errors, warnings

        if len(query) == 0:
            if errors:
                return False, errors, warnings
            warnings.append("Query is empty ({}) and will match all documents in the collection.")
            return True, errors, warnings

        # Deep inspect AST nodes
        self._inspect_node(query, depth=1, errors=errors, warnings=warnings, path="")

        is_valid = len(errors) == 0
        return is_valid, errors, warnings

    def _inspect_node(
        self,
        node: Any,
        depth: int,
        errors: List[str],
        warnings: List[str],
        path: str
    ) -> None:
        if depth > self.MAX_DEPTH:
            errors.append(f"Query exceeds maximum allowed nesting depth of {self.MAX_DEPTH} at path: '{path}'.")
            return

        if isinstance(node, dict):
            for key, val in node.items():
                current_path = f"{path}.{key}" if path else key

                # Check if key is a MongoDB operator
                if key.startswith("$"):
                    if key in FORBIDDEN_OPERATORS:
                        errors.append(f"Security violation: Operator '{key}' is forbidden (risk of mutation or server-side code execution).")
                    elif key not in ALLOWED_OPERATORS:
                        errors.append(f"Unsupported operator '{key}' is not verified for Amazon DocumentDB read-only query filters.")
                else:
                    # Check field name for dangerous characters
                    if "\x00" in key or key.startswith("system."):
                        errors.append(f"Security violation: Illegal field name identifier '{key}'.")
                    
                    # Schema advisory warning for unrecognized root fields
                    if "." not in key and not key.startswith("$") and depth == 1:
                        if key not in KNOWN_ROOT_FIELDS:
                            warnings.append(f"Field '{key}' is not in the standard inspection report schema (may be a custom or dynamic attribute).")

                # Recurse into child
                self._inspect_node(val, depth + 1, errors, warnings, current_path)

        elif isinstance(node, list):
            for idx, item in enumerate(node):
                current_path = f"{path}[{idx}]"
                self._inspect_node(item, depth + 1, errors, warnings, current_path)

        elif isinstance(node, str):
            # Check for suspicious script injection strings
            lowered = node.lower()
            if "function(" in lowered or "javascript:" in lowered or "sleep(" in lowered:
                errors.append(f"Security violation: Suspicious script pattern detected in value at '{path}'.")

query_validator = QueryValidator()
