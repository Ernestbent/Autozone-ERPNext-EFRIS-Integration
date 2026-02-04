import json
import base64
from datetime import datetime, timedelta, timezone
import frappe
import requests
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

eat_timezone = timezone(timedelta(hours=3))

## SHARED UTILITIES - KEEP LOGGING, NO REFERENCE FIELDS

def get_efris_settings():
    """Extract and validate EFRIS settings."""
    efris_settings = frappe.get_single("EFRIS Settings")
    
    required = {
        "Device Number": efris_settings.device_number,
        "TIN": efris_settings.tin,
        "Server URL": efris_settings.server_url
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        frappe.throw(f"EFRIS Settings incomplete: {', '.join(missing)}")
    
    return efris_settings

def log_integration_request(status, url, headers, data, response, error=""):
    """Log integration requests - NO reference_doctype/docname."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"
    
    try:
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "method": "POST",
            "integration_request_service": "Customer TIN Validation (T119)",
            "is_remote_request": 1,
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

def build_tin_payload(tax_id):
    """Build T119 TIN query payload."""
    return {"ninBrn": "", "tin": tax_id.strip()}

def build_t119_request(payload, efris_settings):
    """Build complete T119 request structure."""
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")
    
    request_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    data_exchange_id = frappe.generate_hash(length=18)
    reference_no = frappe.generate_hash(length=14)
    
    return {
        "data": {
            "content": encrypted_result["encrypted_content"],
            "signature": encrypted_result["signature"],
            "dataDescription": {
                "codeType": "0",
                "encryptCode": "1",
                "zipCode": "0",
            },
        },
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": data_exchange_id,
            "interfaceCode": "T119",
            "requestCode": "TP",
            "requestTime": request_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": efris_settings.device_number,
            "tin": efris_settings.tin,
            "brn": efris_settings.brn or "",
            "taxpayerID": "999000002030357",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo": reference_no,
                "operatorName": frappe.session.user,
            },
        },
        "returnStateInfo": {"returnCode": "", "returnMessage": ""},
    }

def send_efris_request(server_url, data_to_post, headers):
    """Send request with timeout."""
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        return response.json() if response.text else {}, response.status_code
    except requests.exceptions.Timeout:
        raise Exception("Request timed out after 30s")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API Error: {str(e)}")

def process_tin_response(response_data, server_url, headers, data_to_post, status_code):
    """Process and decrypt successful T119 response."""
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
    
    # Log raw response - NO reference fields
    log_integration_request(
        'Completed' if status_code == 200 else 'Failed',
        server_url, headers, data_to_post, response_data,
        f"HTTP {status_code}: {return_message}"
    )
    
    if status_code != 200 or return_message != "SUCCESS":
        frappe.throw(f"EFRIS Response: {return_message}. Check Integration Request log.", 
                    title="EFRIS API Response")
    
    # Decrypt response content
    content = response_data.get("data", {}).get("content")
    if not content:
        frappe.throw("No encrypted content in URA response")
    
    decrypted_string = decrypt_string(content)
    decoded_data = json.loads(decrypted_string)
    taxpayer = decoded_data.get("taxpayer", {})
    
    return {
        "success": True,
        "business_name": taxpayer.get("legalName", ""),
        "nin_brn": taxpayer.get("ninBrn", ""),
        "taxpayer_type": taxpayer.get("taxpayerType", ""),
        "contact_email": taxpayer.get("contactEmail", ""),
        "contact_number": taxpayer.get("contactNumber", ""),
        "address": taxpayer.get("address", ""),
        "government_tin": taxpayer.get("governmentTIN", taxpayer.get("tin", "")),
        "tax_id": decoded_data.get("tin", ""),
        "legal_name": taxpayer.get("legalName", "")
    }

## Main API Endpoint

@frappe.whitelist()
def query_tax_payer(tax_id, customer_name=""):
    """TIN Validation - EFRIS T119 API with decryption & field population."""
    
    # Validate input
    if not tax_id or len(tax_id.strip()) < 10:
        frappe.throw("Please enter a valid TIN (minimum 10 characters)")
    
    # Get validated settings
    efris_settings = get_efris_settings()
    
    # Build and send request
    headers = {"Content-Type": "application/json"}
    payload = build_tin_payload(tax_id)
    data_to_post = build_t119_request(payload, efris_settings)
    
    try:
        response_data, status_code = send_efris_request(efris_settings.server_url, data_to_post, headers)
        
        # Process successful response - NO reference fields
        result = process_tin_response(
            response_data, efris_settings.server_url, headers, data_to_post, status_code
        )
        
        return result
        
    except Exception as e:
        # Log errors - NO reference fields
        error_msg = str(e)
        log_integration_request(
            'Failed', efris_settings.server_url, headers, data_to_post, {},
            error_msg
        )
        frappe.throw(error_msg)
