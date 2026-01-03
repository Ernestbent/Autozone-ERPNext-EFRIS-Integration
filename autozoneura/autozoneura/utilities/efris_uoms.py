import frappe
import requests
import json
import base64
import gzip
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

eat_timezone = timezone(timedelta(hours=3))

def get_aes_key():
    """Get AES key from cache as bytes"""
    key_hex = frappe.cache().get_value("efris_cached_aes_key")
    
    if not key_hex:
        frappe.msgprint("AES key not found in cache. Refreshing...", indicator="orange")
        from autozoneura.autozoneura.background_tasks.efris_key_manager import test_efris_complete_flow
        result = test_efris_complete_flow()
        if result.get("success"):
            key_hex = result.get("aes_key")
            if not key_hex:
                frappe.throw("AES key missing in response")
        else:
            frappe.throw(f"Failed to get AES key: {result.get('error')}")
    
    # Clean the key - remove any whitespace or newlines
    key_hex = str(key_hex).strip()
    
    try:
        key_bytes = bytes.fromhex(key_hex)
        key_length = len(key_bytes)
        
        # Validate key length (16=AES-128, 24=AES-192, 32=AES-256)
        if key_length not in [16, 24, 32]:
            frappe.throw(f"Invalid AES key length: {key_length} bytes (expected 16, 24, or 32). Hex string length: {len(key_hex)} chars. Please click 'Refresh AES Key' button to get a new key.")
        
        frappe.logger().info(f"AES key loaded: {key_length} bytes ({key_length * 8}-bit)")
        return key_bytes
        
    except ValueError as e:
        frappe.throw(f"Invalid AES key format (not valid hex): {str(e)}. Key preview: {key_hex[:50]}... Please click 'Refresh AES Key' button.")

def decrypt_and_unzip(encrypted_b64, aes_key):
    """Decrypt and decompress data"""
    # Base64 decode
    encrypted = base64.b64decode(encrypted_b64)
    
    # AES decrypt
    cipher = AES.new(aes_key, AES.MODE_ECB)
    decrypted = cipher.decrypt(encrypted)
    decrypted = unpad(decrypted, AES.block_size)
    
    # Gunzip
    return gzip.decompress(decrypted).decode("utf-8")

@frappe.whitelist()
def get_uoms_from_efris(docname):
    """Fetch UOMs from EFRIS and save to ERPNext"""
    try:
        settings = frappe.get_doc("EFRIS Settings", docname)
        
        # Build request
        payload = {
            "data": {"content": "", "signature": "", "dataDescription": {"codeType": "0", "encryptionCode": "1", "zipCode": "1"}},
            "globalInfo": {
                "appId": "AP04",
                "version": "1.1.20191201",
                "dataExchangeId": "1",
                "interfaceCode": "T115",
                "requestCode": "TP",
                "requestTime": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S"),
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "60-FF-9E-47-D4-04",
                "deviceNo": settings.device_number,
                "tin": settings.tin,
                "brn": settings.brn or "",
                "taxpayerID": "1",
                "longitude": "32.61665",
                "latitude": "0.36601",
                "agentType": "0",
                "extendField": {"responseDateFormat": "dd/MM/yyyy", "responseTimeFormat": "dd/MM/yyyy HH:mm:ss", "referenceNo": "24PL01000221", "operatorName": "administrator"}
            },
            "returnStateInfo": {"returnCode": "", "returnMessage": ""}
        }
        
        # Make API request
        response = requests.post(settings.server_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        response_json = response.json()
        
        # Check for errors
        return_code = response_json.get("returnStateInfo", {}).get("returnCode", "")
        if return_code and return_code != "00":
            frappe.throw(f"EFRIS Error {return_code}: {response_json.get('returnStateInfo', {}).get('returnMessage')}")
        
        # Decrypt and decompress
        encrypted_content = response_json.get("data", {}).get("content")
        if not encrypted_content:
            frappe.throw("No content in response")
        
        aes_key = get_aes_key()
        decrypted_json = decrypt_and_unzip(encrypted_content, aes_key)
        data = json.loads(decrypted_json)
        
        # Save UOMs
        created = 0
        updated = 0
        for item in data.get("rateUnit", []):
            uom_name = item.get("name")
            if not uom_name:
                continue
                
            existing = frappe.db.exists("UOM", {"uom_name": uom_name})
            if existing:
                updated += 1
            else:
                frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(ignore_permissions=True)
                created += 1
        
        frappe.db.commit()
        
        return {
            "status": "success",
            "message": f"{created + updated} UOMs synced ({created} created, {updated} updated)"
        }
        
    except Exception as e:
        frappe.log_error(str(e), "EFRIS UOM Sync")
        return {"status": "error", "message": str(e)}