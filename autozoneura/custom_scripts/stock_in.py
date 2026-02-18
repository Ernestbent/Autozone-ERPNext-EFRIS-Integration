import base64
from datetime import datetime, timezone, timedelta
import frappe
import requests
import json
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

# East Africa Time
eat_timezone = timezone(timedelta(hours=3))

## Helper Functions

def get_efris_settings():
    """Load and validate EFRIS Settings single doctype."""
    efris_settings = frappe.get_single("EFRIS Settings")
    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")
    
    required_fields = {
        "device_number": efris_settings.device_number,
        "tin": efris_settings.tin,
        "server_url": efris_settings.server_url,
    }
    
    missing = [k for k, v in required_fields.items() if not v]
    if missing:
        frappe.throw(f"EFRIS Settings incomplete: {', '.join(missing)}")
    
    return {
        "device_number": efris_settings.device_number,
        "tin": efris_settings.tin,
        "server_url": efris_settings.server_url,
        "brn": efris_settings.brn or "",
    }

def build_stock_payload(doc):
    """Build T131 Goods Stock In payload from Stock document."""
    stock_in_type_mapping = {
        "Import": "101",
        "Local Purchase": "102",
        "Manufacturing/Assembling": "103",
        "Opening Stock": "104",
    }
    
    stock_in_type = stock_in_type_mapping.get(doc.custom_stock_in_type)
    if not stock_in_type:
        frappe.throw(f"Invalid Stock In Type: {doc.custom_stock_in_type}")
    
    goods_stock_in_items = [
        {
            "commodityGoodsId": "",
            "goodsCode": item.item_code,
            "measureUnit": item.custom_uom_code,
            "quantity": str(item.qty),
            "unitPrice": str(item.rate),
            "remarks": "",
            "fuelTankId": "",
            "lossQuantity": "",
            "originalQuantity": "",
        }
        for item in doc.items
    ]
    
    return {
        "goodsStockIn": {
            "operationType": "101",
            "supplierTin": "",
            "supplierName": doc.supplier_name,
            "adjustType": "",
            "remarks": doc.remarks or "",
            "stockInDate": doc.posting_date,
            "stockInType": stock_in_type,
            "productionBatchNo": "",
            "productionDate": "",
            "branchId": "",
            "invoiceNo": "",
            "isCheckBatchNo": "0",
            "rollBackIfError": "0",
            "goodsTypeCode": "101",
        },
        "goodsStockInItem": goods_stock_in_items,
    }

def encrypt_payload(payload):
    """Encrypt payload using dynamic JSON encryption."""
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")
    return encrypted_result

def build_request_data(payload, settings, doc):
    """Build final API request structure."""
    date_str = doc.posting_date
    time_str = doc.posting_time or "00:00:00"
    datetime_combined = f"{date_str} {time_str}"
    
    encrypted_result = encrypt_payload(payload)
    
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
            "interfaceCode": "T131",
            "requestCode": "TP",
            "requestTime": datetime_combined,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": settings["device_number"],
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
        "returnStateInfo": {"returnCode": "", "returnMessage": ""},
    }

def log_integration_request(status, url, headers, data, response, error=""):
    """Log to Integration Request doctype (raw payloads)."""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"
    
    integration_request = frappe.get_doc({
        "doctype": "Integration Request",
        "integration_type": "Remote",
        "integration_request_service": "Goods Stock Maintain T131",
        "is_remote_request": True,
        "method": "POST",
        "status": status,
        "url": url,
        "request_headers": json.dumps(headers, indent=4),
        "data": json.dumps(data, indent=4),
        "output": json.dumps(response, indent=4),
        "error": error,
        "reference_doctype": "Stock Entry", 
        "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    })
    integration_request.insert(ignore_permissions=True)
    frappe.db.commit()

def send_efris_request(server_url, data_to_post):
    """Send POST request to EFRIS and handle response."""
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=60)
        response_data = response.json() if response.text else {}
        return response.status_code, response_data
    except requests.exceptions.Timeout:
        raise Exception("Request timed out after 60s")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API Error: {str(e)}")

def handle_efris_response(doc, response_data, server_url, data_to_post):
    """Process response, log, and update document."""
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
    
    status = 'Completed' if response_data.get("status_code") == 200 else 'Failed' 
    log_integration_request(status, server_url, {}, data_to_post, response_data, return_message)
    
    # Store in custom fields
    doc.custom_post_request = json.dumps(data_to_post, indent=4)
    doc.custom_response_ = json.dumps(response_data, indent=4)
    doc.custom_return_status = return_message
    doc.save()
    
    if return_message == "SUCCESS":
        frappe.msgprint("Stock successfully recorded in EFRIS")
    else:
        frappe.msgprint(f"EFRIS Response: {return_message}. Details in Integration Request.", title="EFRIS API", indicator="orange")

## Main Hook
def on_stock(doc, event):
    """Main hook for Stock Entry/Purchase Receipt submit event."""
    # if not doc.custom_efris_stock:
    #     return
    
    try:
        settings = get_efris_settings()
        payload = build_stock_payload(doc)
        data_to_post = build_request_data(payload, settings, doc)
        server_url = settings["server_url"]
        
        status_code, response_data = send_efris_request(server_url, data_to_post)
        handle_efris_response(doc, response_data, server_url, data_to_post)
        
    except Exception as e:
        error_msg = str(e)
        settings = get_efris_settings()  # Safe re-fetch for logging
        log_integration_request('Failed', settings["server_url"], {}, data_to_post if 'data_to_post' in locals() else {}, {}, error_msg)
        frappe.throw(error_msg)
