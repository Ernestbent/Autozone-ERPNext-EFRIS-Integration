import frappe
from frappe import _

def execute(filters=None):
    """FULL WIDTH EFRIS STOCK REPORT"""
    columns = [
        {"label": _("Item Code"), "fieldname": "goods_code", "fieldtype": "Link", "options": "Item", "width": 220},  
        {"label": _("Item Name"), "fieldname": "goods_name", "fieldtype": "Data", "width": 600},                  
        {"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Int", "width": 150, "precision": 1}     #
    ]
    
    result = frappe.call("autozoneura.custom_scripts.efris_stock_for_report.get_efris_t127_stock_report_data")
    if not result.get("success"):
        frappe.throw(result.get("message"))
    
    return columns, result["data"]
