import frappe
import json
import base64
import requests
from frappe import _
from datetime import datetime, timezone, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

# East Africa Time
eat_timezone = timezone(timedelta(hours=3))

def decrypt_aes_content(encrypted_content, aes_key_hex):
    """
    Decrypt AES encrypted content from EFRIS response
    """
    try:
        # Convert hex key to bytes
        aes_key = bytes.fromhex(aes_key_hex)
        
        # Base64 decode the encrypted content
        encrypted_data = base64.b64decode(encrypted_content)
        
        # AES ECB mode decryption (EFRIS uses ECB mode)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        decrypted_data = cipher.decrypt(encrypted_data)
        
        # Remove padding
        decrypted_data = unpad(decrypted_data, AES.block_size)
        
        # Convert to string and parse JSON
        decrypted_json = decrypted_data.decode('utf-8')
        return json.loads(decrypted_json)
        
    except Exception as e:
        print(f"Decryption error: {str(e)}")
        raise

@frappe.whitelist()
def get_efris_stock(item_code):
    """
    Query EFRIS goods/services using T128 interface code with encryption
    Interface: Goods/Services Inquiry
    """
    try:
        # Get EFRIS settings
        efris_settings = get_efris_settings()
        
        # Get AES key from EFRIS Settings
        aes_key = efris_settings.get("aes_key")
        if not aes_key:
            frappe.throw(_("AES key not found. Please refresh EFRIS key."))
        
        # Prepare T128 request payload for goods inquiry
        payload = {
            "goodsCode": item_code,
            "pageNo": "1",
            "pageSize": ""
        }
        
        # Encrypt the payload
        encrypted_result = encrypt_dynamic_json(payload)
        if not encrypted_result.get("success"):
            frappe.throw(_("Encryption failed: {0}").format(encrypted_result.get("error")))
        
        # Build final request with encrypted content
        current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
        final_request = {
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
                "interfaceCode": "T127",
                "requestCode": "TP",
                "requestTime": current_time,
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "B47720524158",
                "deviceNo": efris_settings["device_no"],
                "tin": efris_settings["tin"],
                "brn": efris_settings["brn"],
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
        
        # Make request to EFRIS API
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            efris_settings['url'], 
            json=final_request, 
            headers=headers, 
            timeout=60
        )
        response_data = response.json() if response.text else {}
        
        print("\n>>> EFRIS Response:")
        print(json.dumps(response_data, indent=2))
        
        # Process response
        return_state = response_data.get("returnStateInfo", {})
        return_code = return_state.get("returnCode", "")
        return_message = return_state.get("returnMessage", "")
        
        if return_code == "00" or return_message == "SUCCESS":
            data = response_data.get("data", {})
            encrypted_content = data.get("content", "")
            
            # Decrypt the content using AES key
            try:
                content_data = decrypt_aes_content(encrypted_content, aes_key)
                print("\n>>> Decrypted Content:")
                print(json.dumps(content_data, indent=2))
            except Exception as decrypt_error:
                print(f"Decryption failed: {str(decrypt_error)}")
                content_data = {"error": "Decryption failed", "details": str(decrypt_error)}
            
            # Log the request
            log_integration_request(
                'Completed',
                efris_settings['url'],
                headers,
                final_request,
                response_data,
                item_code,
                content_data
            )
            
            return {
                "success": True,
                "item_code": item_code,
                "message": return_message,
                "data": content_data
            }
        else:
            # Log failed request
            log_integration_request(
                'Failed',
                efris_settings['url'],
                headers,
                final_request,
                response_data,
                item_code
            )
            
            return {
                "success": False,
                "item_code": item_code,
                "message": return_message,
                "return_code": return_code
            }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        frappe.log_error(f"EFRIS T128 Error: {str(e)}", "EFRIS Goods Inquiry")
        return {
            "success": False,
            "item_code": item_code,
            "message": str(e),
            "return_code": "ERROR"
        }


def get_efris_settings():
    """Fetch EFRIS configuration settings"""
    efris_settings = frappe.get_single("EFRIS Settings")
    
    if not efris_settings.is_active:
        frappe.throw(_("EFRIS integration is disabled"))
    
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw(_("EFRIS Settings are incomplete"))
    
    return {
        "url": efris_settings.server_url,
        "tin": efris_settings.tin,
        "device_no": efris_settings.device_number,
        "brn": efris_settings.brn or "",
        "aes_key": efris_settings.aes_key or ""
    }


def log_integration_request(status, url, headers, data, response, item_code, decrypted_data=None):
    """Log integration request to Integration Request doctype"""
    try:
        output = {
            "encrypted_response": response,
            "decrypted_data": decrypted_data if decrypted_data else {}
        }
        
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
            "output": json.dumps(output, indent=4),
            "reference_doctype": "Item",
            "reference_docname": item_code,
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except:
        pass


@frappe.whitelist()
def sync_item_to_efris(item_code):
    """Query item goods/services info from EFRIS system"""
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
        frappe.log_error(f"EFRIS Query Error: {str(e)}", "EFRIS Goods Query")
        frappe.throw(str(e))