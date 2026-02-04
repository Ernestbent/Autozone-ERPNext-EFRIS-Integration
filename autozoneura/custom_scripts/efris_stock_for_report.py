import frappe
import json
import requests
from frappe import _
from datetime import datetime, timezone, timedelta
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

eat_timezone = timezone(timedelta(hours=3))

def get_efris_settings():
    efris_settings = frappe.get_single("EFRIS Settings")
    if not efris_settings.is_active:
        frappe.throw(_("EFRIS Settings disabled"))
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw(_("EFRIS Settings are incomplete"))
    if not efris_settings.aes_key:
        frappe.throw(_("AES key not found. Go to EFRIS Settings → Refresh EFRIS Key"))
    return {
        "url": efris_settings.server_url,
        "tin": efris_settings.tin,
        "device_no": efris_settings.device_number,
        "brn": efris_settings.brn or "",
        "aes_key": efris_settings.aes_key
    }

def log_integration_request(status, url, headers, data, response, service=""):
    try:
        frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "integration_request_service": service,
            "is_remote_request": True,
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT")
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

def decrypt_aes_content(encrypted_content, aes_key):
    """ Returns stock records only"""
    try:
        aes_key_bytes = bytes.fromhex(aes_key)
        encrypted_data = base64.b64decode(encrypted_content)
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size)
        decrypted_dict = json.loads(decrypted_data.decode('utf-8'))
        return decrypted_dict.get('records', [])
    except Exception as e:
        frappe.log_error(f"Decrypt failed: {str(e)}", "EFRIS Decrypt")
        frappe.throw(_("AES Key expired → EFRIS Settings → Refresh EFRIS Key → Save"))

def build_t127_request(payload, settings):
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(_(f"Encryption failed: {encrypted_result.get('error')}"))
    
    data_exchange_id = frappe.generate_hash(length=32)
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    return {
        "data": {
            "content": encrypted_result["encrypted_content"],
            "signature": encrypted_result["signature"],
            "dataDescription": {"codeType": "0", "encryptCode": "1", "zipCode": "0"}
        },
        "globalInfo": {
            "appId": "AP04", "version": "1.1.20191201",
            "dataExchangeId": data_exchange_id, "interfaceCode": "T127",
            "requestCode": "TP", "requestTime": current_time, "responseCode": "TA",
            "userName": "admin", "deviceMAC": "B47720524158",
            "deviceNo": settings["device_no"], "tin": settings["tin"],
            "brn": settings["brn"].strip().lstrip("/") if settings["brn"] else "",
            "taxpayerID": "999000002030357", "longitude": "32.61665",
            "latitude": "0.36601", "agentType": "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo": frappe.generate_hash(length=14),
                "operatorName": frappe.session.user
            }
        },
        "returnStateInfo": {"returnCode": "", "returnMessage": ""}
    }

def send_efris_request(server_url, request_data):
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(server_url, json=request_data, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        frappe.throw(_("EFRIS request timed out"))
    except requests.exceptions.RequestException as e:
        frappe.throw(_(f"EFRIS request failed: {str(e)}"))

@frappe.whitelist()
def get_efris_t127_stock_report_data():
    """ Returns Only stock quantities"""
    try:
        cache_key = "efris_t127_stock"
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return {"success": True, "data": cached, "cached": True}
    
        settings = get_efris_settings()
        payload = {"pageNo": "", "pageSize": ""}
        request_data = build_t127_request(payload, settings)
        response_data = send_efris_request(settings['url'], request_data)
        
        log_integration_request('Completed', settings['url'], {}, request_data, response_data, "T127 Stock")
        
        if response_data.get("returnStateInfo", {}).get("returnMessage") != "SUCCESS":
            return {"success": False, "message": "EFRIS error"}
        
        records = decrypt_aes_content(response_data["data"]["content"], settings["aes_key"])
        
        stock_data = []
        for record in records:
            stock_data.append({
                "goods_code": record.get('goodsCode', ''),
                "goods_name": record.get('goodsName', ''),
                "stock_qty": float(record.get('stock', 0))
            })
        
        frappe.cache().set_value(cache_key, stock_data)
        return {"success": True, "data": stock_data, "total_items": len(stock_data)}
        
    except Exception as e:
        return {"success": False, "message": str(e)}
