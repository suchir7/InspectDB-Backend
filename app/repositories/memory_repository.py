import copy
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set

from app.repositories.base import BaseInspectionRepository
from app.schemas.report import SchemaFieldInfo, SchemaOverviewResponse

class InMemoryInspectionRepository(BaseInspectionRepository):
    """
    In-Memory repository implementing the BaseInspectionRepository interface.
    Provides full CRUD, dynamic schema discovery, and a MongoDB-compatible nested document
    query engine with full $elemMatch and dot-notation semantics.
    Starts with 0 reports (empty repository) in accordance with data integrity principles.
    """

    def __init__(self):
        self._reports: Dict[str, Dict[str, Any]] = {}
        # Monotonic so IDs are never reused after a delete
        self._last_seq = 0

    async def get_all(
        self,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        sort_by: str = "inspection_date",
        sort_order: str = "desc",
        page: int = 1,
        limit: int = 10
    ) -> Tuple[List[Dict[str, Any]], int]:
        results = list(self._reports.values())

        if user_id is not None:
            results = [r for r in results if r.get("user_id") == user_id]

        if search:
            query = search.strip().lower()
            results = [
                r for r in results
                if query in r.get("id", "").lower()
                or query in r.get("title", "").lower()
                or query in r.get("inspector_name", "").lower()
                or query in r.get("location", "").lower()
                or query in r.get("description", "").lower()
            ]

        if category and category.lower() != "all":
            results = [r for r in results if r.get("category", "").lower() == category.lower()]

        if status and status.lower() != "all":
            results = [r for r in results if r.get("status", "").lower() == status.lower()]

        if severity and severity.lower() != "all":
            results = [r for r in results if r.get("overall_severity", "").lower() == severity.lower()]

        reverse = (sort_order.lower() == "desc")
        results.sort(
            key=lambda x: str(x.get(sort_by, "")),
            reverse=reverse
        )

        total = len(results)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated = results[start_idx:end_idx]

        return [copy.deepcopy(r) for r in paginated], total

    async def get_by_id(self, report_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        report = self._reports.get(report_id)
        if report:
            if user_id is not None and report.get("user_id") != user_id:
                return None
            return copy.deepcopy(report)
        return None

    async def create(self, report_data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        report = copy.deepcopy(report_data)
        if user_id is not None:
            report["user_id"] = user_id

        if "id" not in report or not report["id"]:
            self._last_seq += 1
            report["id"] = f"RPT-2026-{self._last_seq:04d}"
        
        now = datetime.utcnow().isoformat() + "Z"
        report["created_at"] = now
        report["updated_at"] = now
        report["is_sample"] = False
        
        self._reports[report["id"]] = report
        return copy.deepcopy(report)

    async def update(self, report_id: str, update_data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if report_id not in self._reports:
            return None
        
        current = self._reports[report_id]
        if user_id is not None and current.get("user_id") != user_id:
            return None

        for key, value in update_data.items():
            if value is not None and key not in ["id", "user_id", "created_at"]:
                current[key] = copy.deepcopy(value)
        
        current["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return copy.deepcopy(current)

    async def delete(self, report_id: str, user_id: Optional[str] = None) -> bool:
        if report_id in self._reports:
            if user_id is not None and self._reports[report_id].get("user_id") != user_id:
                return False
            del self._reports[report_id]
            return True
        return False

    # ---------------------------------------------------------
    # DYNAMIC SCHEMA DISCOVERY
    # ---------------------------------------------------------

    def get_schema_overview(self, user_id: Optional[str] = None) -> SchemaOverviewResponse:
        """
        Scans documents in the repository owned by user_id and discovers all unique nested paths,
        types, array indicators, and variable-schema distribution metrics.
        """
        reports = [r for r in self._reports.values() if user_id is None or r.get("user_id") == user_id]
        total_docs = len(reports)
        
        # path -> dict(types=set(), count=int, is_array=bool, is_nested=bool, example=val)
        discovered: Dict[str, Dict[str, Any]] = {}

        def _traverse(obj: Any, current_path: str, is_inside_array: bool = False):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    sub_path = f"{current_path}.{k}" if current_path else k
                    is_arr = isinstance(v, list)
                    is_nest = isinstance(v, dict) or is_arr
                    
                    if sub_path not in discovered:
                        discovered[sub_path] = {
                            "types": set(),
                            "count": 0,
                            "is_array": is_arr,
                            "is_nested": is_nest or is_inside_array,
                            "example": v if not is_nest else None
                        }
                    
                    t_str = self._infer_val_type(v)
                    discovered[sub_path]["types"].add(t_str)
                    discovered[sub_path]["count"] += 1
                    if not is_nest and discovered[sub_path]["example"] is None:
                        discovered[sub_path]["example"] = v

                    if isinstance(v, dict):
                        _traverse(v, sub_path, is_inside_array)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                _traverse(item, sub_path, is_inside_array=True)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        _traverse(item, current_path, is_inside_array=True)

        for r in reports:
            # Clean copy to avoid mutating during schema inspection
            _traverse(r, "")

        fields: List[SchemaFieldInfo] = []
        nested_count = 0
        array_count = 0
        var_groups = set()

        # Build clean SchemaFieldInfo items
        for path, info in sorted(discovered.items()):
            is_arr = info["is_array"] or "[]" in path
            is_nest = info["is_nested"] or "." in path
            is_var = info["count"] < total_docs

            if is_nest:
                nested_count += 1
            if is_arr:
                array_count += 1

            if path.startswith("dynamic_attributes."):
                parts = path.split(".")
                if len(parts) > 1:
                    var_groups.add(parts[1])

            inferred_type = next(iter(info["types"])) if info["types"] else "string"
            # Categorical checks
            if path in ["status", "overall_severity", "category", "findings.severity", "findings.category", "findings.issues.status", "findings.issues.severity"]:
                inferred_type = "categorical"

            display = path
            if is_arr and not display.endswith("[]"):
                display += "[]"

            fields.append(SchemaFieldInfo(
                path=path,
                display_name=display,
                field_type=inferred_type,
                is_array=is_arr,
                is_nested=is_nest,
                is_variable_schema=is_var,
                occurrence_count=info["count"],
                total_documents=total_docs,
                example_value=info["example"] if not isinstance(info["example"], (dict, list)) else None
            ))

        return SchemaOverviewResponse(
            total_documents=total_docs,
            nested_fields_count=nested_count,
            arrays_count=array_count,
            fields=fields,
            variable_schema_groups=sorted(list(var_groups))
        )

    def _infer_val_type(self, val: Any) -> str:
        if isinstance(val, bool):
            return "boolean"
        elif isinstance(val, (int, float)):
            return "number"
        elif isinstance(val, list):
            return "array"
        elif isinstance(val, dict):
            return "object"
        elif isinstance(val, str):
            if re.match(r"^\d{4}-\d{2}-\d{2}", val):
                return "date"
            return "string"
        return "string"

    # ---------------------------------------------------------
    # MONGODB-COMPATIBLE FILTER EVALUATION ENGINE
    # ---------------------------------------------------------

    def _extract_nested_values(self, doc: Any, path_parts: List[str]) -> List[Any]:
        """
        Recursively extracts all values matching a dot notation path in nested dicts/lists.
        Handles array unwinding identical to MongoDB/DocumentDB.
        """
        if not path_parts:
            return [doc]
        
        current_part = path_parts[0]
        remaining = path_parts[1:]
        
        extracted = []
        
        if isinstance(doc, dict):
            if current_part in doc:
                extracted.extend(self._extract_nested_values(doc[current_part], remaining))
        elif isinstance(doc, list):
            for item in doc:
                if isinstance(item, dict) and current_part in item:
                    extracted.extend(self._extract_nested_values(item[current_part], remaining))
                elif isinstance(item, list):
                    extracted.extend(self._extract_nested_values(item, path_parts))
        
        return extracted

    def evaluate_mongo_filter(self, doc: Dict[str, Any], query_filter: Dict[str, Any]) -> bool:
        """
        Evaluates a MongoDB-compatible JSON filter dictionary against a document,
        supporting $and, $or, $nor, $not, $elemMatch, dot notation, and comparison operators.
        """
        if not query_filter:
            return True

        for key, condition in query_filter.items():
            if key == "$and":
                if not isinstance(condition, list) or not all(self.evaluate_mongo_filter(doc, sub) for sub in condition):
                    return False
            elif key == "$or":
                if not isinstance(condition, list) or not any(self.evaluate_mongo_filter(doc, sub) for sub in condition):
                    return False
            elif key == "$nor":
                if not isinstance(condition, list) or any(self.evaluate_mongo_filter(doc, sub) for sub in condition):
                    return False
            elif key == "$not":
                if isinstance(condition, dict) and self.evaluate_mongo_filter(doc, condition):
                    return False
            else:
                # Field key (may contain dot notation e.g. 'findings.severity' or 'findings')
                path_parts = key.split(".")
                
                # Check for $elemMatch on array field
                if isinstance(condition, dict) and "$elemMatch" in condition:
                    elem_match_criteria = condition["$elemMatch"]
                    # Extract target array from document
                    array_vals = self._extract_nested_values(doc, path_parts)
                    matched_elem = False
                    for candidate in array_vals:
                        if isinstance(candidate, list):
                            for sub_item in candidate:
                                if isinstance(sub_item, dict) and self.evaluate_mongo_filter(sub_item, elem_match_criteria):
                                    matched_elem = True
                                    break
                        elif isinstance(candidate, dict) and self.evaluate_mongo_filter(candidate, elem_match_criteria):
                            matched_elem = True
                            break
                    if not matched_elem:
                        return False
                else:
                    # Regular field match or comparison operator
                    extracted_values = self._extract_nested_values(doc, path_parts)
                    if not self._evaluate_field_condition(extracted_values, condition):
                        return False

        return True

    def _evaluate_field_condition(self, extracted_values: List[Any], condition: Any) -> bool:
        if isinstance(condition, dict):
            # Evaluate all operator conditions
            for op, target_val in condition.items():
                if op == "$eq":
                    if not any(self._compare_single(v, "equals", target_val) for v in extracted_values):
                        return False
                elif op == "$ne":
                    if any(self._compare_single(v, "equals", target_val) for v in extracted_values):
                        return False
                elif op == "$gt":
                    if not any(self._compare_single(v, "greater_than", target_val) for v in extracted_values):
                        return False
                elif op == "$gte":
                    if not any(self._compare_single(v, "greater_than_or_equal", target_val) for v in extracted_values):
                        return False
                elif op == "$lt":
                    if not any(self._compare_single(v, "less_than", target_val) for v in extracted_values):
                        return False
                elif op == "$lte":
                    if not any(self._compare_single(v, "less_than_or_equal", target_val) for v in extracted_values):
                        return False
                elif op == "$in":
                    if not isinstance(target_val, list):
                        target_val = [target_val]
                    if not any(self._compare_single(v, "in", target_val) for v in extracted_values):
                        return False
                elif op == "$nin":
                    if not isinstance(target_val, list):
                        target_val = [target_val]
                    if any(self._compare_single(v, "in", target_val) for v in extracted_values):
                        return False
                elif op == "$exists":
                    exists = len(extracted_values) > 0
                    if exists != bool(target_val):
                        return False
                elif op == "$regex":
                    regex_pat = str(target_val)
                    options = condition.get("$options", "i")
                    flags = re.IGNORECASE if "i" in options else 0
                    try:
                        compiled = re.compile(regex_pat, flags)
                        if not any(bool(compiled.search(str(v))) for v in extracted_values):
                            return False
                    except re.error:
                        return False
                elif op == "$size":
                    if not any(isinstance(v, list) and len(v) == int(target_val) for v in extracted_values):
                        return False
            return True
        else:
            # Direct value match (implicit $eq)
            return any(self._compare_single(v, "equals", condition) for v in extracted_values)

    def _compare_single(self, val: Any, operator: str, target: Any) -> bool:
        try:
            if operator == "equals":
                if isinstance(val, (int, float)) and isinstance(target, (int, float, str)):
                    return float(val) == float(target)
                if isinstance(val, bool) and isinstance(target, bool):
                    return val == target
                return str(val).strip().lower() == str(target).strip().lower()
            elif operator == "greater_than":
                return float(val) > float(target)
            elif operator == "less_than":
                return float(val) < float(target)
            elif operator == "greater_than_or_equal":
                return float(val) >= float(target)
            elif operator == "less_than_or_equal":
                return float(val) <= float(target)
            elif operator == "contains":
                return str(target).lower() in str(val).lower()
            elif operator == "starts_with":
                return str(val).lower().startswith(str(target).lower())
            elif operator == "ends_with":
                return str(val).lower().endswith(str(target).lower())
            elif operator == "in":
                if isinstance(target, list):
                    return any(self._compare_single(val, "equals", t) for t in target)
                return str(val).lower() in str(target).lower()
        except (ValueError, TypeError):
            return False
        return False

    # ---------------------------------------------------------
    # STRUCTURED & RAW QUERY EXECUTION
    # ---------------------------------------------------------

    async def query_nested(
        self,
        conditions: List[Dict[str, Any]],
        match_type: str = "and",
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], float]:
        start_time = time.perf_counter()
        
        mongo_query = self._build_mongo_query_from_conditions(conditions, match_type)
        query_ast = {
            "match_type": match_type,
            "conditions": conditions,
            "generated_mongo": mongo_query
        }

        matched = []
        reports = [r for r in self._reports.values() if user_id is None or r.get("user_id") == user_id]
        for report in reports:
            if self.evaluate_mongo_filter(report, mongo_query):
                matched.append(report)

        execution_time_ms = round((time.perf_counter() - start_time) * 1000, 3)
        return [copy.deepcopy(r) for r in matched[:limit]], query_ast, mongo_query, execution_time_ms

    async def query_raw(
        self,
        raw_filter: Dict[str, Any],
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], float]:
        start_time = time.perf_counter()
        matched = []
        reports = [r for r in self._reports.values() if user_id is None or r.get("user_id") == user_id]
        for report in reports:
            if self.evaluate_mongo_filter(report, raw_filter):
                matched.append(report)
        execution_time_ms = round((time.perf_counter() - start_time) * 1000, 3)
        return [copy.deepcopy(r) for r in matched[:limit]], execution_time_ms

    def _build_mongo_query_from_conditions(self, conditions: List[Dict[str, Any]], match_type: str) -> Dict[str, Any]:
        """
        Translates visual conditions into optimized MongoDB queries, automatically
        grouping multi-conditions targeting the same array under $elemMatch.
        """
        if not conditions:
            return {}

        # Check for array prefixes to group into $elemMatch if match_type is 'and'
        array_prefixes = ["findings", "findings.issues", "custom_fields"]
        grouped_by_prefix: Dict[str, List[Dict[str, Any]]] = {}
        non_grouped_conditions: List[Dict[str, Any]] = []

        is_and_match = match_type.lower() in ["and", "all"]
        if is_and_match:
            for cond in conditions:
                field = cond.get("field", "")
                placed = False
                for prefix in array_prefixes:
                    if field.startswith(f"{prefix}."):
                        sub_field = field[len(prefix) + 1:]
                        if prefix not in grouped_by_prefix:
                            grouped_by_prefix[prefix] = []
                        grouped_by_prefix[prefix].append({**cond, "field": sub_field})
                        placed = True
                        break
                if not placed:
                    non_grouped_conditions.append(cond)
        else:
            non_grouped_conditions = list(conditions)

        mongo_parts: List[Dict[str, Any]] = []

        # Build $elemMatch for grouped array conditions
        for prefix, sub_conds in grouped_by_prefix.items():
            if len(sub_conds) > 1:
                # Multiple conditions on the same array -> $elemMatch
                elem_match_dict = {}
                for c in sub_conds:
                    field_filter = self._condition_to_mongo(c)
                    elem_match_dict.update(field_filter)
                mongo_parts.append({prefix: {"$elemMatch": elem_match_dict}})
            else:
                # Single condition
                orig_field = f"{prefix}.{sub_conds[0]['field']}"
                mongo_parts.append(self._condition_to_mongo({**sub_conds[0], "field": orig_field}))

        # Non-grouped conditions
        for c in non_grouped_conditions:
            mongo_parts.append(self._condition_to_mongo(c))

        if not mongo_parts:
            return {}

        if match_type.lower() in ["or", "any"]:
            return {"$or": mongo_parts}
        elif match_type.lower() in ["not", "none"]:
            return {"$nor": mongo_parts}
        else:
            return {"$and": mongo_parts} if len(mongo_parts) > 1 else mongo_parts[0]

    def _condition_to_mongo(self, cond: Dict[str, Any]) -> Dict[str, Any]:
        field = cond.get("field", "")
        op = cond.get("operator", "equals")
        val = cond.get("value")
        val_type = cond.get("value_type", "string")

        # Cast value appropriately
        if val_type == "number" and val is not None:
            try:
                val = float(val) if "." in str(val) else int(val)
            except ValueError:
                pass
        elif val_type == "boolean" and val is not None:
            val = str(val).lower() in ["true", "1", "yes"]

        if op == "equals":
            return {field: val}
        elif op == "not_equals":
            return {field: {"$ne": val}}
        elif op == "greater_than":
            return {field: {"$gt": val}}
        elif op == "greater_than_or_equal":
            return {field: {"$gte": val}}
        elif op == "less_than":
            return {field: {"$lt": val}}
        elif op == "less_than_or_equal":
            return {field: {"$lte": val}}
        elif op in ["contains", "starts_with", "ends_with"]:
            regex_str = str(val)
            if op == "starts_with":
                regex_str = f"^{re.escape(str(val))}"
            elif op == "ends_with":
                regex_str = f"{re.escape(str(val))}$"
            return {field: {"$regex": regex_str, "$options": "i"}}
        elif op == "regex":
            return {field: {"$regex": str(val), "$options": "i"}}
        elif op == "in":
            items = val if isinstance(val, list) else [v.strip() for v in str(val).split(",") if v.strip()]
            return {field: {"$in": items}}
        elif op == "not_in":
            items = val if isinstance(val, list) else [v.strip() for v in str(val).split(",") if v.strip()]
            return {field: {"$nin": items}}
        elif op == "exists":
            return {field: {"$exists": bool(val)}}
        elif op == "is_true":
            return {field: True}
        elif op == "is_false":
            return {field: False}
        elif op == "array_size":
            try:
                return {field: {"$size": int(val)}}
            except (ValueError, TypeError):
                return {field: {"$size": 1}}

        return {field: val}

    async def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        reports = [r for r in self._reports.values() if user_id is None or r.get("user_id") == user_id]
        total = len(reports)
        
        high_severity_findings = 0
        status_dist: Dict[str, int] = {}
        category_dist: Dict[str, int] = {}
        severity_dist: Dict[str, int] = {}
        requiring_attention = 0
        completed = 0

        for r in reports:
            st = r.get("status", "draft")
            status_dist[st] = status_dist.get(st, 0) + 1
            if st in ["action_required", "failed", "in_review"]:
                requiring_attention += 1
            if st == "passed":
                completed += 1

            cat = r.get("category", "General")
            category_dist[cat] = category_dist.get(cat, 0) + 1

            sev = r.get("overall_severity", "low")
            severity_dist[sev] = severity_dist.get(sev, 0) + 1

            for f in r.get("findings", []):
                if f.get("severity") in ["high", "critical"]:
                    high_severity_findings += 1

        schema_overview = self.get_schema_overview(user_id=user_id)

        return {
            "total_reports": total,
            "high_severity_findings": high_severity_findings,
            "reports_requiring_attention": requiring_attention,
            "completed_inspections": completed,
            "status_distribution": status_dist,
            "category_distribution": category_dist,
            "severity_distribution": severity_dist,
            "is_demonstration": True,
            "storage_mode": "Local In-Memory Repository",
            "aws_connected": False,
            "schema_fields_count": len(schema_overview.fields),
            "nested_fields_count": schema_overview.nested_fields_count,
            "array_fields_count": schema_overview.arrays_count,
            "data_source": "Local In-Memory Repository"
        }
