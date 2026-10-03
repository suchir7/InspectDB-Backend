import requests
import json

BASE_URL = "http://127.0.0.1:8000/api"

def run_tests():
    print("--- 1. Testing Schema Overview Endpoint ---")
    r = requests.get(f"{BASE_URL}/query/schema")
    assert r.status_code == 200, f"Schema endpoint failed: {r.text}"
    schema = r.json()
    print(f"Discovered {len(schema['fields'])} fields across {schema['total_documents']} documents. (Nested: {schema['nested_fields_count']}, Arrays: {schema['arrays_count']})")
    assert schema['total_documents'] > 0
    assert schema['nested_fields_count'] > 0

    print("\n--- 2. Testing Example 1: High Severity Findings ---")
    payload = {
        "conditions": [{"field": "findings.severity", "operator": "equals", "value": "high"}],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs. Query: {json.dumps(res['mongo_equivalent_query'])}")
    assert res['total_matches'] > 0

    print("\n--- 3. Testing Example 2: Finding both high severity AND status open ($elemMatch) ---")
    payload = {
        "conditions": [
            {"field": "findings.severity", "operator": "equals", "value": "high"},
            {"field": "findings.status", "operator": "equals", "value": "open"}
        ],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs. Query: {json.dumps(res['mongo_equivalent_query'])}")
    assert "findings" in res['mongo_equivalent_query'] and "$elemMatch" in res['mongo_equivalent_query']['findings']

    print("\n--- 4. Testing Example 3: Reports containing electrical issue / category ---")
    payload = {
        "conditions": [{"field": "findings.category", "operator": "contains", "value": "Electrical"}],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs. Query: {json.dumps(res['mongo_equivalent_query'])}")

    print("\n--- 5. Testing Example 4: Building / Location Query with Critical Findings ---")
    payload = {
        "conditions": [
            {"field": "location", "operator": "contains", "value": "Austin"},
            {"field": "findings.severity", "operator": "equals", "value": "critical"}
        ],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs. Query: {json.dumps(res['mongo_equivalent_query'])}")

    print("\n--- 6. Testing Example 5: Telemetry Voltage > 230 (Numeric Comparison) ---")
    raw_payload = {
        "query": {"dynamic_attributes.electrical_telemetry.voltage_kv": {"$gt": 10.0}}
    }
    r = requests.post(f"{BASE_URL}/query/raw", json=raw_payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Raw query matched {res['total_matches']} docs in {res['execution_time_ms']}ms.")

    print("\n--- 7. Testing Example 6: Reports containing action required / failed status ---")
    payload = {
        "conditions": [{"field": "status", "operator": "equals", "value": "action_required"}],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs. Complexity: {res.get('complexity')}")

    print("\n--- 8. Testing Example 7: Nested issue status is open/resolved ---")
    payload = {
        "conditions": [{"field": "findings.status", "operator": "equals", "value": "resolved"}],
        "match_type": "all"
    }
    r = requests.post(f"{BASE_URL}/query", json=payload)
    assert r.status_code == 200
    res = r.json()
    print(f"Matched {res['total_matches']} docs with resolved findings.")

    print("\n--- 9. Testing Query Explanation Endpoint ---")
    explain_payload = {
        "query": {"findings": {"$elemMatch": {"severity": "high", "status": "open"}}},
        "collection": "inspection_reports"
    }
    r = requests.post(f"{BASE_URL}/query/explain", json=explain_payload)
    assert r.status_code == 200
    explain_res = r.json()
    print(f"Explanation: {explain_res['explanation'][:100]}... (Complexity: {explain_res['complexity']})")

    print("\n--- 10. Testing Raw Query Mutation Protection (Security) ---")
    unsafe_payload = {
        "query": {"$where": "this.findings.length > 0"}
    }
    r = requests.post(f"{BASE_URL}/query/raw", json=unsafe_payload)
    assert r.status_code == 400
    print("Safely rejected unsafe $where operator!")

    print("\n ALL VERIFICATION EXAMPLES PASSED SUCCESSFULLY! ")

if __name__ == "__main__":
    run_tests()
