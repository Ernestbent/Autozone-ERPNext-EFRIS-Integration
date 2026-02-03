import frappe
import requests
import json
import base64
from frappe.model.document import Document
from frappe.utils.data import now
from datetime import datetime, timedelta, timezone

eat_timezone = timezone(timedelta(hours=3))

## SHARED UTILITIES - NO REFERENCE FIELDS

def get_efris_settings(docname):
    """Fetch and validate EFRIS configuration."""
    efris_doc = frappe.get_doc("EFRIS Settings", docname)
    
    if not efris_doc.server_url:
        frappe.throw("Server URL is not configured")
    if not efris_doc.device_number:
        frappe.throw("Device Number is not configured")
    if not efris_doc.tin:
        frappe.throw("TIN is not configured")
    
    return {
        "server_url": efris_doc.server_url,
        "device_number": efris_doc.device_number,
        "tin": efris_doc.tin,
        "brn": efris_doc.get("brn", "")
    }

def log_efris_integration(status, url, headers, data, response, error=""):
    """Log EFRIS integration - NO reference fields."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"
    
    try:
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "is_remote_request": True,
            "integration_request_service": "EFRIS Ping Server T101",
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "error": error,
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT")
            # NO reference_doctype or reference_docname
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

def build_t101_ping_request(settings):
    """Build T101 ping request payload."""
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    return {
        "data": {
            "content": "",
            "signature": "",
            "dataDescription": {
                "codeType": "0",
                "encryptCode": "1",
                "zipCode": "0"
            }
        },
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode": "T101",
            "requestCode": "TP",
            "requestTime": current_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "60-FF-9E-47-D4-04",
            "deviceNo": settings["device_number"],
            "tin": settings["tin"],
            "brn": settings["brn"],
            "taxpayerID": "1",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo": frappe.generate_hash(length=14),
                "operatorName": frappe.session.user
            }
        },
        "returnStateInfo": {
            "returnCode": "",
            "returnMessage": ""
        }
    }

def safe_decode_response(response_json):
    """Safely decode base64 response content."""
    try:
        encoded_string = response_json.get("data", {}).get("content", "")
        if encoded_string:
            decoded_bytes = base64.b64decode(encoded_string)
            decoded_string = decoded_bytes.decode('utf-8')
            return json.loads(decoded_string)
        return {}
    except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {"message": "Could not decode response content"}

def send_efris_ping(server_url, request_data):
    """Send T101 ping request."""
    headers = {"Content-Type": "application/json"}
    response = requests.post(server_url, headers=headers, json=request_data, timeout=30)
    response.raise_for_status()
    return response.json()

## MAIN API ENDPOINT

@frappe.whitelist()
def test_efris_connection(docname):
    """Test EFRIS server connection using T101 ping."""
    url = None
    headers = None
    data = None
    
    try:
        # Validate settings
        settings = get_efris_settings(docname)
        url = settings["server_url"]
        
        # Build and send ping request
        request_data = build_t101_ping_request(settings)
        data = request_data
        headers = {"Content-Type": "application/json"}
        
        response_json = send_efris_ping(url, request_data)
        
        # Safely decode response
        decoded_json = safe_decode_response(response_json)
        
        # Log success
        log_efris_integration('Completed', url, headers, data, response_json)
        
        return {
            "status": "success",
            "message": decoded_json,
            "server_time": decoded_json.get("serverTime", "N/A"),
            "response_code": response_json.get("returnStateInfo", {}).get("returnCode")
        }
        
    except requests.exceptions.Timeout:
        error_msg = "Connection timeout. Please check your network connection."
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {"status": "error", "message": error_msg}
        
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {"status": "error", "message": error_msg}
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        frappe.log_error(error_msg, "EFRIS Connection Test Error")
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {"status": "error", "message": error_msg}

class EFRISSettings(Document):
    pass
