frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Add button only if invoice is in Draft state
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Validate Efris Stock'), function() {
                // Call server-side validation
                frappe.call({
                    method: "autozoneura.custom_scripts.query_stock_first.validate_invoice_stock_before_efris",
                    args: {
                        invoice_name: frm.doc.name
                    },
                    freeze: true,
                    freeze_message: __("Checking EFRIS stock for all items..."),
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint({
                                title: __('Stock Validation Complete'),
                                message: __('All items have sufficient stock in EFRIS!'),
                                indicator: 'green'
                            });
                        }
                    }
                });
            });
        }
    }
});