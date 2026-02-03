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
    """Get EFRIS settings"""
    efris_settings = frappe.get_single("EFRIS Settings")
    if not efris_settings.is_active:
        frappe.throw(_("EFRIS Settings disabled"))
    if not efris_settings.device_number or not efris_settings.tin or not efris_settings.server_url:
        frappe.throw(_("EFRIS Settings are incomplete"))
    if not efris_settings.aes_key:
        frappe.throw(_("AES key not found"))
    return {
        "url": efris_settings.server_url,
        "tin": efris_settings.tin,
        "device_no": efris_settings.device_number,
        "brn": efris_settings.brn or "",
        "aes_key": efris_settings.aes_key
    }


def log_integration_request(status, url, headers, data, response, service=""):
    """Log integration request"""
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
    """Decrypt AES encrypted content from EFRIS"""
    try:
        aes_key_bytes = bytes.fromhex(aes_key)
        encrypted_data = base64.b64decode(encrypted_content)
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size)
        return json.loads(decrypted_data.decode('utf-8'))
    except Exception as e:
        frappe.log_error(f"Decryption error: {str(e)}", "EFRIS Decryption")
        frappe.throw(_("Failed to decrypt EFRIS response"))


def build_t128_request(payload, settings):
    """Build T128 request with encryption"""
    ## Encrypt payload
    encrypted_result = encrypt_dynamic_json(payload)
    if not encrypted_result.get("success"):
        frappe.throw(_(f"Encryption failed: {encrypted_result.get('error')}"))
    
    ## Generate unique IDs
    data_exchange_id = frappe.generate_hash(length=32)
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    ## Build request
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
            "dataExchangeId": data_exchange_id,
            "interfaceCode": "T128",
            "requestCode": "TP",
            "requestTime": current_time,
            "responseCode": "TA",
            "userName": "admin",
            "deviceMAC": "B47720524158",
            "deviceNo": settings["device_no"],
            "tin": settings["tin"],
            "brn": settings["brn"].strip().lstrip("/") if settings["brn"] else "",
            "taxpayerID": "999000002030357",
            "longitude": "32.61665",
            "latitude": "0.36601",
            "agentType": "0",
            "extendField": {
                "responseDateFormat": "dd/MM/yyyy",
                "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
                "referenceNo": frappe.generate_hash(length=14),
                "operatorName": frappe.session.user
            }
        },
        "returnStateInfo": {
            "returnCode": "",
            "returnMessage": ""
        }
    }


def send_efris_request(server_url, request_data):
    """Send request to EFRIS server"""
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(server_url, json=request_data, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        frappe.throw(_("EFRIS request timed out. Please try again."))
    except requests.exceptions.RequestException as e:
        frappe.throw(_(f"EFRIS request failed: {str(e)}"))


def get_efris_stock_all(settings):
    """Query ALL stock from EFRIS using T128 interface"""
    ## Build request payload for T128
    payload = {
        "pageNo": "1",
        "pageSize": "1000"
    }
    
    request_data = build_t128_request(payload, settings)
    
    ## Send request
    try:
        response_data = send_efris_request(settings['url'], request_data)
    except Exception as e:
        frappe.throw(_(f"EFRIS request failed: {str(e)}"))
    
    ## Log request
    log_integration_request('Completed', settings['url'], {}, request_data, response_data, "T128 Stock Query")
    
    ## Check response status
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
    
    if return_message != "SUCCESS":
        frappe.throw(_(f"EFRIS returned error: {return_message}"))
    
    ## Decrypt response
    data = response_data.get("data", {})
    encrypted_content = data.get("content", "")
    
    if not encrypted_content:
        frappe.throw(_("No data returned from EFRIS"))
    
    decrypted_data = decrypt_aes_content(encrypted_content, settings["aes_key"])
    
    ## Log decrypted response
    frappe.log_error(
        title="EFRIS Stock Response (Decrypted)",
        message=json.dumps(decrypted_data, indent=2)
    )
    
    return decrypted_data


@frappe.whitelist()
def validate_invoice_stock_before_efris(invoice_name):
    """
    Validate Sales Invoice items against EFRIS stock.
    
    Args:
        invoice_name: Sales Invoice name
    
    Returns:
        dict: Validation results with stock comparison
    """
    try:
        ## Get Sales Invoice
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
        
        if not invoice.items:
            frappe.throw(_("No items found in invoice"))
        
        ## Get EFRIS settings
        settings = get_efris_settings()
        
        ## Query ALL stock from EFRIS
        frappe.msgprint("Querying EFRIS stock...", alert=True)
        stock_response = get_efris_stock_all(settings)
        
        ## Build stock lookup from EFRIS response
        stock_lookup = {}
        records = stock_response.get('records', [])
        
        for record in records:
            goods_code = record.get('goodsCode', '')
            stock_qty = float(record.get('stock', 0))
            stock_lookup[goods_code] = {
                'stock': stock_qty,
                'goods_name': record.get('goodsName', ''),
                'unit_price': record.get('unitPrice', 0)
            }
        
        frappe.msgprint(f"Retrieved stock data for {len(stock_lookup)} items from EFRIS", alert=True)
        
        ## Build items map from invoice
        items_map = {}
        
        for item in invoice.items:
            ## Use item_code as the goods code (this is what matches EFRIS)
            goods_code = item.item_code
            
            if not goods_code:
                frappe.throw(_(f"Item {item.item_name} (Row {item.idx}) has no item code"))
            
            ## Store or update item info
            if goods_code in items_map:
                items_map[goods_code]['qty'] += item.qty
                items_map[goods_code]['rows'].append(item.idx)
            else:
                items_map[goods_code] = {
                    'item_name': item.item_name,
                    'qty': item.qty,
                    'rows': [item.idx]
                }
        
        ## Log what we found for debugging
        frappe.log_error(
            title="EFRIS Stock Lookup Map",
            message=f"Available goods codes in EFRIS:\n" + "\n".join([f"- {code}: {info['stock']} units" for code, info in list(stock_lookup.items())[:20]])
        )
        
        frappe.log_error(
            title="Invoice Items Map", 
            message=f"Items to validate:\n" + "\n".join([f"- {code}: {info['item_name']} (need {info['qty']})" for code, info in items_map.items()])
        )
        
        ## Validate each invoice item against EFRIS stock
        out_of_stock = []
        sufficient_stock = []
        missing_items = []
        
        for goods_code, item_info in items_map.items():
            item_name = item_info['item_name']
            required_qty = item_info['qty']
            
            ## Check if item exists in EFRIS
            if goods_code not in stock_lookup:
                missing_items.append({
                    'goods_code': goods_code,
                    'item_name': item_name,
                    'message': f"❌ {item_name} ({goods_code}) - NOT FOUND IN EFRIS"
                })
                continue
            
            ## Get available stock
            available_stock = stock_lookup[goods_code]['stock']
            
            ## Check if sufficient
            if available_stock < required_qty:
                shortage = required_qty - available_stock
                out_of_stock.append({
                    'goods_code': goods_code,
                    'item_name': item_name,
                    'required': required_qty,
                    'available': available_stock,
                    'shortage': shortage,
                    'message': f"❌ {item_name} ({goods_code}): Need {required_qty}, Available {available_stock}, Short by {shortage}"
                })
            else:
                sufficient_stock.append({
                    'goods_code': goods_code,
                    'item_name': item_name,
                    'required': required_qty,
                    'available': available_stock,
                    'message': f"✅ {item_name} ({goods_code}): Available {available_stock} (need {required_qty})"
                })
        
        ## Display results
        if sufficient_stock:
            for item in sufficient_stock:
                frappe.msgprint(item['message'], indicator='green')
        
        ## If any items missing or out of stock, throw error
        if missing_items or out_of_stock:
            error_messages = []
            
            if missing_items:
                error_messages.append("<b>Items Not Found in EFRIS:</b>")
                for item in missing_items:
                    error_messages.append(f"• {item['message']}")
                error_messages.append("")
            
            if out_of_stock:
                error_messages.append("<b>Insufficient Stock:</b>")
                for item in out_of_stock:
                    error_messages.append(f"• {item['message']}")
                error_messages.append("")
            
            error_messages.append("<b>Action Required:</b>")
            error_messages.append("Please update stock quantities in EFRIS before submitting this invoice.")
            
            frappe.throw("<br>".join(error_messages), title="❌ Stock Validation Failed")
        
        ## All items have sufficient stock
        success_message = f"""
        <div style="padding: 15px; background-color: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px;">
            <h4 style="color: #155724; margin-top: 0;">✅ Stock Validation Passed!</h4>
            <p style="margin: 10px 0;">All {len(sufficient_stock)} items have sufficient stock in EFRIS.</p>
            <p style="margin: 10px 0;"><b>Invoice {invoice_name} is ready for submission.</b></p>
        </div>
        """
        
        frappe.msgprint(success_message, title="Stock Validation Success", indicator='green')
        
        return {
            "success": True,
            "invoice_name": invoice_name,
            "total_items": len(items_map),
            "sufficient_stock": len(sufficient_stock),
            "out_of_stock": len(out_of_stock),
            "missing_items": len(missing_items),
            "details": {
                "sufficient": sufficient_stock,
                "insufficient": out_of_stock,
                "missing": missing_items
            }
        }
        
    except Exception as e:
        frappe.log_error(str(e), "EFRIS Stock Validation Error")
        frappe.throw(str(e))