import frappe
import requests
import json
import base64
import gzip
import uuid
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

EAT_TZ = timezone(timedelta(hours=3))

def get_aes_key_from_cache():
    """Get AES key from cache (set by T104 key manager)"""
    key_hex = frappe.cache().get_value("efris_cached_aes_key")
    if not key_hex:
        frappe.throw("AES Key not found in cache. Please click 'Refresh AES Key' button first.")
    
    try:
        key_bytes = bytes.fromhex(key_hex)
        if len(key_bytes) not in [16, 24, 32]:
            frappe.throw(f"Invalid AES key length: {len(key_bytes)} bytes")
        return key_bytes
    except Exception as e:
        frappe.throw(f"Invalid AES key: {str(e)}")


def decrypt_response(encrypted_content, aes_key_bytes):
    """
    Decrypt and decompress EFRIS T115 response
    Order: Base64 → GZIP → AES → JSON
    """
    try:
        # Step 1: Base64 decode
        compressed_bytes = base64.b64decode(encrypted_content)
        
        # Step 2: GZIP decompress
        try:
            encrypted_bytes = gzip.decompress(compressed_bytes)
        except Exception as e:
            # Try removing trailing bytes
            for i in range(1, 5):
                try:
                    encrypted_bytes = gzip.decompress(compressed_bytes[:-i])
                    break
                except:
                    continue
            else:
                raise Exception(f"GZIP decompress failed: {str(e)}")
        
        # Step 3: Handle AES block alignment
        remainder = len(encrypted_bytes) % 16
        if remainder != 0:
            encrypted_bytes = encrypted_bytes[:-remainder]
        
        # Step 4: AES decrypt (ECB mode)
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        decrypted_padded = cipher.decrypt(encrypted_bytes)
        
        # Step 5: Remove PKCS7 padding
        try:
            decrypted_data = unpad(decrypted_padded, AES.block_size)
        except ValueError:
            decrypted_data = decrypted_padded
        
        # Step 6: Parse JSON
        try:
            result = json.loads(decrypted_data.decode('utf-8'))
        except UnicodeDecodeError:
            result = json.loads(decrypted_data.decode('latin-1'))
        
        return result
        
    except Exception as e:
        frappe.log_error(f"Decryption failed: {str(e)}", "EFRIS T115 Decryption Error")
        frappe.throw(f"Failed to decrypt response: {str(e)}")

@frappe.whitelist()
def get_uoms_from_efris(docname):
    """Fetch UOMs from EFRIS T115 and save to ERPNext"""
    
    def get_current_datetime():
        now = datetime.now(EAT_TZ)
        return now.strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Get settings
        settings = frappe.get_doc("EFRIS Settings", docname)
        
        if not settings.server_url:
            frappe.throw("Server URL not configured")
        if not settings.device_number:
            frappe.throw("Device Number not configured")
        if not settings.tin:
            frappe.throw("TIN not configured")
        
        current_time = get_current_datetime()
        
        
        # Generate 32-character UUID for dataExchangeId
        data_exchange_id = str(uuid.uuid4()).replace('-', '')  # Remove hyphens for 32 chars
        
        # Build request payload
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
                "dataExchangeId": data_exchange_id,
                "interfaceCode": "T115",
                "requestCode": "TP",
                "requestTime": current_time,
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "60-FF-9E-47-D4-04",
                "deviceNo": settings.device_number,
                "tin": settings.tin,
                "brn": settings.brn or "",
                "taxpayerID": "1",
                "longitude": "0.321",
                "latitude": "32.5714",
                "agentType": "0",
                "extendField": {
                    "responseDateFormat": "dd/MM/yyyy",
                    "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                    "referenceNo": "",
                    "operatorName": frappe.session.user
                }
            },
            "returnStateInfo": {
                "returnCode": "",
                "returnMessage": ""
            }
        }
        
        headers = {'Content-Type': 'application/json'}
        
        # Make API call
        response = requests.post(
            settings.server_url,
            headers=headers,
            json=data,
            timeout=45
        )
        response.raise_for_status()
        response_json = response.json()
        
        # Check return code
        return_code = response_json.get('returnStateInfo', {}).get('returnCode', '')
        return_msg = response_json.get('returnStateInfo', {}).get('returnMessage', '')
        
        if return_code != "00":
            frappe.throw(f"EFRIS Error {return_code}: {return_msg}")
        
        # Get encrypted content
        encrypted_content = response_json.get('data', {}).get('content', '')
        if not encrypted_content:
            frappe.throw("No content in response from EFRIS")
        
        # Decrypt response
        aes_key = get_aes_key_from_cache()
        json_data = decrypt_response(encrypted_content, aes_key)
        
        # Get UOMs from rateUnit
        uoms = json_data.get("rateUnit", [])
        
        if not uoms:
            return {
                "status": "warning",
                "message": f"No UOMs found in response. Keys: {list(json_data.keys())}"
            }
        
        # Process UOMs
        created = 0
        updated = 0
        errors = []
        
        for rate in uoms:
            uom_name = rate.get("name", "").strip()
            uom_value = rate.get("value", "").strip()
            
            if not uom_name:
                continue
            
            try:
                # Check if UOM exists
                existing_uom = frappe.get_all("UOM", filters={"uom_name": uom_name}, limit=1)
                
                if existing_uom:
                    # Update existing UOM
                    uom_doc = frappe.get_doc("UOM", uom_name)
                    uom_doc.custom_value = uom_value
                    uom_doc.save(ignore_permissions=True)
                    updated += 1
                else:
                    # Insert new UOM
                    uom_doc = frappe.new_doc("UOM")
                    uom_doc.uom_name = uom_name
                    uom_doc.custom_value = uom_value
                    uom_doc.enabled = 1
                    uom_doc.must_be_whole_number = 0
                    uom_doc.insert(ignore_permissions=True)
                    created += 1
                    
            except Exception as e:
                errors.append(f"{uom_name}: {str(e)}")
        
        # Commit changes
        frappe.db.commit()
        
        # Log integration request
        log_integration_request(
            status='Completed',
            url=settings.server_url,
            headers=headers,
            data=data,
            response=response_json
        )
        
        # Build response message
        message = f"Processed {len(uoms)} Efris UOMS\n"
        # message += f"   • {created} created\n"
        # message += f"   • {updated} updated"
        
        if errors:
            message += f"\n\n⚠️ {len(errors)} errors:\n   • " + "\n   • ".join(errors[:3])
            if len(errors) > 3:
                message += f"\n   • ... and {len(errors) - 3} more"
        
        return {
            "status": "success",
            "message": message,
            "created": created,
            "updated": updated,
            "total": len(uoms)
        }
        
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        frappe.log_error(error_msg, "EFRIS T115 Request Error")
        return {
            "status": "error",
            "message": error_msg
        }
    except Exception as e:
        error_msg = str(e)
        frappe.log_error(error_msg, "EFRIS T115 Sync Error")
        return {
            "status": "error",
            "message": error_msg
        }

def log_integration_request(status, url, headers, data, response):
    """Log integration request to ERPNext"""
    try:
        valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
        if status not in valid_statuses:
            status = "Failed"
        
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "is_remote_request": True,
            "integration_request_service": "System Dictionary",
            "method": "POST",
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4)
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Failed to log integration request: {str(e)}", "Integration Request Log Error")