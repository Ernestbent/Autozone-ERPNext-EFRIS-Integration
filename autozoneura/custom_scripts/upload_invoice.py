import json
import base64
import uuid
from datetime import datetime, timezone, timedelta
import frappe
import requests

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

# East Africa Time (EAT) timezone
eat_timezone = timezone(timedelta(hours=3))  # UTC+3

def log_integration_request(status, url, headers, data, response, error=""):
    """Log integration request to Integration Request doctype"""
    valid_statuses = ["", "Queued", "Authorized", "Completed", "Cancelled", "Failed"]
    status = status if status in valid_statuses else "Failed"
    
    integration_request = frappe.get_doc({
        "doctype": "Integration Request",
        "integration_type": "Remote",
        "method": "POST",
        "integration_request_service": "Goods Upload/Credit Note Issue",
        "is_remote_request": True,
        "status": status,
        "url": url,
        "request_headers": json.dumps(headers, indent=4),
        "data": json.dumps(data, indent=4),
        "output": json.dumps(response, indent=4),
        "error": error,
        "execution_time": datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    })
    integration_request.insert(ignore_permissions=True)
    frappe.db.commit()

def on_send(doc, event):
    """Hook for Sales Invoice submission to EFRIS"""
    if not doc.custom_efris_invoice or doc.is_return:
        return
    
    # Get datetime
    datetime_combined = f"{doc.posting_date} {doc.posting_time}"
    
    # Fetch EFRIS Settings for current company (single doctype)
    company = frappe.defaults.get_user_default("company")
    if not company:
        frappe.throw("No default company set for the current session")

    # Direct fetch for single EFRIS Settings doctype by company
    efris_settings = frappe.get_doc("EFRIS Settings", {"company": company})
    
    # Check if EFRIS integration is active
    if not efris_settings.is_active:
        frappe.throw("EFRIS integration is disabled")
    
    # Get EFRIS configuration
    device_number = efris_settings.device_number
    tin = efris_settings.tin
    server_url = efris_settings.server_url
    legal_name = efris_settings.legal_name
    business_name = efris_settings.business_name
    brn = efris_settings.brn
    email_phone = efris_settings.email_phone
    mobile_phone = efris_settings.mobile_phone
    line_phone = efris_settings.line_phone

    # Validate required fields
    if not tin or not brn:
        frappe.throw("TIN and BRN are required in EFRIS Settings")
    
    # Clean BRN - remove leading slash and any whitespace
    brn = brn.strip().lstrip("/") if brn else ""

    # Process items
    goods_details = []
    tax_categories = {}
    item_count = 0

    for item in doc.items:
        item_count += 1

        # Determine tax rate and category
        if item.item_tax_template.startswith("Exempt"):
            tax_rate = "-"
            tax_category_code = "03"
            tax = 0
            grossAmount = item.amount
            taxAmount = 0
            netAmount = item.amount
        elif item.item_tax_template.startswith("Zero"):
            tax_rate = 0
            tax_category_code = "02"
            tax = 0
            grossAmount = item.amount
            taxAmount = 0
            netAmount = item.amount
        else:
            tax_category_code = "01"
            tax_rate = "0.18"
            tax = round((item.amount - item.net_amount), 3)
            grossAmount = item.amount
            taxAmount = round((item.amount - item.net_amount), 3)
            netAmount = round((grossAmount - tax), 3)

        # Update or create tax category
        if item.item_tax_template in tax_categories:
            tax_categories[item.item_tax_template]["grossAmount"] += item.amount
            tax_categories[item.item_tax_template]["taxAmount"] += taxAmount
            tax_categories[item.item_tax_template]["netAmount"] += netAmount
        else:
            tax_categories[item.item_tax_template] = {
                "taxCategoryCode": tax_category_code,
                "netAmount": netAmount,
                "taxRate": tax_rate,
                "taxAmount": taxAmount,
                "grossAmount": item.amount,
                "exciseUnit": "",
                "exciseCurrency": "",
                "taxRateName": "",
            }

        # Add goods detail
        goods_detail = {
            "item": item.item_name,
            "itemCode": item.item_code,
            "qty": item.qty,
            "unitOfMeasure": item.custom_uom_codeefris,
            "unitPrice": item.rate,
            "total": item.amount,
            "taxRate": tax_rate,
            "tax": tax,
            "discountTotal": "",
            "discountTaxRate": "",
            "orderNumber": str(len(goods_details)),
            "discountFlag": "2", 
            "deemedFlag": "2",
            "exciseFlag": "2",
            "categoryId": "",
            "categoryName": "",
            "goodsCategoryId": item.custom_goods_category_id,
            "goodsCategoryName": "",
            "exciseRate": "",
            "exciseRule": "",
            "exciseTax": "",
            "pack": "",
            "stick": "",
            "exciseUnit": "",
            "exciseCurrency": "",
            "exciseRateName": "",
            "vatApplicableFlag": "1",
            "deemedExemptCode": "",
            "vatProjectId": "",
            "vatProjectName": "",
            "totalWeight": "",
            "hsCode": "",
            "hsName": "",
            "pieceQty": "",
            "pieceMeasureUnit": "",
            "highSeaBondFlag": "2",
            "highSeaBondCode": "",
            "highSeaBondNo": "",
        }
        goods_details.append(goods_detail)

    # Validate items
    if not goods_details:
        frappe.throw("No items found in the invoice")

    # Round tax category values
    for category in tax_categories.values():
        category["netAmount"] = round(category["netAmount"], 3)
        category["taxAmount"] = round(category["taxAmount"], 3)

    tax_categories_list = list(tax_categories.values())
    total_tax_amount = sum(tax_category["taxAmount"] for tax_category in tax_categories_list)

    # Buyer type mapping
    buyer_categories = {
        "B2B": "0",
        "B2C": "1",
        "Foreigner": "2",
        "B2G": "3"
    }
    buyer_types = buyer_categories.get(doc.customer_group, "1")  # Default to B2C if not found

    # Build invoice data structure (NOT a list - single object)
    invoice_data = {
        "sellerDetails": {
            "tin": tin,
            "ninBrn": brn,
            "legalName": legal_name,
            "businessName": business_name,
            "address": doc.company_address or "",
            "mobilePhone": mobile_phone,
            "linePhone": line_phone,
            "emailAddress": email_phone,
            "placeOfBusiness": doc.company_address or "",
            "referenceNo": doc.name,
            "branchId": "",
            "isCheckReferenceNo": "",
        },
        "basicInformation": {
            "invoiceNo": "",
            "antifakeCode": "",
            "deviceNo": device_number,
            "issuedDate": datetime_combined,
            "operator": legal_name,
            "currency": "UGX",
            "oriInvoiceId": "",
            "invoiceType": "1",
            "invoiceKind": "1",
            "dataSource": "105",
            "invoiceIndustryCode": "101",
            "isBatch": "0",
        },
        "buyerDetails": {
            "buyerTin": doc.tax_id or "1000000001",
            "buyerNinBrn": "",
            "buyerPassportNum": "",
            "buyerLegalName": doc.customer_name or "",
            "buyerBusinessName": doc.customer_name or "",
            "buyerAddress": doc.customer_address or "",
            "buyerEmail": doc.contact_email or "",
            "buyerMobilePhone": doc.contact_mobile or "",
            "buyerLinePhone": "",
            "buyerPlaceOfBusi": "",
            "buyerType": buyer_types,
            "buyerCitizenship": "",
            "buyerSector": "1",
            "buyerReferenceNo": "",
            "nonResidentFlag": "0",
            "deliveryTermsCode": ""
        },
        "buyerExtend": {
            "propertyType": "",
            "district": "",
            "municipalityCounty": "",
            "divisionSubcounty": "",
            "town": "",
            "cellVillage": "",
            "effectiveRegistrationDate": "",
            "meterStatus": "",
        },
        "goodsDetails": goods_details,
        "taxDetails": tax_categories_list,
        "summary": {
            "netAmount": round((doc.total - total_tax_amount), 3),
            "taxAmount": round(total_tax_amount, 3),
            "grossAmount": round(doc.total, 3),
            "itemCount": item_count,
            "modeCode": "0",
            "remarks": "We appreciate your continued support",
            "qrCode": "",
        },
        "extend": {
            "reason": "",
            "reasonCode": ""
        },
        "importServicesSeller": {
            "importBusinessName": "",
            "importEmailAddress": "",
            "importContactNumber": "",
            "importAddress": "",
            "importInvoiceDate": "",
            "importAttachmentName": "",
            "importAttachmentContent": "",
        },
        "airlineGoodsDetails": [{
            "item": "",
            "itemCode": "",
            "qty": "",
            "unitOfMeasure": "",
            "unitPrice": "",
            "total": "",
            "taxRate": "",
            "tax": "",
            "discountTotal": "",
            "discountTaxRate": "",
            "orderNumber": "",
            "discountFlag": "",
            "deemedFlag": "",
            "exciseFlag": "",
            "categoryId": "",
            "categoryName": "",
            "goodsCategoryId": "",
            "goodsCategoryName": "",
            "exciseRate": "",
            "exciseRule": "",
            "exciseTax": "",
            "pack": "1",
            "stick": "",
            "exciseUnit": "",
            "exciseCurrency": "",
            "exciseRateName": "",
        }],
        "edcDetails": {
            "tankNo": "",
            "pumpNo": "",
            "nozzleNo": "",
            "controllerNo": "",
            "acquisitionEquipmentNo": "",
            "levelGaugeNo": "",
            "mvrn": "",
        },
    }

    # Log invoice data before encryption for debugging
    frappe.log_error(
        title="EFRIS Invoice Data Before Encryption",
        message=json.dumps(invoice_data, indent=2)
    )

    # Encrypt invoice data
    encrypted_result = encrypt_dynamic_json(invoice_data)
    if not encrypted_result.get("success"):
        frappe.throw(f"Encryption failed: {encrypted_result.get('error')}")

    # Generate unique data exchange ID
    data_exchange_id = uuid.uuid4().hex[:32]
    current_time = datetime.now(eat_timezone).strftime("%Y-%m-%d %H:%M:%S")
    
    # Prepare item description for extendField (max 100 chars)
    item_description = ", ".join([item["item"] for item in goods_details[:3]])[:100]
    
    # Prepare POST data
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
            "interfaceCode": "T109",
            "requestCode": "TP",
            "requestTime": current_time,
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
                "referenceNo": doc.name,
                "operatorName": legal_name,
                "itemDescription": item_description,
                "currency": "UGX",
                "grossAmount": str(round(doc.total, 2)),
                "taxAmount": str(round(total_tax_amount, 2))
            },
        },
        "returnStateInfo": {
            "returnCode": "",
            "returnMessage": ""
        },
    }
    
    # Assign request to sales invoice
    doc.custom_post_request = json.dumps(data_to_post, indent=4)

    try:
        # Make POST request to EFRIS API
        headers = {"Content-Type": "application/json"}
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        response.raise_for_status()

        # Parse response
        response_data = response.json()
        doc.custom_response = json.dumps(response_data, indent=4)
        
        return_message = response_data["returnStateInfo"]["returnMessage"]
        doc.custom_return_status = return_message
        
        # Handle response
        if response.status_code == 200 and return_message == "SUCCESS":
            frappe.msgprint("Sales Invoice successfully submitted to EFRIS URA.")

            # Decrypt response content
            encrypted_content = response_data["data"]["content"]
            
            try:
                decrypted_content = decrypt_string(encrypted_content)
            except Exception as decrypt_error:
                frappe.log_error(
                    title="EFRIS Decryption Error",
                    message=f"Decryption failed: {decrypt_error}"
                )
                decrypted_content = base64.b64decode(encrypted_content).decode("utf-8")

            data = json.loads(decrypted_content)

            # Update sales invoice with EFRIS response data
            doc.custom_device_number = data.get("basicInformation", {}).get("deviceNo")
            doc.custom_verification_code = data.get("basicInformation", {}).get("antifakeCode")
            doc.custom_fdn = data.get("basicInformation", {}).get("invoiceNo")
            doc.custom_qr_code = data.get("summary", {}).get("qrCode")
            doc.custom_invoice_number = data.get("basicInformation", {}).get("invoiceId")
            doc.custom_brn = data.get("sellerDetails", {}).get("ninBrn")
            doc.custom_company_email_id = data.get("sellerDetails", {}).get("emailAddress")
            doc.custom_served_by = data.get("basicInformation", {}).get("operator")
            doc.custom_legal_name = data.get("sellerDetails", {}).get("legalName")
            doc.custom_companys_address = data.get("sellerDetails", {}).get("address")

            # Log successful request
            log_integration_request('Completed', server_url, headers, data_to_post, response_data)
            doc.save()
        else:
            log_integration_request('Failed', server_url, headers, data_to_post, response_data, return_message)
            frappe.throw(title="Oops! API Error", msg=return_message)

    except requests.exceptions.Timeout:
        error_msg = "Request timed out. Please try again."
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except requests.exceptions.RequestException as e:
        # Log failed request
        error_msg = f"API request failed: {str(e)}"
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        frappe.throw(error_msg)
    except Exception as e:
        # Log any other errors
        error_msg = f"Unexpected error: {str(e)}"
        frappe.log_error(
            title="EFRIS Submission Error",
            message=error_msg
        )
        frappe.throw(error_msg)