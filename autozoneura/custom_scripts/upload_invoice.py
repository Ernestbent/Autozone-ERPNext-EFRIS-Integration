import json
import base64
import uuid
from datetime import datetime, timezone, timedelta
import frappe
import requests

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string
from autozoneura.custom_scripts.issue_credit_note import process_credit_note
## East Africa Time (UTC+3)
EAT_TIMEZONE = timezone(timedelta(hours=3))

## Tax category codes
TAX_CATEGORY_CODES = {
    "EXEMPT": "03",
    "ZERO": "02",
    "STANDARD": "01"
}

## Buyer type mapping
BUYER_TYPE_MAPPING = {
    "B2B": "0",
    "B2C": "1",
    "Foreigner": "2",
    "B2G": "3"
}

## Default values
DEFAULT_BUYER_TYPE = "1"  # B2C


class EFRISIntegrationError(Exception):
    """Custom exception for EFRIS integration errors"""
    pass


def log_integration_request(status, url, headers, data, response, error=""):
    """
    Log integration request to Integration Request doctype
    
    """
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
        "execution_time": datetime.now(EAT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    })
    integration_request.insert(ignore_permissions=True)
    frappe.db.commit()


def get_efris_settings():
    """
    Fetch and validate EFRIS settings for current company

    """
    company = frappe.defaults.get_user_default("company")
    if not company:
        raise EFRISIntegrationError("No default company set for the current session")

    efris_settings = frappe.get_doc("EFRIS Settings", {"company": company})
    
    if not efris_settings.is_active:
        raise EFRISIntegrationError("EFRIS integration is disabled")
    
    if not efris_settings.tin or not efris_settings.brn:
        raise EFRISIntegrationError("TIN and BRN are required in EFRIS Settings")
    
    return efris_settings


def clean_brn(brn):
    """Remove leading slash and whitespace from BRN"""
    return brn.strip().lstrip("/") if brn else ""


def determine_tax_details(item_tax_template):
    """
    Determine tax rate and category based on tax template
    
    """
    if item_tax_template.startswith("Exempt"):
        return {
            "tax_rate": "-",
            "tax_category_code": TAX_CATEGORY_CODES["EXEMPT"],
            "is_exempt": True
        }
    elif item_tax_template.startswith("Zero"):
        return {
            "tax_rate": 0,
            "tax_category_code": TAX_CATEGORY_CODES["ZERO"],
            "is_exempt": True
        }
    else:
        return {
            "tax_rate": "0.18",
            "tax_category_code": TAX_CATEGORY_CODES["STANDARD"],
            "is_exempt": False
        }


def calculate_item_amounts(item, tax_details):
    """
    Calculate tax and net amounts for an item
    
    """
    if tax_details["is_exempt"]:
        tax = 0
        grossAmount = item.amount
        taxAmount = 0
        netAmount = item.amount
    else:
        ## For standard tax (18%)
        tax_category_code = "01"
        tax_rate = "0.18"
        tax = round((item.amount - item.net_amount), 3)
        grossAmount = item.amount
        taxAmount = round((item.amount - item.net_amount), 3)
        netAmount = round((grossAmount - tax), 3)
    
    return {
        "tax": tax,
        "grossAmount": grossAmount,
        "taxAmount": taxAmount,
        "netAmount": netAmount
    }


def build_goods_detail(item, amounts, tax_details, order_number):
    """
    Build goods detail object for a single item
    
    """
    return {
        "item": item.item_name,
        "itemCode": item.item_code,
        "qty": item.qty,
        "unitOfMeasure": item.custom_uom_codeefris,
        "unitPrice": item.rate,
        "total": item.amount,
        "taxRate": tax_details["tax_rate"],
        "tax": amounts["tax"],
        "discountTotal": "",
        "discountTaxRate": "",
        "orderNumber": str(order_number),
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


def process_invoice_items(items):
    """
    Process all invoice items and build goods details and tax categories
    
    """
    goods_details = []
    tax_categories = {}
    item_count = 0

    for item in items:
        item_count += 1
        
        ## Determine tax details
        tax_details = determine_tax_details(item.item_tax_template)
        
        ## Calculate amounts
        amounts = calculate_item_amounts(item, tax_details)
        
        ## Extract calculated values
        tax = amounts["tax"]
        grossAmount = amounts["grossAmount"]
        taxAmount = amounts["taxAmount"]
        netAmount = amounts["netAmount"]
        
        ## Update or create tax category
        if item.item_tax_template in tax_categories:
            tax_categories[item.item_tax_template]["grossAmount"] += item.amount
            tax_categories[item.item_tax_template]["taxAmount"] += taxAmount
            tax_categories[item.item_tax_template]["netAmount"] += netAmount
        else:
            tax_categories[item.item_tax_template] = {
                "taxCategoryCode": tax_details["tax_category_code"],
                "netAmount": netAmount,
                "taxRate": tax_details["tax_rate"],
                "taxAmount": taxAmount,
                "grossAmount": item.amount,
                "exciseUnit": "",
                "exciseCurrency": "",
                "taxRateName": "",
            }
        
        ## Build goods detail
        goods_detail = build_goods_detail(item, amounts, tax_details, len(goods_details))
        goods_details.append(goods_detail)

    ## Round tax category values
    for category in tax_categories.values():
        category["netAmount"] = round(category["netAmount"], 3)
        category["taxAmount"] = round(category["taxAmount"], 3)

    return goods_details, list(tax_categories.values()), item_count

def build_seller_details(efris_settings, doc):
    """Build seller details section"""
    return {
        "tin": efris_settings.tin,
        "ninBrn": clean_brn(efris_settings.brn),
        "legalName": efris_settings.legal_name,
        "businessName": efris_settings.business_name,
        "address": "999 MBOGO ROAD OPPOSITE MBOGO COLLEGE KAWEMPE KAMPALA KAWEMPE DIVISION NORTH KAWEMPE DIVISION KAWEMPE 1",
        "mobilePhone": efris_settings.mobile_phone,
        "linePhone": efris_settings.line_phone,
        "emailAddress": efris_settings.email_phone,
        "placeOfBusiness": efris_settings.place_of_business,
        "referenceNo": doc.name,
        "branchId": "",
        "isCheckReferenceNo": "",
    }


def build_basic_information(efris_settings, doc, datetime_combined):
    """Build basic information section"""

    ## Get full name of document owner
    owner_full_name = frappe.db.get_value(
        "User",
        doc.owner,
        "full_name"
    ) or doc.owner

    return {
        "invoiceNo": "",
        "antifakeCode": "",
        "deviceNo": efris_settings.device_number,
        "issuedDate": datetime_combined,
        "operator": owner_full_name,
        "currency": "UGX",
        "oriInvoiceId": "",
        "invoiceType": "1",
        "invoiceKind": "1",
        "dataSource": "105",
        "invoiceIndustryCode": "101",
        "isBatch": "0",
    }

def build_buyer_details(doc):
    """Build buyer details section"""
    buyer_type = BUYER_TYPE_MAPPING.get(doc.customer_group, DEFAULT_BUYER_TYPE)
    
    return {
        "buyerTin": doc.tax_id,
        "buyerNinBrn": "",
        "buyerPassportNum": "",
        "buyerLegalName": doc.customer_name or "",
        "buyerBusinessName": doc.customer_name or "",
        "buyerAddress": doc.customer_address or "",
        "buyerEmail": doc.contact_email or "",
        "buyerMobilePhone": doc.contact_mobile or "",
        "buyerLinePhone": "",
        "buyerPlaceOfBusi": "",
        "buyerType": buyer_type,
        "buyerCitizenship": "",
        "buyerSector": "1",
        "buyerReferenceNo": "",
        "nonResidentFlag": "0",
        "deliveryTermsCode": ""
    }


def build_buyer_extend():
    """Build buyer extend section (mostly empty)"""
    return {
        "propertyType": "",
        "district": "",
        "municipalityCounty": "",
        "divisionSubcounty": "",
        "town": "",
        "cellVillage": "",
        "effectiveRegistrationDate": "",
        "meterStatus": "",
    }


def build_summary(doc, total_tax_amount, item_count):
    """Build summary section"""
    return {
        "netAmount": round(doc.total - total_tax_amount, 3),
        "taxAmount": round(total_tax_amount, 3),
        "grossAmount": round(doc.total, 3),
        "itemCount": item_count,
        "modeCode": "0",
        "remarks": "We appreciate your continued support",
        "qrCode": "",
    }


def build_extend():
    """Build extend section (mostly empty)"""
    return {
        "reason": "",
        "reasonCode": ""
    }


def build_import_services_seller():
    """Build import services seller section (mostly empty)"""
    return {
        "importBusinessName": "",
        "importEmailAddress": "",
        "importContactNumber": "",
        "importAddress": "",
        "importInvoiceDate": "",
        "importAttachmentName": "",
        "importAttachmentContent": "",
    }


def build_airline_goods_details():
    """Build airline goods details section (mostly empty)"""
    return [{
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
    }]


def build_edc_details():
    """Build EDC details section (mostly empty)"""
    return {
        "tankNo": "",
        "pumpNo": "",
        "nozzleNo": "",
        "controllerNo": "",
        "acquisitionEquipmentNo": "",
        "levelGaugeNo": "",
        "mvrn": "",
    }


def build_invoice_data(efris_settings, doc, datetime_combined):
    """
    Build complete invoice data structure for EFRIS submission
    
    """
    ## Process items and get goods details and tax categories
    goods_details, tax_categories_list, item_count = process_invoice_items(doc.items)
    
    if not goods_details:
        raise EFRISIntegrationError("No items found in the invoice")
    
    ## Calculate total tax amount
    total_tax_amount = sum(tax_category["taxAmount"] for tax_category in tax_categories_list)
    
    ## Build invoice data structure
    invoice_data = {
        "sellerDetails": build_seller_details(efris_settings, doc),
        "basicInformation": build_basic_information(efris_settings, doc, datetime_combined),
        "buyerDetails": build_buyer_details(doc),
        "buyerExtend": build_buyer_extend(),
        "goodsDetails": goods_details,
        "taxDetails": tax_categories_list,
        "summary": build_summary(doc, total_tax_amount, item_count),
        "extend": build_extend(),
        "importServicesSeller": build_import_services_seller(),
        "airlineGoodsDetails": build_airline_goods_details(),
        "edcDetails": build_edc_details(),
    }
    
    return invoice_data, total_tax_amount, item_count


def build_global_info(efris_settings, doc, total_tax_amount, goods_details):
    data_exchange_id = uuid.uuid4().hex[:32]
    current_time = datetime.now(EAT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    ## Get document owner full name
    owner_full_name = frappe.db.get_value(
        "User",
        doc.owner,
        "full_name"
    ) or doc.owner

    item_description = ", ".join([item["item"] for item in goods_details[:3]])[:100]

    return {
        "appId": "AP04",
        "version": "1.1.20191201",
        "dataExchangeId": data_exchange_id,
        "interfaceCode": "T109",
        "requestCode": "TP",
        "requestTime": current_time,
        "responseCode": "TA",
        "userName": "admin",
        "deviceMAC": "B47720524158",
        "deviceNo": efris_settings.device_number,
        "tin": efris_settings.tin,
        "brn": clean_brn(efris_settings.brn),
        "taxpayerID": "999000002030357",
        "longitude": "32.61665",
        "latitude": "0.36601",
        "agentType": "0",
        "extendField": {
            "responseDateFormat": "dd/MM/yyyy",
            "responseTimeFormat": "dd/MM/yyyy HH:mm:ss",
            "referenceNo": doc.name,
            "operatorName": owner_full_name,  ## FIXED
            "itemDescription": item_description,
            "currency": "UGX",
            "grossAmount": str(round(doc.total, 2)),
            "taxAmount": str(round(total_tax_amount, 2)),
        },
    }


def encrypt_invoice_data(invoice_data):
    """
    Encrypt invoice data using encryption service
  
    """
    ## Log invoice data before encryption
    frappe.log_error(
        title="EFRIS Invoice Data Before Encryption",
        message=json.dumps(invoice_data, indent=2)
    )
    
    ## Encrypt invoice data
    encrypted_result = encrypt_dynamic_json(invoice_data)
    if not encrypted_result.get("success"):
        raise EFRISIntegrationError(f"Encryption failed: {encrypted_result.get('error')}")
    
    return encrypted_result


def build_post_data(encrypted_result, global_info):
    """
    Build complete POST data for API request

    """
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
        "globalInfo": global_info,
        "returnStateInfo": {
            "returnCode": "",
            "returnMessage": ""
        },
    }


def decrypt_response_content(encrypted_content):
    """
    Decrypt EFRIS response content

    """
    try:
        decrypted_content = decrypt_string(encrypted_content)
    except Exception as decrypt_error:
        frappe.log_error(
            title="EFRIS Decryption Error",
            message=f"Decryption failed: {decrypt_error}"
        )
        ## Fallback to base64 decoding
        decrypted_content = base64.b64decode(encrypted_content).decode("utf-8")
    
    return json.loads(decrypted_content)


def update_invoice_with_response(doc, decrypted_data):
    """
    Update sales invoice with EFRIS response data
   
    """
    doc.custom_device_number = decrypted_data.get("basicInformation", {}).get("deviceNo")
    doc.custom_verification_code = decrypted_data.get("basicInformation", {}).get("antifakeCode")
    doc.custom_fdn = decrypted_data.get("basicInformation", {}).get("invoiceNo")
    doc.custom_qr_code = decrypted_data.get("summary", {}).get("qrCode")
    doc.custom_invoice_number = decrypted_data.get("basicInformation", {}).get("invoiceId")
    doc.custom_brn = decrypted_data.get("sellerDetails", {}).get("ninBrn")
    doc.custom_company_email_id = decrypted_data.get("sellerDetails", {}).get("emailAddress")
    doc.custom_served_by = decrypted_data.get("basicInformation", {}).get("operator")
    doc.custom_legal_name = decrypted_data.get("sellerDetails", {}).get("legalName")
    doc.custom_companys_address = decrypted_data.get("sellerDetails", {}).get("address")


def submit_to_efris(efris_settings, data_to_post):
    """
    Submit invoice data to EFRIS API
    
    """
    headers = {"Content-Type": "application/json"}
    server_url = efris_settings.server_url
    
    try:
        response = requests.post(server_url, json=data_to_post, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json(), headers, server_url
        
    except requests.exceptions.Timeout:
        error_msg = "Request timed out. Please try again."
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        raise EFRISIntegrationError(error_msg)
        
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        log_integration_request('Failed', server_url, headers, data_to_post, {}, error_msg)
        raise EFRISIntegrationError(error_msg)


def handle_efris_response(doc, response_data, headers, server_url, data_to_post):
    """
    Handle EFRIS API response
    
    """
    ## Store response in document
    doc.custom_response = json.dumps(response_data, indent=4)
    
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
    doc.custom_return_status = return_message
    
    ## Check if successful
    if return_message == "SUCCESS":
        frappe.msgprint("Sales Invoice successfully submitted to EFRIS URA.")
        
        ## Decrypt and extract response data
        encrypted_content = response_data.get("data", {}).get("content", "")
        decrypted_data = decrypt_response_content(encrypted_content)
        
        ## Update invoice with response data
        update_invoice_with_response(doc, decrypted_data)
        
        ## Log successful request
        log_integration_request('Completed', server_url, headers, data_to_post, response_data)
        doc.save()
    else:
        ## Log failed request
        log_integration_request('Failed', server_url, headers, data_to_post, response_data, return_message)
        frappe.throw(title="Oops! API Error", msg=return_message)


def on_send(doc, event):
    """
    Main entry point for ALL EFRIS submissions
    Routes invoices to T109 and credit notes to T110
    """
    ## Skip if not EFRIS invoice
    # if not doc.custom_efris_invoice:
    #     return
    
    ## Route based on document type
    if doc.is_return:
        # No import here anymore - already imported at top
        process_credit_note(doc, event)
        return
    
    # Only process regular invoices below
    process_regular_invoice(doc)


def process_regular_invoice(doc):
    """
    Process T109 regular invoice submission
    """
    try:
        ## Get EFRIS settings
        efris_settings = get_efris_settings()
        
        ## Prepare datetime
        datetime_combined = f"{doc.posting_date} {doc.posting_time}"
        
        ## Build invoice data
        invoice_data, total_tax_amount, item_count = build_invoice_data(efris_settings, doc, datetime_combined)
        
        ## Encrypt invoice data
        encrypted_result = encrypt_invoice_data(invoice_data)
        
        ## Build global info
        global_info = build_global_info(
            efris_settings, 
            doc, 
            total_tax_amount, 
            invoice_data["goodsDetails"]
        )
        
        ## Build complete POST data
        data_to_post = build_post_data(encrypted_result, global_info)
        
        ## Store request in document
        doc.custom_post_request = json.dumps(data_to_post, indent=4)
        
        ## Submit to EFRIS
        response_data, headers, server_url = submit_to_efris(efris_settings, data_to_post)
        
        ## Handle response
        handle_efris_response(doc, response_data, headers, server_url, data_to_post)
        
    except EFRISIntegrationError as e:
        frappe.throw(str(e))
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        frappe.log_error(
            title="EFRIS Submission Error",
            message=error_msg,
            docstatus = 0 
        )
        frappe.throw(error_msg)


# Optional: Add validation function for hooks
def validate_efris_fields(doc, method):
    """
    Validate EFRIS fields before submission
    Can be used as a validate hook
    """
    if not doc.custom_efris_invoice:
        return
    
    # Common validations
    if not doc.tax_id:
        frappe.throw("Customer TIN is required for EFRIS invoices")
    
    # Type-specific validations
    if doc.is_return:
        validate_credit_note_fields(doc)
    else:
        validate_invoice_fields(doc)

def validate_credit_note_fields(doc):
    """Validate credit note specific fields"""
    if not doc.return_against:
        frappe.throw("Original Invoice (Return Against) is required for credit notes")
    
    # Check if original invoice exists
    if not frappe.db.exists("Sales Invoice", doc.return_against):
        frappe.throw(f"Original invoice {doc.return_against} does not exist")

def validate_invoice_fields(doc):
    """Validate invoice specific fields"""
    # Add any invoice-specific validations here
    pass