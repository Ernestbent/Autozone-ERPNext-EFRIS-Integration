import json
import base64
import uuid
from datetime import datetime, timezone, timedelta
import frappe
import requests

from autozoneura.autozoneura.background_tasks.encryption import encrypt_dynamic_json
from autozoneura.autozoneura.background_tasks.decryption import decrypt_string

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

## Reason codes for credit notes
REASON_CODES = {
    "Wrong Invoice": "102",
    "Defective Goods": "103",
    "Price Adjustment": "104",
    "Return": "105"
}


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
        "integration_request_service": "Credit Note Issue (T110)",
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
    Build goods detail object for a single item (Credit Note format)
    EFRIS expects NEGATIVE values for credit notes
    """
    return {
        "item": item.item_name,
        "itemCode": item.item_code,
        "qty": item.qty,  ## Keep negative for credit notes
        "unitOfMeasure": item.custom_uom_codeefris,
        "unitPrice": item.rate,
        "total": item.amount,  ## Keep negative for credit notes
        "taxRate": tax_details["tax_rate"],
        "tax": amounts["tax"],  ## Keep negative for credit notes
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
    }


def process_credit_note_items(items):
    """
    Process all credit note items and build goods details and tax categories
    EFRIS expects NEGATIVE values for credit notes
    """
    goods_details = []
    tax_categories = {}
    item_count = 0

    for item in items:
        item_count += 1
        
        ## Determine tax details
        tax_details = determine_tax_details(item.item_tax_template)
        
        ## Calculate amounts (keep negative values)
        amounts = calculate_item_amounts(item, tax_details)
        
        ## Extract calculated values (keep negative)
        taxAmount = amounts["taxAmount"]
        netAmount = amounts["netAmount"]
        grossAmount = item.amount
        
        ## Update or create tax category
        if item.item_tax_template in tax_categories:
            tax_categories[item.item_tax_template]["grossAmount"] += grossAmount
            tax_categories[item.item_tax_template]["taxAmount"] += taxAmount
            tax_categories[item.item_tax_template]["netAmount"] += netAmount
        else:
            tax_categories[item.item_tax_template] = {
                "taxCategoryCode": tax_details["tax_category_code"],
                "netAmount": netAmount,
                "taxRate": tax_details["tax_rate"],
                "taxAmount": taxAmount,
                "grossAmount": grossAmount,
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
        category["grossAmount"] = round(category["grossAmount"], 3)

    return goods_details, list(tax_categories.values()), item_count


def build_buyer_details(doc):
    """Build buyer details section for credit note"""
    buyer_type = BUYER_TYPE_MAPPING.get(doc.customer_group, DEFAULT_BUYER_TYPE)
    
    return {
        "buyerTin": doc.tax_id or "",
        "buyerNinBrn": "",
        "buyerPassportNum": "",
        "buyerLegalName": doc.customer or "",  ## Use doc.customer like original
        "buyerBusinessName": doc.customer or "",  ## Use doc.customer like original
        "buyerAddress": "",
        "buyerEmail": getattr(doc, 'custom_email_id', '') or "",  ## Use custom_email_id like original
        "buyerMobilePhone": "",
        "buyerLinePhone": "",
        "buyerPlaceOfBusi": "",
        "buyerType": buyer_type,
        "buyerCitizenship": "",
        "buyerSector": "1",
        "buyerReferenceNo": "",
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


def build_basic_information(efris_settings, doc):
    """Build basic information section for credit note"""
    
    return {
        "operator": "Testing Service",  ## Hardcoded like original
        "invoiceKind": "1",
        "invoiceIndustryCode": "",
        "branchId": "",
    }


def build_summary(doc, item_count):
    """Build summary section for credit note - use negative values"""
    ## EFRIS expects NEGATIVE values in summary for credit notes
    total_tax_amount = round(doc.total - doc.net_total, 3)
    
    return {
        "netAmount": round(doc.net_total, 3),      # Keep negative
        "taxAmount": total_tax_amount,              # Keep negative
        "grossAmount": round(doc.total, 3),         # Keep negative
        "itemCount": item_count,
        "modeCode": "0",
        "remarks": "We appreciate your continued support",
        "qrCode": doc.custom_qr_code or "",
    }


def build_pay_way(doc):
    """Build payment way section - use negative values"""
    return {
        "paymentMode": "102",
        "paymentAmount": doc.total,  ## Keep negative value for credit notes
        "orderNumber": "a",
    }


def build_credit_note_data(efris_settings, doc, datetime_combined):
    """
    Build complete credit note data structure for EFRIS submission
    """
    ## Validate required fields
    if not doc.custom_invoice_number:
        raise EFRISIntegrationError("Original Invoice Number is required for credit notes")
    
    if not doc.custom_fdn:
        raise EFRISIntegrationError("Original FDN is required for credit notes")
    
    if not doc.custom_reason:
        raise EFRISIntegrationError("Reason is required for credit notes")
    
    ## Process items and get goods details and tax categories
    goods_details, tax_categories_list, item_count = process_credit_note_items(doc.items)
    
    if not goods_details:
        raise EFRISIntegrationError("No items found in the credit note")
    
    ## Log goods details for debugging
    frappe.logger().info(f"Credit Note Goods Details: {json.dumps(goods_details, indent=2)}")
    
    ## Build credit note data structure
    credit_note_data = {
        "oriInvoiceId": doc.custom_invoice_number,
        "oriInvoiceNo": doc.custom_fdn,
        "reasonCode": "102",  # Default reason code - can be made dynamic
        "reason": doc.custom_reason,
        "applicationTime": datetime_combined,
        "invoiceApplyCategoryCode": "101",
        "currency": doc.currency or "UGX",
        "contactName": "",
        "contactMobileNum": "",
        "contactEmail": "",
        "source": "106",  ## Changed from 105 to 106 to match original code
        "remarks": "Remarks",  ## Changed from "Credit Note" to "Remarks"
        "sellersReferenceNo": "",
        "goodsDetails": goods_details,
        "taxDetails": tax_categories_list,
        "summary": build_summary(doc, item_count),
        "payWay": build_pay_way(doc),
        "buyerDetails": build_buyer_details(doc),
        "importServicesSeller": build_import_services_seller(),
        "basicInformation": build_basic_information(efris_settings, doc),
    }
    
    return credit_note_data, item_count


def build_global_info_t110(efris_settings, doc, goods_details):
    """Build global info section for T110 interface"""
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
        "interfaceCode": "T110",  # Credit Note interface code
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
            "operatorName": owner_full_name,
            "itemDescription": item_description,
        },
    }


def encrypt_credit_note_data(credit_note_data):
    """
    Encrypt credit note data using encryption service
    """
    ## Log credit note data before encryption
    frappe.log_error(
        title="EFRIS Credit Note Data Before Encryption",
        message=json.dumps(credit_note_data, indent=2)
    )
    
    ## Encrypt credit note data
    encrypted_result = encrypt_dynamic_json(credit_note_data)
    if not encrypted_result.get("success"):
        raise EFRISIntegrationError(f"Encryption failed: {encrypted_result.get('error')}")
    
    return encrypted_result


def build_post_data_t110(encrypted_result, global_info):
    """
    Build complete POST data for T110 API request
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


def update_credit_note_with_response(doc, decrypted_data):
    """
    Update sales invoice (return) with EFRIS credit note response data
    """
    doc.custom_reference_number = decrypted_data.get("referenceNo", "")


def validate_credit_note_quantities(doc):
    """
    Validate that credit note quantities don't exceed original invoice quantities
    
    Args:
        doc: Credit note (return) Sales Invoice document
    """
    if not doc.return_against:
        return
    
    ## Get original invoice
    try:
        original_invoice = frappe.get_doc("Sales Invoice", doc.return_against)
    except Exception as e:
        frappe.throw(f"Cannot fetch original invoice {doc.return_against}: {str(e)}")
    
    ## Build a map of original invoice items
    original_items = {}
    for item in original_invoice.items:
        original_items[item.item_code] = {
            "qty": item.qty,
            "item_name": item.item_name
        }
    
    ## Validate credit note items
    errors = []
    for item in doc.items:
        if item.item_code in original_items:
            original_qty = original_items[item.item_code]["qty"]
            return_qty = abs(item.qty)  # Credit note has negative qty
            
            if return_qty > original_qty:
                errors.append(
                    f"Item '{item.item_name}' (Code: {item.item_code}): "
                    f"Cannot return {return_qty} units. "
                    f"Original invoice only has {original_qty} units."
                )
        else:
            errors.append(
                f"Item '{item.item_name}' (Code: {item.item_code}) "
                f"was not found in original invoice {doc.return_against}"
            )
    
    if errors:
        error_msg = "<br>".join(errors)
        frappe.throw(
            title="Credit Note Quantity Validation Failed",
            msg=error_msg
        )
    
    frappe.logger().info(f"Credit note quantities validated successfully for {doc.name}")


def submit_to_efris(efris_settings, data_to_post):
    """
    Submit credit note data to EFRIS API
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
    Handle EFRIS API response for credit note
    """
    ## Store response in document
    doc.custom_response = json.dumps(response_data, indent=4)
    
    return_message = response_data.get("returnStateInfo", {}).get("returnMessage", "")
    doc.custom_return_status = return_message
    
    ## Check if successful
    if return_message == "SUCCESS":
        frappe.msgprint("Credit Note successfully submitted to EFRIS URA.")
        
        ## Decrypt and extract response data
        encrypted_content = response_data.get("data", {}).get("content", "")
        decrypted_data = decrypt_response_content(encrypted_content)
        
        ## Update credit note with response data
        update_credit_note_with_response(doc, decrypted_data)
        
        ## Log successful request
        log_integration_request('Completed', server_url, headers, data_to_post, response_data)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    else:
        ## Log failed request
        log_integration_request('Failed', server_url, headers, data_to_post, response_data, return_message)
        frappe.throw(title="Oops! API Error", msg=return_message)


def process_credit_note(doc, event):
    """
    Hook for Sales Invoice (Return) submission to EFRIS using T110 interface
    
    This function should be called when a return sales invoice is submitted
    """
    ## Only process if it's a return (credit note)
    if not doc.is_return:
        return
    
    ## Skip if not EFRIS invoice
    if not doc.custom_efris_invoice:
        return
    
    try:
        ## Validate against original invoice if return_against is set
        if doc.return_against:
            validate_credit_note_quantities(doc)
        
        ## Get EFRIS settings
        efris_settings = get_efris_settings()
        
        ## Prepare datetime
        datetime_combined = f"{doc.posting_date} {doc.posting_time}"
        
        ## Build credit note data
        credit_note_data, item_count = build_credit_note_data(efris_settings, doc, datetime_combined)
        
        ## Encrypt credit note data
        encrypted_result = encrypt_credit_note_data(credit_note_data)
        
        ## Build global info
        global_info = build_global_info_t110(
            efris_settings, 
            doc, 
            credit_note_data["goodsDetails"]
        )
        
        ## Build complete POST data
        data_to_post = build_post_data_t110(encrypted_result, global_info)
        
        ## Store request in document
        doc.custom_post_request = json.dumps(data_to_post, indent=4)
        
        ## Log request data
        frappe.log_error(
            title="EFRIS T110 Request Data",
            message=json.dumps(data_to_post, indent=2)
        )
        
        ## Submit to EFRIS
        response_data, headers, server_url = submit_to_efris(efris_settings, data_to_post)
        
        ## Handle response
        handle_efris_response(doc, response_data, headers, server_url, data_to_post)
        
    except EFRISIntegrationError as e:
        ## Set document to draft on error
        doc.docstatus = 0
        frappe.throw(str(e))
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        frappe.log_error(
            title="EFRIS Credit Note Submission Error",
            message=error_msg
        )
        ## Set document to draft on error
        doc.docstatus = 0
        frappe.throw(error_msg)