import json
import requests
from datetime import datetime, timedelta, timezone
import frappe
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

eat_timezone = timezone(timedelta(hours=3))

## Settings
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

## Logging
def log_integration_request(status, url, headers, data, response, error=""):
    """Log integration requests to Integration Request doctype."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"

    try:
        frappe.get_doc({
            "doctype":                     "Integration Request",
            "integration_type":            "Remote",
            "method":                      "POST",
            "integration_request_service": "Query Credit Note Number (T111)",
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
def build_credit_note_payload(reference_number, fdn):
    """Build T111 credit note query payload."""
    return {
        "referenceNo":               reference_number,
        "oriInvoiceNo":              fdn,
        "invoiceNo":                 "",
        "combineKeywords":           "",
        "approveStatus":             "101,102",
        "queryType":                 "1",
        "invoiceApplyCategoryCode":  "101,103",
        "startDate":                 "",
        "endDate":                   "",
        "pageNo":                    "1",
        "pageSize":                  "10",
        "creditNoteType":            "1",
        "branchName":                "",
        "sellerTinOrNin":            "",
        "sellerLegalOrBusinessName": "",
    }

## Request Builder
def build_t111_request(payload, efris_settings):
    """Build complete T111 request structure."""
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
            "interfaceCode":  "T111",
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
    """Send request with timeout."""
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        return response.json() if response.text else {}, response.status_code
    except requests.exceptions.Timeout:
        raise Exception("Request timed out after 30s")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API Error: {str(e)}")

## Process Response
def process_credit_note_response(response_data, server_url, headers, data_to_post, status_code):
    """Process and decrypt T111 credit note response."""
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
    records          = decoded_data.get("records", [])

    if not records:
        frappe.throw("No credit note records found in EFRIS response")

    return {
        "status":         "success",
        "credit_note_no": records[0].get("invoiceNo", "N/A"),
        "id":             records[0].get("id", "N/A"),
    }

## Save to Sales Invoice
def save_credit_note_to_invoice(invoice_name, credit_note_no, credit_note_id):
    """Save credit note number and ID to Sales Invoice via db.set_value.
    Uses db.set_value to bypass the submit restriction on field changes.
    """
    try:
        frappe.db.set_value(
            "Sales Invoice",
            invoice_name,
            {
                "custom_credit_note_number": credit_note_no,
                "custom_id":                 credit_note_id,
            },
            update_modified=False
        )
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(str(e), "Credit Note Save Error")
        frappe.throw(f"Failed to save credit note details to invoice: {str(e)}")

# Main
@frappe.whitelist()
def query_credit_note(invoice_name, custom_reference_number=None, custom_fdn=None):
    """
    Query credit note information from EFRIS T111 API
    and save results to the Sales Invoice.

    Args:
        invoice_name (str): Sales Invoice document name to update.
        custom_reference_number (str): Reference number for query.
        custom_fdn (str): Original invoice FDN.
    """

    # Get settings
    efris_settings = get_efris_settings()

    # Build request
    headers      = {"Content-Type": "application/json"}
    payload      = build_credit_note_payload(custom_reference_number, custom_fdn)
    data_to_post = build_t111_request(payload, efris_settings)

    # Send and process
    try:
        response_data, status_code = send_efris_request(
            efris_settings.server_url, data_to_post, headers
        )
        result = process_credit_note_response(
            response_data, efris_settings.server_url, headers, data_to_post, status_code
        )

        # Save to Sales Invoice via db.set_value (bypasses submit restriction)
        save_credit_note_to_invoice(
            invoice_name,
            result["credit_note_no"],
            result["id"]
        )

        return result

    except Exception as e:
        error_msg = str(e)
        log_integration_request(
            "Failed", efris_settings.server_url, headers, data_to_post, {}, error_msg
        )
        frappe.throw(error_msg)