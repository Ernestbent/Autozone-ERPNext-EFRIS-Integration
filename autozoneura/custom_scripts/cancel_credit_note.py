import json
import requests
from datetime import datetime, timedelta, timezone
import frappe
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

eat_timezone = timezone(timedelta(hours=3))

## Fetch Settings from single doctype EFRIS Settings
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

## Log Integration Request
def log_integration_request(status, url, headers, data, response, error=""):
    """Log integration requests to Integration Request doctype."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"

    try:
        frappe.get_doc({
            "doctype":                     "Integration Request",
            "integration_type":            "Remote",
            "method":                      "POST",
            "integration_request_service": "Cancellation Of Credit Note (T114)",
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
def build_cancellation_payload(doc):
    """Build T114 credit note cancellation payload."""
    return {
        "oriInvoiceId":             doc.custom_invoice_number,
        "invoiceNo":                doc.custom_credit_note_number,
        "reason":                   doc.custom_reason or "",
        "reasonCode":               "103",
        "invoiceApplyCategoryCode": "104",
        "attachmentList": [
            {
                "fileName":    "",
                "fileType":    "",
                "fileContent": "",
            }
        ],
    }

## Request Builder
def build_t114_request(payload, efris_settings):
    """Build complete T114 request structure."""
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
            "interfaceCode":  "T114",
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

## Http Helper
def send_efris_request(server_url, data_to_post, headers):
    """Send POST request to EFRIS with 30s timeout."""
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        return response.json() if response.text else {}, response.status_code
    except requests.exceptions.Timeout:
        raise Exception("EFRIS request timed out after 30 seconds.")
    except requests.exceptions.RequestException as e:
        raise Exception(f"EFRIS API connection error: {str(e)}")

## Process response
def process_cancellation_response(response_data, server_url, headers, data_to_post, status_code):
    """
    Process T114 cancellation response.
    On any failure, logs and raises frappe.throw() which
    rolls back the Frappe cancel transaction entirely.
    """
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "Unknown error")

    if status_code == 200 and return_message == "SUCCESS":
        log_integration_request(
            "Completed",
            server_url, headers, data_to_post, response_data,
            f"HTTP {status_code}: {return_message}"
        )
        return {"status": "success"}

    # Any non-success response — log and block the cancel
    log_integration_request(
        "Failed",
        server_url, headers, data_to_post, response_data,
        f"HTTP {status_code}: {return_message}"
    )
    frappe.throw(
        f"EFRIS rejected the cancellation: <b>{return_message}</b><br><br>"
        f"The document has <b>NOT</b> been cancelled. "
        f"Check the Integration Request log for details.",
        title="EFRIS Cancellation Failed"
    )

# Document Event Hook
def on_cancel(doc, event):
    # Only run for credit notes
    if not doc.is_return:
        return

    # Validate required fields 
    if not doc.custom_credit_note_number:
        frappe.throw(
            "Credit Note Number is missing on this document. "
            "Please query Credit Note Details before cancelling.",
            title="Missing Credit Note Number"
        )

    if not doc.custom_invoice_number:
        frappe.throw(
            "Original Invoice Number (custom_invoice_number) is missing on this document.",
            title="Missing Invoice Number"
        )

    # Get Settings 
    efris_settings = get_efris_settings()

    # Build Request 
    headers      = {"Content-Type": "application/json"}
    payload      = build_cancellation_payload(doc)
    data_to_post = build_t114_request(payload, efris_settings)

    #Send to EFRIS 
    try:
        response_data, status_code = send_efris_request(
            efris_settings.server_url, data_to_post, headers
        )
    except Exception as e:
        # Network / timeout error — log and block cancel
        log_integration_request(
            "Failed",
            efris_settings.server_url, headers, data_to_post, {},
            str(e)
        )
        frappe.throw(
            f"Could not reach EFRIS: <b>{str(e)}</b><br><br>"
            f"The document has <b>NOT</b> been cancelled.",
            title="EFRIS Connection Error"
        )

    #Process Response
    process_cancellation_response(
        response_data, efris_settings.server_url, headers, data_to_post, status_code
    )

    #Only reaches here on SUCCESS 
    frappe.msgprint(
        "Credit Note cancelled successfully on EFRIS.",
        indicator="green",
        alert=True
    )