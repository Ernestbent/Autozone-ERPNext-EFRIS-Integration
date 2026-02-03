import json
import base64
import uuid
from datetime import datetime, timezone, timedelta

import frappe
import requests

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

## East Africa Time (UTC+3)
EAT = timezone(timedelta(hours=3))

## Valid statuses for logging
VALID_STATUSES = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]

## Mapping for adjustment types
ADJUSTMENT_TYPE_MAPPING = {
    "Expired Goods": "101",
    "Damaged Goods": "102",
    "Personal Uses": "103",
    "Raw Materials": "105",
}

## Log integration requests to Integration Request doctype
def log_integration_request(status, url, headers, request_data, response_data, error=""):
    status = status if status in VALID_STATUSES else "Failed"
    frappe.get_doc({
        "doctype": "Integration Request",
        "integration_type": "Remote",
        "integration_request_service": "Stock Adjustment",
        "is_remote_request": 1,
        "method": "POST",
        "status": status,
        "url": url,
        "request_headers": json.dumps(headers, indent=2),
        "data": json.dumps(request_data, indent=2),
        "output": json.dumps(response_data, indent=2),
        "error": error,
        "execution_time": datetime.now(EAT).strftime("%Y-%m-%d %H:%M:%S"),
    }).insert(ignore_permissions=True)
    frappe.db.commit()

## Fetch EFRIS Settings
def get_efris_settings():
    settings = frappe.get_single("EFRIS Settings")
    if not settings.is_active:
        frappe.throw("EFRIS integration is disabled")
    if not settings.tin or not settings.brn:
        frappe.throw("TIN and BRN are required in EFRIS Settings")
    settings.brn = settings.brn.strip().lstrip("/")
    return settings

## Build goods stock item for payload
def build_goods_stock_item(item):
    return {
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

## Build stock adjustment payload for URA
def build_stock_adjustment_payload(doc, item, settings):
    adjust_type_code = ADJUSTMENT_TYPE_MAPPING.get(doc.custom_adjustment_type, "")
    if not adjust_type_code:
        frappe.throw("Invalid stock adjustment type")
    return {
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
        "goodsStockInItem": [build_goods_stock_item(item)],
    }

## Build full request payload for URA
def build_request_payload(settings, encrypted_content, signature, reference_no):
    return {
        "data": {
            "content": encrypted_content,
            "signature": signature,
            "dataDescription": {
                "codeType": "0",
                "encryptCode": "1",
                "zipCode": "0",
            },
        },
        "globalInfo": {
            "appId": "AP04",
            "version": "1.1.20191201",
            "dataExchangeId": uuid.uuid4().hex[:32],
            "interfaceCode": "T131",
            "requestCode": "TP",
            "requestTime": datetime.now(EAT).strftime("%Y-%m-%d %H:%M:%S"),
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": settings.device_number,
            "tin": settings.tin,
            "brn": settings.brn,
            "taxpayerID": settings.tin,
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "referenceNo": reference_no,
                "operatorName": "administrator",
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
            },
        },
        "returnStateInfo": {},
    }

## Main stock adjustment function triggered on document submission
def stock_adjust(doc, event):
    settings = get_efris_settings()
    headers = {"Content-Type": "application/json"}

    for item in doc.items:
        stock_payload = build_stock_adjustment_payload(doc, item, settings)
        encrypted = encrypt_dynamic_json(stock_payload)
        if not encrypted.get("success"):
            frappe.throw(encrypted.get("error"))

        request_payload = build_request_payload(
            settings=settings,
            encrypted_content=encrypted["encrypted_content"],
            signature=encrypted["signature"],
            reference_no=doc.name,
        )

        doc.custom_post_payload = json.dumps(request_payload, indent=2)

        try:
            response = requests.post(settings.server_url, json=request_payload, headers=headers, timeout=30)
            response.raise_for_status()
            response_data = response.json()

            doc.custom_response_payload = json.dumps(response_data, indent=2)
            return_message = response_data["returnStateInfo"].get("returnMessage")
            doc.custom_return_status = return_message

            if return_message == "SUCCESS":
                log_integration_request("Completed", settings.server_url, headers, request_payload, response_data)
                frappe.msgprint("Stock levels adjusted successfully")
            elif return_message == "Partial failure!":
                encoded = response_data["data"]["content"]
                decoded = base64.b64decode(encoded).decode()
                error_data = json.loads(decoded)
                error_message = error_data[0].get("returnMessage")
                log_integration_request("Failed", settings.server_url, headers, request_payload, response_data, error_message)
                frappe.throw(error_message)
            else:
                log_integration_request("Failed", settings.server_url, headers, request_payload, response_data, return_message)
                frappe.throw(return_message)

        except Exception as e:
            log_integration_request("Failed", settings.server_url, headers, request_payload, {}, str(e))
            frappe.throw(str(e))

    doc.save()
