import json
import requests
from datetime import datetime, timedelta, timezone
import frappe
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

eat_timezone = timezone(timedelta(hours=3))


# Fetch Settings Document

def get_efris_settings():
    """Extract and validate EFRIS settings from single doctype."""
    efris_settings = frappe.get_single("EFRIS Settings")

    required = {
        "Device Number": efris_settings.device_number,
        "TIN":           efris_settings.tin,
        "Server URL":    efris_settings.server_url,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        frappe.throw(f"EFRIS Settings incomplete: {', '.join(missing)}")

    return efris_settings


## Handle Logging
def log_integration_request(status, url, headers, data, response, error=""):
    """Log integration requests to Integration Request doctype."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"

    try:
        frappe.get_doc({
            "doctype":                     "Integration Request",
            "integration_type":            "Remote",
            "method":                      "POST",
            "integration_request_service": "Query Verification Code For Credit Note (T108)",
            "is_remote_request":           1,
            "status":                      status,
            "url":                         url,
            "request_headers":             json.dumps(headers, indent=4),
            "data":                        json.dumps(data, indent=4),
            "output":                      json.dumps(response, indent=4),
            "error":                       error,
            "execution_time":              datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT"),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

## Payload Builder
def build_verification_payload(credit_note_number):
    """Build T108 verification code query payload."""
    return {
        "invoiceNo": credit_note_number,
    }


## Request Builder
def build_t108_request(payload, efris_settings):
    """Build complete T108 request structure."""
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")

    return {
        "data": {
            "content":         encrypted_result["encrypted_content"],
            "signature":       encrypted_result["signature"],
            "dataDescription": {
                "codeType":    "0",
                "encryptCode": "1",
                "zipCode":     "0",
            },
        },
        "globalInfo": {
            "appId":          "AP04",
            "version":        "1.1.20191201",
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode":  "T108",
            "requestCode":    "TP",
            "requestTime":    datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S"),
            "responseCode":   "TA",
            "userName":       "admin",
            "deviceMAC":      "B47720524158",
            "deviceNo":       efris_settings.device_number,
            "tin":            efris_settings.tin,
            "brn":            efris_settings.brn or "",
            "taxpayerID":     "999000002030357",
            "longitude":      "32.61665",
            "latitude":       "0.36601",
            "agentType":      "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo":        frappe.generate_hash(length=14),
                "operatorName":       frappe.session.user,
            },
        },
        "returnStateInfo": {"returnCode": "", "returnMessage": ""},
    }

# Handle Http Response
def send_efris_request(server_url, data_to_post, headers):
    """Send request with timeout."""
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        return response.json() if response.text else {}, response.status_code
    except requests.exceptions.Timeout:
        raise Exception("Request timed out after 30s")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API Error: {str(e)}")


## Process response
def process_verification_response(response_data, server_url, headers, data_to_post, status_code):
    """Process and decrypt T108 verification code response."""
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")

    log_integration_request(
        "Completed" if status_code == 200 else "Failed",
        server_url, headers, data_to_post, response_data,
        f"HTTP {status_code}: {return_message}"
    )

    if status_code != 200 or return_message != "SUCCESS":
        frappe.throw(
            f"EFRIS Response: {return_message}. Check Integration Request log.",
            title="EFRIS API Response"
        )

    content = response_data.get("data", {}).get("content")
    if not content:
        frappe.throw("No encrypted content in EFRIS response")

    decrypted_string = decrypt_string(content)
    decoded_data     = json.loads(decrypted_string)

    verification_code = decoded_data.get("basicInformation", {}).get("antifakeCode")
    qr_code_efris     = decoded_data.get("summary", {}).get("qrCode")

    if not verification_code:
        frappe.throw("Verification code missing in EFRIS response")

    return {
        "status":           "success",
        "verification_code": verification_code,
        "qr_code_efris":    qr_code_efris,
    }

## Sales Invoice Save
def save_verification_to_invoice(invoice_name, verification_code, qr_code_efris):
    """Save verification code and QR code back to the Sales Invoice custom fields."""
    try:
        frappe.db.set_value(
            "Sales Invoice",
            invoice_name,
            {
                "custom_verification_code_cn": verification_code,
                "custom_qr_code_credit_note":  qr_code_efris or "",
            },
            update_modified=False
        )
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(str(e), "Verification Code Save Error")
        frappe.throw(f"Failed to save verification code to invoice: {str(e)}")

# Main
@frappe.whitelist()
def query_verification_code_cn(invoice_name, credit_note_number=None): 
    if not credit_note_number:
        frappe.throw("Credit Note Number is required")

    # Get Settings from single efris settings doctype
    efris_settings = get_efris_settings()

    # Build request
    headers      = {"Content-Type": "application/json"}
    payload      = build_verification_payload(credit_note_number)
    data_to_post = build_t108_request(payload, efris_settings)

    # Send and process
    try:
        response_data, status_code = send_efris_request(
            efris_settings.server_url, data_to_post, headers
        )
        result = process_verification_response(
            response_data, efris_settings.server_url, headers, data_to_post, status_code
        )

        # Save to Sales Invoice via db.set_value (bypasses submit restriction)
        save_verification_to_invoice(
            invoice_name,
            result["verification_code"],
            result["qr_code_efris"]
        )

        return result

    except Exception as e:
        error_msg = str(e)
        log_integration_request(
            "Failed", efris_settings.server_url, headers, data_to_post, {}, error_msg
        )
        frappe.throw(error_msg)