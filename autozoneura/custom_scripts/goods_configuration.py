import frappe
import requests
import json
from datetime import datetime, timezone, timedelta
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Import from encryption module
from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json

# Define the East Africa Time (EAT) timezone, which is UTC+3
eat_timezone = timezone(timedelta(hours=3))

def log_integration_request(status, url, headers, data, response, error=""):
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"

    integration_request = frappe.get_doc({
        "doctype": "Integration Request",
        "integration_type": "Remote",
        "method": "POST",
        "integration_request_service": "Goods Upload",
        "is_remote_request": True,
        "status": status,
        "url": url,
        "request_headers": json.dumps(headers),
        "data": json.dumps(data),
        "output": json.dumps(response),
        "error": error,
        "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    })
    integration_request.insert(ignore_permissions=True)
    frappe.db.commit()

@frappe.whitelist()
def on_save(doc, event):

    if not doc.custom_efris_item:
        return

    # Load Single Doctype
    efris_settings = frappe.get_single("EFRIS Settings")

    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")

    server_url = efris_settings.server_url
    device_number = efris_settings.device_number
    tin = efris_settings.tin

    operation_type = doc.custom_registermodify_item

    # Build goods data
    goods_data = [{
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
        # "commodityGoodsExtendEntity": {
        #     "customsMeasureUnit": "",
        #     "customsUnitPrice":"",
        #     "packageScaledValueCustoms":"",
        #     "customsScaledValue":""
        # },
        "goodsOtherUnits": [],
    }]

    # Encrypt the payload
    encrypted_result = encrypt_dynamic_json(goods_data)

    if not encrypted_result.get("success"):
        frappe.throw(encrypted_result.get("error"))

    # Decrypt the encryted content locally
    try:
        aes_key_hex = efris_settings.aes_key
        if not aes_key_hex:
            frappe.throw("AES key not found in EFRIS Settings")

        aes_key_bytes = bytes.fromhex(aes_key_hex)
        encrypted_bytes = base64.b64decode(encrypted_result["encrypted_content"])
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        decrypted_json = json.loads(decrypted_bytes.decode("utf-8"))

    except Exception as e:
        print("Failed to decrypt encrypted content locally:", str(e))

    # Build final payload
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
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
            "deviceNo": device_number,
            "tin": tin,
            "brn": efris_settings.brn or "",
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

    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(server_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        response_data = response.json()

        if response_data.get("returnStateInfo", {}).get("returnMessage") == "SUCCESS":
            frappe.msgprint("Item successfully synced with EFRIS")
            log_integration_request("Completed", server_url, headers, payload, response_data)
        else:
            msg = response_data["returnStateInfo"].get("returnMessage", "Unknown error")
            log_integration_request("Failed", server_url, headers, payload, response_data, msg)
            frappe.throw(msg)

    except Exception as e:
        log_integration_request("Failed", server_url, headers, payload, {}, str(e))
        frappe.throw(str(e))
