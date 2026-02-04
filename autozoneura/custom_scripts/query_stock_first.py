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


def build_t127_request(payload, settings):
    """Build T127 request with encryption - T127 returns all configured items"""
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
            "interfaceCode": "T127",  ## T127 returns all configured items
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
    """Query ALL configured items from EFRIS using T127 interface"""
    ## Build request payload for T127
    payload = {
        "pageNo": "",
        "pageSize": ""
    }
    
    request_data = build_t127_request(payload, settings)
    
    ## Send request
    try:
        response_data = send_efris_request(settings['url'], request_data)
    except Exception as e:
        frappe.throw(_(f"EFRIS request failed: {str(e)}"))
    
    ## Log request
    log_integration_request('Completed', settings['url'], {}, request_data, response_data, "T127 Stock Query")
    
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
    
    return decrypted_data


@frappe.whitelist()
def validate_invoice_stock_before_efris(invoice_name):
    """
    Validate Sales Invoice items against EFRIS stock.
    Uses item_code from Sales Invoice Item as goodsCode for EFRIS lookup.
    
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
        
        frappe.msgprint(f"Retrieved stock for {len(stock_lookup)} items from EFRIS", alert=True)
        
        ## Build items map from invoice (using item_code as goodsCode)
        items_map = {}
        
        for item in invoice.items:
            ## Use item_code as the goods code
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
                    'required': required_qty
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
                    'shortage': shortage
                })
            else:
                sufficient_stock.append({
                    'goods_code': goods_code,
                    'item_name': item_name,
                    'required': required_qty,
                    'available': available_stock
                })
        
        ## Display success messages
        if sufficient_stock:
            success_items = "<br>".join([
                f"✅ {item['goods_code']}: <span style='color: green; font-weight: bold;'>{item['available']}</span> available"
                for item in sufficient_stock
            ])
            frappe.msgprint(success_items, title="Validation Passed", indicator='green')
        
        ## If any items missing or out of stock, throw error
        if missing_items or out_of_stock:
            error_details = []
            
            if missing_items:
                error_details.append("<h5>Items Not Found in EFRIS:</h5>")
                error_details.append("<table class='table table-bordered'><tr><th>Item Code</th><th>Item Name</th><th>Required Qty</th></tr>")
                for item in missing_items:
                    error_details.append(f"<tr><td>{item['goods_code']}</td><td>{item['item_name']}</td><td style='color: green; font-weight: bold;'>{item['required']}</td></tr>")
                error_details.append("</table><br>")
            
            if out_of_stock:
                error_details.append("<h5>Insufficient Stock:</h5>")
                error_details.append("<table class='table table-bordered'><tr><th>Item Code</th><th>Item Name</th><th>Required</th><th>Available</th><th>Short</th></tr>")
                for item in out_of_stock:
                    error_details.append(f"<tr><td>{item['goods_code']}</td><td>{item['item_name']}</td><td style='color: green; font-weight: bold;'>{item['required']}</td><td style='color: orange; font-weight: bold;'>{item['available']}</td><td style='color:red; font-weight: bold;'>{item['shortage']}</td></tr>")
                error_details.append("</table><br>")
            
            error_details.append("<p><b>Action Required:</b> Update stock in EFRIS before submitting.</p>")
            
            frappe.throw("".join(error_details), title="Stock Validation Failed")
        
        ## All items have sufficient stock
        # success_message = f"""
        # <div style="padding: 15px; background-color: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px;">
        #     <h4 style="color: #155724; margin-top: 0;">✅ Stock Validation Passed!</h4>
        #     <p>All {len(sufficient_stock)} item(s) have sufficient stock in EFRIS.</p>
        #     <p><b>Invoice {invoice_name} is ready for submission.</b></p>
        # </div>
        # """
        
        # frappe.msgprint(success_message, title="Validation Success", indicator='green')
        
        return {
            "success": True,
            "invoice_name": invoice_name,
            "total_items": len(items_map),
            "sufficient_stock": len(sufficient_stock),
            "out_of_stock": len(out_of_stock),
            "missing_items": len(missing_items)
        }
        
    except Exception as e:
        frappe.log_error(str(e), "Stock Validation Error")
        frappe.throw(str(e))