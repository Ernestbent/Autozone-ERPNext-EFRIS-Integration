import frappe
import json
import requests
from frappe import _
from datetime import datetime, timezone, timedelta

# Import encryption function from your module
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

# East Africa Time
eat_timezone = timezone(timedelta(hours=3))

@frappe.whitelist()
def get_efris_stock(item_code):
    """
    Query EFRIS goods/services using T127 interface code with encryption
    Interface: Goods/Services Inquiry
    """
    try:
        # Get item details
        item = frappe.get_doc("Item", item_code)
        
        # Get EFRIS settings
        efris_settings = get_efris_settings()
        
        # Prepare T127 request payload for goods inquiry
        payload = prepare_t127_payload(item, efris_settings)
        
        # Encrypt the payload
        encrypted_result = encrypt_payload(payload)
        
        # Build final request with encrypted content
        final_request = build_final_request(encrypted_result, efris_settings)
        
        # Make request to EFRIS API
        response = send_efris_request(final_request, efris_settings, item_code)
        
        # Process and return response
        return process_efris_response(response, item_code)
        
    except Exception as e:
        frappe.log_error(f"EFRIS T127 Error: {str(e)}", "EFRIS Goods Inquiry")
        frappe.throw(_("Failed to query EFRIS goods/services: {0}").format(str(e)))


def get_efris_settings():
    """
    Fetch EFRIS configuration settings
    """
    efris_settings = frappe.get_single("EFRIS Settings")
    
    if not efris_settings.is_active:
        frappe.throw(_("EFRIS integration is disabled"))
    
    # Validate required fields
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw(_("EFRIS Settings are incomplete. Please configure Device Number, TIN, and Server URL."))
    
    return {
        "url": efris_settings.server_url,
        "tin": efris_settings.tin,
        "device_no": efris_settings.device_number,
        "legal_name": efris_settings.legal_name,
        "business_name": efris_settings.business_name,
        "company": efris_settings.company,
        "branch_id": efris_settings.branch_id or "",
        "brn": efris_settings.brn or "",
    }


def prepare_t127_payload(item, settings):
    """
    Prepare T127 interface request payload for Goods/Services Inquiry
    According to URA EFRIS documentation
    
    Interface: T127 - Goods/Services Inquiry
    Request/Response: Encrypted
    """
    
    # Get current date for date range
    current_date = datetime.now(eat_timezone).strftime("%Y-%m-%d")
    
    # T127 - Goods/Services Inquiry Request Structure
    payload = {
        "goodsCode": item.item_code or "",  # Item code to search
        "goodsName": item.item_name or "",  # Item name
        "commodityCategoryName": item.item_group or "",  # Category name
        "pageNo": "1",  # Page number for pagination
        "pageSize": "10",  # Records per page
    }

    return payload


def encrypt_payload(payload):
    """
    Encrypt and sign the payload using the encryption module
    """
    encrypted_result = encrypt_dynamic_json(payload)
    
    if not encrypted_result.get("success"):
        frappe.throw(_("Encryption failed: {0}").format(encrypted_result.get("error")))
    
    return encrypted_result


def build_final_request(encrypted_result, settings):
    """
    Build the final request with encrypted content and global info
    """
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    final_request = {
        "data": {
            "content": encrypted_result["encrypted_content"],
            "signature": encrypted_result["signature"],
            "dataDescription": {
                "codeType": "0",
                "encryptCode": "1",  # 1 = Encrypted
                "zipCode": "0",
            },
        },
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode": "T127",  # Interface code for Goods/Services Inquiry
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
        "returnStateInfo": {
            "returnCode": "",
            "returnMessage": ""
        }
    }
    
    return final_request


def send_efris_request(final_request, settings, item_code):
    """
    Send encrypted request to EFRIS API endpoint
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    url = settings['url']
    
    def log_integration_request(status, url, headers, data, response, error=""):
        """Log integration request to Integration Request doctype - RAW payloads only"""
        valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
        status = status if status in valid_statuses else "Failed"
        
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "integration_request_service": "Goods/Services Inquiry T127",
            "is_remote_request": True,
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "error": error,
            "reference_doctype": "Item",
            "reference_docname": item_code,
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    
    try:
        response = requests.post(url, json=final_request, headers=headers, timeout=60)
        response_data = response.json() if response.text else {}
        
        return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
        
        # Log Raw Request and Response
        log_integration_request(
            'Completed' if response.status_code == 200 else 'Failed',
            url,
            headers,
            final_request,
            response_data,
            f"HTTP {response.status_code}: {return_message}"
        )
        
        return response_data
        
    except requests.exceptions.Timeout:
        error_msg = "Request timed out after 60s"
        log_integration_request('Failed', url, headers, final_request, {}, error_msg)
        frappe.throw(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"API Error: {str(e)}"
        log_integration_request('Failed', url, headers, final_request, {}, error_msg)
        frappe.throw(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        log_integration_request('Failed', url, headers, final_request, {}, error_msg)
        frappe.throw(error_msg)


def process_efris_response(response, item_code):
    """
    Process EFRIS API response
    Response is encrypted and needs to be decrypted
    """
    if not response:
        frappe.throw(_("Empty response from EFRIS"))
    
    return_state = response.get("returnStateInfo", {})
    return_code = return_state.get("returnCode", "")
    return_message = return_state.get("returnMessage", "")
    
    if return_code == "00" or return_message == "SUCCESS":
        # Success - response content is encrypted
        data = response.get("data", {})
        encrypted_content = data.get("content", "")
        signature = data.get("signature", "")
        
        # Note: You'll need to implement decryption here
        # For now, return the encrypted content
        # TODO: Add decryption using your decrypt function
        
        try:
            # If you have a decrypt function, use it here:
            # from autozoneura.autozoneura.background_tasks.encryption import decrypt_dynamic_json
            # decrypted_data = decrypt_dynamic_json(encrypted_content, signature)
            
            # For now, just parse if it's JSON
            content_data = json.loads(encrypted_content) if isinstance(encrypted_content, str) else encrypted_content
        except:
            content_data = encrypted_content
        
        return {
            "success": True,
            "item_code": item_code,
            "message": return_message,
            "return_code": return_code,
            "data": content_data,
            "encrypted_content": encrypted_content,
            "signature": signature,
            "response": response
        }
    else:
        # Error or non-success response
        return {
            "success": False,
            "item_code": item_code,
            "message": return_message,
            "return_code": return_code,
            "response": response
        }


@frappe.whitelist()
def search_efris_goods(search_term="", page_no=1, page_size=10):
    """
    Search for goods/services in EFRIS
    """
    try:
        efris_settings = get_efris_settings()
        current_date = datetime.now(eat_timezone).strftime("%Y-%m-%d")
        
        # Build search payload
        payload = {
            "goodsCode": "",
            "goodsName": search_term,
            "commodityCategoryName": "",
            "pageNo": str(page_no),
            "pageSize": str(page_size),
            "branchId": efris_settings["branch_id"],
            "serviceMark": "101",  # 101: Goods
            "haveExciseTax": "101",
            "startDate": current_date,
            "endDate": current_date,
            "combineKeywords": search_term,
            "goodsTypeCode": "101",
            "tin": efris_settings["tin"],
            "queryType": "1"
        }
        
        # Encrypt
        encrypted_result = encrypt_payload(payload)
        
        # Build request
        final_request = build_final_request(encrypted_result, efris_settings)
        
        # Send request
        headers = {"Content-Type": "application/json"}
        response = requests.post(efris_settings['url'], json=final_request, headers=headers, timeout=60)
        response_data = response.json() if response.text else {}
        
        return process_efris_response(response_data, search_term)
        
    except Exception as e:
        frappe.log_error(f"EFRIS Search Error: {str(e)}", "EFRIS Goods Search")
        frappe.throw(str(e))

@frappe.whitelist()
def sync_item_to_efris(item_code):
    """
    Query item goods/services info from EFRIS system
    """
    try:
        result = get_efris_stock(item_code)
        
        if result.get("success"):
            frappe.msgprint(_("Goods/Services queried successfully from EFRIS for {0}").format(item_code))
        else:
            frappe.msgprint(
                _("EFRIS Response: {0}. Full details logged in Integration Request.").format(result.get("message")),
                title=_("EFRIS API Response"),
                indicator="orange"
            )
        
        return result
        
    except Exception as e:
        frappe.log_error(f"EFRIS Query Error: {str(e)}", "EFRIS Goods Query")
        frappe.throw(str(e))