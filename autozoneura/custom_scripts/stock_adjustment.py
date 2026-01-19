import base64
from datetime import datetime, timezone, timedelta
import frappe
import requests
import json
import uuid

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

eat_timezone = timezone(timedelta(hours=3))  # UTC+3
current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")


def stock_adjust(doc, event):
    date_str = doc.posting_date
    time_str = doc.posting_time
    datetime_combined = f"{date_str} {time_str}"

    # Load Single Doctype # Load Single Doctype
    efris_settings = frappe.get_single("EFRIS Settings")
    
    # Check if EFRIS integration is active
    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")

    # Get settings with proper field names
    device_number = efris_settings.device_number
    tin = efris_settings.tin
    server_url = efris_settings.server_url
    brn = efris_settings.brn 


    adjustment_type_mapping = {
        "Expired Goods": "101",
        "Damaged Goods": "102",
        "Personal Uses": "103",
        "Raw Materials": "105",
    }

    adjust_reason = doc.custom_adjustment_type
    adjust_type_code = adjustment_type_mapping.get(adjust_reason, "")

    for item in doc.items:
        goods_stock_in = {
            "commodityGoodsId": "",
            "goodsCode": item.item_code,
            "measureUnit": item.custom_uom_code,
            "quantity": item.qty,
            "unitPrice": item.basic_rate,
            "remarks": "",
            "fuelTankId": "",
            "lossQuantity": "",
            "originalQuantity": "",
        }

        data_payload = {
            "goodsStockIn": {
                "operationType": "102",
                "supplierTin": "",
                "supplierName": "",
                "adjustType": adjust_type_code,
                "remarks": "",
                "stockInDate": doc.posting_date,
                "stockInType": "",
                "productionBatchNo": "",
                "productionDate": "",
                "branchId": "",
                "invoiceNo": "",
                "isCheckBatchNo": "0",
                "rollBackIfError": "0",
                "goodsTypeCode": "101",
            },
            "goodsStockInItem": [goods_stock_in],
        }

        # Encrypt the payload
        encrypted_result = encrypt_dynamic_json(data_payload)
        if not encrypted_result.get("success"):
            frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")

        # Generate a 32-character UUID (UUID4 without hyphens)
        data_exchange_id = uuid.uuid4().hex[:32]

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
                "dataExchangeId": data_exchange_id,
                "interfaceCode": "T131",
                "requestCode": "TP",
                "requestTime": datetime_combined,
                "responseCode": "TA",
                "userName": "admin",
                "deviceMAC": "B47720524158",
                "deviceNo": device_number,
                "tin": tin,
                "brn": brn,
                "taxpayerID": "999000002030357",
                "longitude": "32.61665",
                "latitude": "0.36601",
                "agentType": "0",
                "extendField": {
                    "responseDateFormat": "dd/MM/yyyy",
                    "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                    "referenceNo": "",
                    "operatorName": "administrator",
                },
            },
            "returnStateInfo": {
                "returnCode": "",
                "returnMessage": ""
            },
        }

        def log_integration_request(status, url, headers, data, response, error=""):
            valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
            status = status if status in valid_statuses else "Failed"
            integration_request = frappe.get_doc({
                "doctype": "Integration Request",
                "integration_type": "Remote",
                "integration_request_service": "Stock Adjustment",
                "is_remote_request": True,
                "method": "POST",
                "status": status,
                "url": url,
                "request_headers": json.dumps(headers, indent=4),
                "data": json.dumps(data, indent=4),
                "output": json.dumps(response, indent=4),
                "error": error,
                "execution_time": datetime.now()
            })
            integration_request.insert(ignore_permissions=True)
            frappe.db.commit()

        try:
            headers = {'Content-Type': 'application/json'}
            response = requests.post(server_url, json=data_to_post, headers=headers)
            response.raise_for_status()
            response_data = response.json()
            return_message = response_data["returnStateInfo"]["returnMessage"]

            doc.custom_post_payload = json.dumps(data_to_post, indent=4)
            doc.custom_response_payload = json.dumps(response_data, indent=4)
            doc.custom_return_status = return_message

            log_integration_request('Completed', server_url, headers, data_to_post, response_data)

            if response.status_code == 200 and return_message == "SUCCESS":
                frappe.msgprint("Stock Levels Adjusted successfully")
            elif return_message == "Partial failure!":
                encoded_content = response_data["data"]["content"]
                decoded_content = base64.b64decode(encoded_content).decode("utf-8")
                error_data = json.loads(decoded_content)
                error_message = error_data[0]["returnMessage"]
                frappe.throw(msg=error_message)
            else:
                log_integration_request('Failed', server_url, headers, data_to_post, response_data, return_message)
                frappe.throw(title="EFRIS API Error", msg=return_message)
                doc.docstatus = 0
                doc.save()

        except requests.exceptions.RequestException as e:
            log_integration_request('Failed', server_url, headers, data_to_post, {}, str(e))
            frappe.msgprint(f"Error making API request: {e}")
            doc.docstatus = 0
            doc.save()