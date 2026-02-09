import frappe
import requests
import json
import base64
from datetime import datetime, timezone, timedelta

EAT_TIMEZONE = timezone(timedelta(hours=3))
HEADERS = {"Content-Type": "application/json"}

def get_efris_settings():
    """Get EFRIS Settings (Single doctype)"""
    try:
        settings = frappe.get_single("EFRIS Settings")
    except Exception:
        frappe.throw("EFRIS Settings Single DocType not found")
    
    if not settings.is_active:
        frappe.throw("EFRIS integration is disabled")
    
    return {
        "server_url": settings.server_url,
        "device_number": settings.device_number,
        "tin": settings.tin,
        "brn": settings.brn
    }

def encode_payload(payload):
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

def decode_response_content(content):
    decoded = base64.b64decode(content).decode("utf-8")
    return json.loads(decoded)

def log_request(status, url, headers, data, response, error="", service="EFRIS"):
    try:
        frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "method": "POST",
            "integration_request_service": service,
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers),
            "data": json.dumps(data),
            "output": json.dumps(response),
            "error": error,
        }).insert(ignore_permissions=True)
    except:
        pass

@frappe.whitelist()
def query_credit_note(custom_reference_number=None, custom_fdn=None):
    """Query credit note from EFRIS T111"""
    try:
        if not custom_reference_number and not custom_fdn:
            return {"status": "failed", "message": "Reference Number or FDN required"}

        settings = get_efris_settings()
        
        payload = {
            "referenceNo": custom_reference_number or "",
            "oriInvoiceNo": custom_fdn or "",
            "invoiceNo": "",
            "combineKeywords": "",
            "approveStatus": "101,102",
            "queryType": "1",
            "invoiceApplyCategoryCode": "101,103",
            "startDate": "", "endDate": "",
            "pageNo": "1", "pageSize": "10",
            "creditNoteType": "1",
            "branchName": "", "sellerTinOrNin": "", "sellerLegalOrBusinessName": ""
        }

        request_time = datetime.now(EAT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        request_data = {
            "data": {
                "content": encode_payload(payload),
                "signature": "",
                "dataDescription": {"codeType": "0", "encryptionCode": "1", "zipCode": "0"}
            },
            "globalInfo": {
                "appId": "AP04",
                "version": "1.1.20191201",
                "dataExchangeId": frappe.generate_hash(),
                "interfaceCode": "T111",
                "requestCode": "TP",
                "requestTime": request_time,
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "B47720524158",
                "deviceNo": settings["device_number"],
                "tin": settings["tin"],
                "brn": settings["brn"],
                "taxpayerID": "1",
                "longitude": "32.61665",
                "latitude": "0.36601",
                "agentType": "0"
            },
            "returnStateInfo": {}
        }

        response = requests.post(settings["server_url"], json=request_data, headers=HEADERS, timeout=60)
        response_data = response.json()
        msg = response_data.get("returnStateInfo", {}).get("returnMessage", "")

        if response.status_code == 200 and msg == "SUCCESS":
            content = response_data.get("data", {}).get("content", "")
            if content:
                decoded = decode_response_content(content)
                records = decoded.get("records", [])
                log_request("Completed", settings["server_url"], HEADERS, request_data, response_data)
                
                return {
                    "status": "success",
                    "credit_note_no": records[0].get("invoiceNo", "") if records else "",
                    "id": records[0].get("id", "") if records else "",
                    "records_count": len(records)
                }
        
        log_request("Failed", settings["server_url"], HEADERS, request_data, response_data, msg)
        return {"status": "failed", "message": msg or "EFRIS API failed"}

    except Exception as e:
        log_request("Failed", "", HEADERS, {}, {}, str(e))
        return {"status": "failed", "message": str(e)}
