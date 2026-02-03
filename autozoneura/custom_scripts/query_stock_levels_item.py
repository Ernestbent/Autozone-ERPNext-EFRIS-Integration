import frappe
import json
import base64
import requests
from frappe import _
from datetime import datetime, timezone, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

eat_timezone = timezone(timedelta(hours=3))

## Shared Utilities
def get_efris_settings():
    """Fetch and validate EFRIS configuration."""
    efris_settings = frappe.get_single("EFRIS Settings")
    
    if not efris_settings.is_active:
        frappe.throw(_("EFRIS integration is disabled"))
    
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw(_("EFRIS Settings are incomplete"))
    
    if not efris_settings.aes_key:
        frappe.throw(_("AES key not found. Please refresh EFRIS key."))
    
    return {
        "url": efris_settings.server_url,
        "tin": efris_settings.tin,
        "device_no": efris_settings.device_number,
        "brn": efris_settings.brn or "",
        "aes_key": efris_settings.aes_key
    }

def decrypt_aes_content(encrypted_content, aes_key_hex):
    """Decrypt AES ECB encrypted EFRIS response."""
    try:
        aes_key = bytes.fromhex(aes_key_hex)
        encrypted_data = base64.b64decode(encrypted_content)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        decrypted_data = cipher.decrypt(encrypted_data)
        decrypted_data = unpad(decrypted_data, AES.block_size)
        decrypted_json = decrypted_data.decode('utf-8')
        return json.loads(decrypted_json)
    except Exception as e:
        frappe.throw(_("Decryption failed: {0}").format(str(e)))

def log_integration_request(status, url, headers, data, response, item_code):
    """Log to Integration Request - NO reference_doctype."""
    try:
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "integration_request_service": "Goods Inquiry T128",
            "is_remote_request": True,
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "error": item_code,  # Use item_code as error field for traceability
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT")
            # NO reference_doctype or reference_docname
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

## T128 BUSINESS LOGIC

def build_t128_payload(item_code):
    """Build T128 goods inquiry payload."""
    return {
        "goodsCode": item_code,
        "pageNo": "1",
        "pageSize": ""
    }

def build_t128_request(payload, settings):
    """Build complete T128 EFRIS request."""
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(_("Encryption failed: {0}").format(encrypted_result.get("error")))
    
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
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
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode": "T127",  # Note: T127 per your code
            "requestCode": "TP",
            "requestTime": current_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": settings["device_no"],
            "tin": settings["tin"],
            "brn": settings["brn"],
            "taxpayerID": "999000002030357",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo": frappe.generate_hash(length=14),
                "operatorName": frappe.session.user,
            },
        },
        "returnStateInfo": {"returnCode": "", "returnMessage": ""}
    }

def send_efris_t128_request(server_url, request_data):
    """Send T128 request with timeout."""
    headers = {"Content-Type": "application/json"}
    response = requests.post(server_url, json=request_data, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

def process_t128_response(response_data, settings, item_code, request_data):
    """Process T128 response with decryption."""
    return_state = response_data.get("returnStateInfo", {})
    return_code = return_state.get("returnCode", "")
    return_message = return_state.get("returnMessage", "")
    
    # Log request
    log_integration_request('Completed', settings['url'], {}, request_data, response_data, item_code)
    
    if return_code == "00" or return_message == "SUCCESS":
        data = response_data.get("data", {})
        encrypted_content = data.get("content", "")
        
        if encrypted_content:
            content_data = decrypt_aes_content(encrypted_content, settings["aes_key"])
        else:
            content_data = {"message": "No encrypted content received"}
        
        return {
            "success": True,
            "item_code": item_code,
            "message": return_message,
            "data": content_data
        }
    else:
        log_integration_request('Failed', settings['url'], {}, request_data, response_data, item_code)
        return {
            "success": False,
            "item_code": item_code,
            "message": return_message,
            "return_code": return_code
        }

## API Endpoint
@frappe.whitelist()
def get_efris_stock(item_code):
    """Query EFRIS goods/services using T128 interface."""
    try:
        # Validate input
        if not item_code:
            frappe.throw(_("Item code is required"))
        
        # Get settings and build request
        settings = get_efris_settings()
        payload = build_t128_payload(item_code)
        request_data = build_t128_request(payload, settings)
        
        # Send request
        response_data = send_efris_t128_request(settings['url'], request_data)
        
        # Process response
        return process_t128_response(response_data, settings, item_code, request_data)
        
    except requests.exceptions.RequestException as e:
        frappe.log_error(f"EFRIS T128 Request Error: {str(e)}", "EFRIS Goods Inquiry")
        return {
            "success": False,
            "item_code": item_code,
            "message": str(e),
            "return_code": "REQUEST_ERROR"
        }
    except Exception as e:
        frappe.log_error(f"EFRIS T128 Error: {str(e)}", "EFRIS Goods Inquiry")
        return {
            "success": False,
            "item_code": item_code,
            "message": str(e),
            "return_code": "ERROR"
        }

@frappe.whitelist()
def sync_item_to_efris(item_code):
    """Query item goods/services info from EFRIS system."""
    try:
        result = get_efris_stock(item_code)
        
        if result.get("success"):
            frappe.msgprint(_("Item queried successfully from EFRIS"))
        else:
            frappe.msgprint(
                _("EFRIS Response: {0}").format(result.get("message")),
                indicator="orange"
            )
        
        return result
        
    except Exception as e:
        frappe.log_error(f"EFRIS Sync Error: {str(e)}", "EFRIS Item Sync")
        frappe.throw(str(e))
