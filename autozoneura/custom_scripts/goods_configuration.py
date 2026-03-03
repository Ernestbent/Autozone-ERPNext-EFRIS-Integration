import frappe
import requests
import json
from datetime import datetime, timezone, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

eat_timezone = timezone(timedelta(hours=3))

## Get Shared Utilities

def get_efris_settings():
    """Fetch and validate EFRIS configuration."""
    efris_settings = frappe.get_single("EFRIS Settings")
    
    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")
    
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw("EFRIS Settings are incomplete")
    
    if not efris_settings.aes_key:
        frappe.throw("AES key not found in EFRIS Settings")
    
    return {
        "server_url": efris_settings.server_url,
        "device_number": efris_settings.device_number,
        "tin": efris_settings.tin,
        "brn": efris_settings.brn or "",
        "aes_key": efris_settings.aes_key
    }

def log_integration_request(status, url, headers, data, response, error=""):
    """Log to Integration Request - NO reference fields."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"
    
    try:
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "method": "POST",
            "integration_request_service": "Goods Upload T130",
            "is_remote_request": True,
            "status": status,
            "url": url,
            "request_headers": json.dumps(headers, indent=4),
            "data": json.dumps(data, indent=4),
            "output": json.dumps(response, indent=4),
            "error": error,
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S EAT")
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass

## Prepare Interface Code Payload data T130

def build_t130_goods_payload(doc):
    """Build T130 goods upload payload."""
    # Use operation type from item field (101=new, 102=modify)
    operation_type = "102"
    
    return {
        "operationType": operation_type,
        "goodsName": doc.item_name,
        "goodsCode": doc.item_code,
        "measureUnit": doc.custom_uom_code_efris,
        "unitPrice": doc.standard_rate,
        "currency": "101",
        "commodityCategoryId": doc.custom_goods_category_id,
        "haveExciseTax": "102",
        "description": doc.description,
        "stockPrewarning": "0",
        "pieceMeasureUnit": "",
        "havePieceUnit": "102",
        "pieceUnitPrice": "",
        "exciseDutyCode": "",
        "haveOtherUnit": "102",
        "goodsTypeCode": "101",
        "haveCustomsUnit": "102",
        "goodsOtherUnits": [],
    }

def build_t130_request(goods_data_list, settings):
    """Build complete T130 EFRIS request with list of goods."""
    if not isinstance(goods_data_list, list):
        goods_data_list = [goods_data_list]
    
    encrypted_result = encrypt_dynamic_json(goods_data_list)
    if not encrypted_result.get("success"):
        frappe.throw(encrypted_result.get("error"))
    
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    return {
        "data": {
            "content": encrypted_result["encrypted_content"],
            "signature": encrypted_result["signature"],
            "dataDescription": {
                "codeType": "0",
                "encryptCode": "1",
                "zipCode": "0"
            }
        },
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode": "T130",
            "requestCode": "TP",
            "requestTime": current_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": settings["device_number"],
            "tin": settings["tin"],
            "brn": settings["brn"],
            "taxpayerID": "1",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "operatorName": frappe.session.user
            }
        },
        "returnStateInfo": {}
    }

def send_efris_t130_request(server_url, request_data):
    """Send T130 request with timeout."""
    headers = {"Content-Type": "application/json"}
    response = requests.post(server_url, json=request_data, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

## Main Event Handler

def on_save(doc, event):
    """Handle Item save event for EFRIS T130 goods upload - single saves."""
    try:
        # Skip if bulk import
        if frappe.flags.in_import:
            doc.custom_pending_sync = 1
            return
        
        # Get validated settings
        settings = get_efris_settings()
        
        # Build and send T130 request
        goods_data = build_t130_goods_payload(doc)
        request_data = build_t130_request([goods_data], settings)
        
        # Send request
        response_data = send_efris_t130_request(settings["server_url"], request_data)
        
        # Process response
        return_state = response_data.get("returnStateInfo", {})
        return_message = return_state.get("returnMessage", "")
        
        # Log request
        if return_message == "SUCCESS":
            log_integration_request("Completed", settings["server_url"], {}, request_data, response_data)
            frappe.msgprint("Item successfully synced with EFRIS")
        else:
            msg = return_message or "Unknown error"
            log_integration_request("Failed", settings["server_url"], {}, request_data, response_data, msg)
            frappe.throw(msg)
            
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        server_url = settings.get("server_url", "") if 'settings' in locals() else ""
        log_integration_request("Failed", server_url, {}, request_data if 'request_data' in locals() else {}, {}, error_msg)
        frappe.throw(error_msg)
    except Exception as e:
        error_msg = str(e)
        server_url = settings.get("server_url", "") if 'settings' in locals() else ""
        log_integration_request("Failed", server_url, {}, {}, {}, error_msg)
        frappe.throw(error_msg)

## Bulk Sync

@frappe.whitelist()
def sync_pending_items_to_efris(batch_size=1):
    """Sync all pending items in batches - batch_size=1 to match EFRIS API limits."""
    try:
        # Convert batch_size to integer
        batch_size = int(batch_size) if batch_size else 1
        
        settings = get_efris_settings()
        
        pending_items = frappe.get_list("Item", 
            filters={"custom_pending_sync": 1},
            fields=["name"],
            limit_page_length=None
        )
        
        if not pending_items:
            return {
                "success": True,
                "message": "No pending items",
                "total_synced": 0
            }
        
        item_codes = [item["name"] for item in pending_items]
        total_synced = 0
        failed_batches = []
        
        for i in range(0, len(item_codes), batch_size):
            batch = item_codes[i:i + batch_size]
            
            try:
                goods_data_list = []
                for item_code in batch:
                    doc = frappe.get_doc("Item", item_code)
                    goods_obj = build_t130_goods_payload(doc)
                    goods_data_list.append(goods_obj)
                
                request_data = build_t130_request(goods_data_list, settings)
                response_data = send_efris_t130_request(settings["server_url"], request_data)
                
                return_state = response_data.get("returnStateInfo", {})
                return_message = return_state.get("returnMessage", "")
                
                if return_message == "SUCCESS":
                    log_integration_request("Completed", settings["server_url"], {}, request_data, response_data)
                    for item_code in batch:
                        frappe.db.set_value("Item", item_code, "custom_pending_sync", 0)
                    frappe.db.commit()
                    total_synced += len(batch)
                else:
                    failed_batches.append({"batch": batch, "error": return_message})
                    log_integration_request("Failed", settings["server_url"], {}, request_data, response_data, return_message)
                    
            except Exception as e:
                failed_batches.append({"batch": batch, "error": str(e)})
                log_integration_request("Failed", settings["server_url"], {}, {}, {}, str(e))
        
        return {
            "success": len(failed_batches) == 0,
            "message": f"Synced {total_synced} items",
            "total_synced": total_synced,
            "total_pending": len(item_codes),
            "failed_batches": failed_batches if failed_batches else None
        }
        
    except Exception as e:
        error_msg = str(e)
        server_url = settings.get("server_url", "") if 'settings' in locals() else ""
        log_integration_request("Failed", server_url, {}, {}, {}, error_msg)
        return {
            "success": False,
            "message": error_msg
        }