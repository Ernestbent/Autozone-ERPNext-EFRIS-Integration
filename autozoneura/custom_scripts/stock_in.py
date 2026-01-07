import base64
from datetime import datetime, timezone, timedelta
import frappe
import requests
import json

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

# East Africa Time
eat_timezone = timezone(timedelta(hours=3))

def on_stock(doc, event):
    # Check if EFRIS is enabled for this Purchase Receipt
    if not doc.custom_efris_stock:
        return

    date_str = doc.posting_date
    time_str = doc.posting_time
    datetime_combined = f"{date_str} {time_str}"

    # Load Single Doctype
    efris_settings = frappe.get_single("EFRIS Settings")
    
    # Check if EFRIS integration is active
    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")

    # Get settings with proper field names
    device_number = efris_settings.device_number
    tin = efris_settings.tin
    server_url = efris_settings.server_url
    brn = efris_settings.brn or ""

    # Validate required fields
    if not device_number or not tin or not server_url:
        frappe.throw("EFRIS Settings are incomplete. Please configure Device Number, TIN, and Server URL.")

    # Stock in type mapping
    stock_in_type_mapping = {
        "Import": "101",
        "Local Purchase": "102",
        "Manufacturing/Assembling": "103",
        "Opening Stock": "104",
    }
    stock_in_type = stock_in_type_mapping.get(doc.custom_stock_in_type, "")

    # Validate stock in type
    if not stock_in_type:
        frappe.throw(f"Invalid Stock In Type: {doc.custom_stock_in_type}. Please select a valid type.")

    # Build goods stock in items
    goods_stock_in_items = []
    for item in doc.items:
        goods_stock_in_items.append({
            "commodityGoodsId": "",
            "goodsCode": item.item_code,
            "measureUnit": item.custom_uom_code,
            "quantity": str(item.qty),
            "unitPrice": str(item.rate),
            "remarks": "",
            "fuelTankId": "",
            "lossQuantity": "",
            "originalQuantity": "",
        })

    # Build payload
    payload = {
        "goodsStockIn": { 
            "operationType": "101",
            "supplierTin": "",
            "supplierName": doc.supplier_name,
            "adjustType": "",
            "remarks": doc.remarks,
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

    # Encrypt and Sign
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")

    # Build final data to post
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
            "dataExchangeId": frappe.generate_hash(length=18),
            "interfaceCode": "T131",
            "requestCode": "TP",
            "requestTime": datetime_combined,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": device_number,
            "tin": tin,
            "brn": brn,
            "taxpayerID": "1",
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
        """Log integration request to Integration Request doctype - RAW payloads only"""
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
            "reference_doctype": doc.doctype,
            "reference_docname": doc.name,
            "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
        })
        integration_request.insert(ignore_permissions=True)
        frappe.db.commit()

    try:
        headers = {"Content-Type": "application/json"}
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=60)
        response_data = response.json() if response.text else {}
        
        return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
        
        # Log Raw Request and Response
        log_integration_request('Completed' if response.status_code == 200 else 'Failed', 
                               server_url, headers, data_to_post, response_data,
                               f"HTTP {response.status_code}: {return_message}")

        # Store in document custom fields
        doc.custom_post_request = json.dumps(data_to_post, indent=4)
        doc.custom_response_ = json.dumps(response_data, indent=4)
        doc.custom_return_status = return_message
        
        # Show user message based on response
        if response.status_code == 200 and return_message == "SUCCESS":
            frappe.msgprint("Stock successfully recorded in EFRIS")
        else:
            frappe.msgprint(f" EFRIS Response: {return_message}. Full details logged in Integration Request.", 
                          title="EFRIS API Response", indicator="orange")

    except requests.exceptions.Timeout:
        error_msg = "Request timed out after 60s"
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"API Error: {str(e)}"
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
