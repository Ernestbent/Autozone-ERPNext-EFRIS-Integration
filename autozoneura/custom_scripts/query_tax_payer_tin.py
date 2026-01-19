import json
import base64
from datetime import datetime, timedelta, timezone
import frappe
import requests
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

eat_timezone = timezone(timedelta(hours=3))

def log_integration_request(status, url, headers, data, response, error="", reference_doctype="", reference_docname=""):
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
            "reference_doctype": reference_doctype,
            "reference_docname": reference_docname,
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT")
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

@frappe.whitelist()
def query_tax_payer(tax_id, customer_name=""):
    """TIN Validation - EFRIS T119 API with DECRYPTION & FIELD POPULATION"""
    
    if not tax_id or len(tax_id.strip()) < 10:
        frappe.throw("Please enter a valid TIN (minimum 10 characters)")
    
    # SINGLE EFRIS Settings
    efris_settings = frappe.get_single("EFRIS Settings")
    
    # Validate settings
    required = {
        "Device Number": efris_settings.device_number,
        "TIN": efris_settings.tin,
        "Server URL": efris_settings.server_url
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        frappe.throw(f"EFRIS Settings incomplete: {', '.join(missing)}")
    
    # TIN Query Payload
    json_data = {"ninBrn": "", "tin": tax_id.strip()}
    encrypted_result = encrypt_dynamic_json(json_data)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")
    
    # Dynamic headers
    request_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    data_exchange_id = frappe.generate_hash(length=18)
    reference_no = frappe.generate_hash(length=14)
    
    # EFRIS T119 Complete Payload (same structure as Stock Entry)
    data_to_post = {
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
    
    headers = {"Content-Type": "application/json"}
    
    try:
        # Send request (same as Stock Entry)
        response = requests.post(efris_settings.server_url, json=data_to_post, headers=headers, timeout=30)
        response_data = response.json() if response.text else {}
        
        return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
        
        # LOG RAW Response (like Stock Entry)
        log_integration_request(
            'Completed' if response.status_code == 200 else 'Failed',
            efris_settings.server_url, headers, data_to_post, response_data,
            f"HTTP {response.status_code}: {return_message}",
            "Customer", customer_name or tax_id
        )
        
        # DECRYPT RESPONSE CONTENT (like you want)
        if response.status_code == 200 and return_message == "SUCCESS":
            content = response_data.get("data", {}).get("content")
            if content:
                # DECRYPT using your decrypt_string function
                decrypted_string = decrypt_string(content)
                decoded_data = json.loads(decrypted_string)
                taxpayer = decoded_data.get("taxpayer", {})
                
                # POPULATE CUSTOMER FIELDS (like Stock Entry stores custom fields)
                result = {
                    "success": True,
                    "business_name": taxpayer.get("legalName", ""),
                    "nin_brn": taxpayer.get("ninBrn", ""),
                    "taxpayer_type": taxpayer.get("taxpayerType", ""),
                    "contact_email": taxpayer.get("contactEmail", ""),
                    "contact_number": taxpayer.get("contactNumber", ""),
                    "address": taxpayer.get("address", ""),
                    "government_tin": taxpayer.get("governmentTIN", taxpayer.get("tin", "")),
                    "tax_id": tax_id,
                    "legal_name": taxpayer.get("legalName", "")
                }
                
                # # Store raw response in custom fields (like Stock Entry)
                # frappe.db.set_value("Customer", customer_name, {
                #     "custom_post_request": json.dumps(data_to_post, indent=4),
                #     "custom_response": json.dumps(response_data, indent=4),
                #     "custom_return_status": return_message
                # })
                
                return result
            else:
                frappe.throw("No encrypted content in URA response")
        else:
            frappe.throw(f"EFRIS Response: {return_message}. Check Integration Request log.", 
                        title="EFRIS API Response")
        
    except requests.exceptions.Timeout:
        error_msg = "Request timed out after 30s"
        log_integration_request('Failed', efris_settings.server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"API Error: {str(e)}"
        log_integration_request('Failed', efris_settings.server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        log_integration_request('Failed', efris_settings.server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
