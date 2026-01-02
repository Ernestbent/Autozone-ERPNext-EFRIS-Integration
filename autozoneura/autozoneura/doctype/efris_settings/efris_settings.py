import base64
import json
import frappe
from frappe.model.document import Document
from frappe.utils.data import now
import requests
from datetime import datetime, timedelta, timezone

# Define the date in African/Nairobi timezone format UTC+3
eat_timezone = timezone(timedelta(hours=3))

class EFRISSettings(Document):
    pass

@frappe.whitelist()
def test_efris_connection(docname):
    """
    Test EFRIS server connection by pinging the server
    Called from the Test Connection button on EFRIS Settings form
    """
    try:
        # Get the current EFRIS Settings document
        efris_doc = frappe.get_doc("EFRIS Settings", docname)

        # Validate required fields
        if not efris_doc.server_url:
            frappe.throw("Server URL is not configured")
        if not efris_doc.device_number:
            frappe.throw("Device Number is not configured")
        if not efris_doc.tin:
            frappe.throw("TIN is not configured")
        
        # Prepare the request
        url = efris_doc.server_url
        headers = {
            'Content-Type': 'application/json',
        }
        
        # Build the payload for server time request (T101 interface)
        data = {
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
                "dataExchangeId": "71a7047922bb4afa9fd4543c637d6786",
                "interfaceCode": "T101",
                "requestCode": "TP",
                "requestTime": "2026-01-02 14:56:31.442292",
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "60-FF-9E-47-D4-04",
                "deviceNo": efris_doc.device_number,
                "tin": efris_doc.tin,
                "brn": "",
                "taxpayerID": "1",
                "longitude": "32.61665",
                "latitude": "0.36601",
                "agentType": "0",
                "extendField": {
                    "responseDateFormat": "dd/MM/yyyy",
                    "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                    "referenceNo": "24PL01000221",
                    "operatorName": "administrator"
                }
            },
            "returnStateInfo": {
                "returnCode": "",
                "returnMessage": ""
            }
        }
        
        # Send POST request to EFRIS server
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        # Get the JSON response
        response_json = response.json()
        
        # Print response in console for debugging
        print("EFRIS Response JSON:", response_json)
        
        # Decode the base64 content from response
        encoded_string = response_json.get("data", {}).get("content", "")
        decoded_json = {}
        
        if encoded_string:
            decoded_bytes = base64.b64decode(encoded_string)
            decoded_string = decoded_bytes.decode('utf-8')
            decoded_json = json.loads(decoded_string)
        
        # Log the successful integration request
        log_efris_integration('Completed', url, headers, data, response_json, "")
        
        return {
            "status": "success",
            "message": decoded_json,
            "server_time": decoded_json.get("serverTime", "N/A")
        }
        
    except requests.exceptions.Timeout:
        error_msg = "Connection timeout. Please check your network connection."
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {
            "status": "error",
            "message": error_msg
        }
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {
            "status": "error",
            "message": error_msg
        }
    except (base64.binascii.Error, json.JSONDecodeError) as e:
        error_msg = f"Error decoding response: {str(e)}"
        log_efris_integration('Failed', url, headers, data, {}, error_msg)
        return {
            "status": "error",
            "message": error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        frappe.log_error(error_msg, "EFRIS Connection Test Error")
        return {
            "status": "error",
            "message": error_msg
        }

def log_efris_integration(status, url, headers, data, response, error=""):
    """
    Log EFRIS integration request to Integration Request doctype
    """
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    if status not in valid_statuses:
        status = "Failed"
    
    try:
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "is_remote_request": True,
            "integration_request_service": "EFRIS Ping Server",
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "error": error,
            "execution_time": now()
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
        print(f"Integration request logged with status: {status}")
    except Exception as e:
        print(f"Failed to log integration request: {e}")
        frappe.log_error(f"Failed to log integration request: {str(e)}", "EFRIS Integration Logging Error")