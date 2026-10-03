import urllib.request
import json

def test_live_api():
    base_url = "http://127.0.0.1:8000/api"

    print("1. Testing GET /api/reports when empty...")
    req = urllib.request.Request(f"{base_url}/reports")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(f"   Status: {resp.status}, Total: {data['total']}, Reports: {data['reports']}")
        assert data['total'] == 0
        assert data['reports'] == []

    print("\n2. Testing GET /api/stats when empty...")
    req = urllib.request.Request(f"{base_url}/stats")
    with urllib.request.urlopen(req) as resp:
        stats = json.loads(resp.read().decode())
        print(f"   Total reports: {stats['total_reports']}")
        print(f"   High severity findings: {stats['high_severity_findings']}")
        print(f"   Reports requiring attention: {stats['reports_requiring_attention']}")
        print(f"   Schema fields count: {stats['schema_fields_count']}")
        assert stats['total_reports'] == 0
        assert stats['high_severity_findings'] == 0

    print("\n3. Testing POST /api/reports (User creating custom inspection)...")
    payload = {
        "title": "Main Plant Electrical Distribution Test",
        "inspector_name": "Harish",
        "location": "Building A, Bay 4",
        "inspection_date": "2026-10-03",
        "category": "Electrical",
        "status": "in_review",
        "overall_severity": "high",
        "description": "Exposed high voltage wiring on secondary panel.",
        "findings": [
            {
                "finding_id": "FND-001",
                "category": "Electrical Hazards",
                "severity": "high",
                "description": "Exposed wiring behind main busbar.",
                "location_details": "Panel 3B",
                "issues": [
                    {
                        "issue_id": "ISS-001",
                        "title": "Loose terminal connection",
                        "severity": "high",
                        "code_reference": "NEC 110.14",
                        "status": "open",
                        "notes": "Requires immediate torque adjustment."
                    }
                ]
            }
        ],
        "custom_fields": [
            {"key": "operating_voltage", "value": 480, "field_type": "number"}
        ],
        "dynamic_attributes": {
            "telemetry": {
                "phase_a_volts": 480.2
            }
        }
    }
    req = urllib.request.Request(
        f"{base_url}/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        created = json.loads(resp.read().decode())
        report_id = created["id"]
        print(f"   Created Report ID: {report_id}")
        print(f"   Inspector: {created['inspector_name']}")
        print(f"   Title: {created['title']}")
        print(f"   Severity: {created['overall_severity']}")
        assert created["inspector_name"] == "Harish"
        assert created["is_sample"] is False

    print("\n4. Testing GET /api/reports after creation...")
    req = urllib.request.Request(f"{base_url}/reports")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(f"   Total: {data['total']}, Reports count: {len(data['reports'])}")
        assert data['total'] == 1
        assert data['reports'][0]['id'] == report_id

    print("\n5. Testing PUT /api/reports/{id} (Updating report)...")
    update_payload = {"status": "passed", "overall_severity": "none"}
    req = urllib.request.Request(
        f"{base_url}/reports/{report_id}",
        data=json.dumps(update_payload).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT"
    )
    with urllib.request.urlopen(req) as resp:
        updated = json.loads(resp.read().decode())
        print(f"   Updated Status: {updated['status']}, Severity: {updated['overall_severity']}")
        assert updated["status"] == "passed"
        assert updated["overall_severity"] == "none"

    print("\n6. Testing DELETE /api/reports/{id}...")
    req = urllib.request.Request(f"{base_url}/reports/{report_id}", method="DELETE")
    with urllib.request.urlopen(req) as resp:
        del_res = json.loads(resp.read().decode())
        print(f"   Delete response: {del_res}")

    print("\n7. Verifying repository is empty after delete...")
    req = urllib.request.Request(f"{base_url}/reports")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(f"   Total: {data['total']}, Reports: {data['reports']}")
        assert data['total'] == 0
        assert data['reports'] == []

    print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_live_api()
