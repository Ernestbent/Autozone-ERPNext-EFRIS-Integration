frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if item has item_code (new items won't have it yet)
       
        
        frm.add_custom_button(__('EFRIS Stock'), function() {
            frappe.call({
                method: "autozoneura.custom_scripts.query_stock_levels_item.get_efris_stock",
                args: {
                    item_code: frm.doc.item_code
                },
                freeze: true,
                freeze_message: __("Querying EFRIS..."),
                callback: function(r) {
                    if (!r.message) {
                        frappe.msgprint({
                            title: __('Error'),
                            message: __('No response from server'),
                            indicator: 'red'
                        });
                        return;
                    }
                    
                    let res = r.message;
                    if (res.success) {
                        show_efris_success_dialog(frm, res);
                    } else {
                        show_efris_error_dialog(res);
                    }
                },
                error: function(r) {
                    frappe.msgprint({
                        title: __('EFRIS Connection Failed'),
                        message: r.message || __('Unknown error occurred'),
                        indicator: 'red'
                    });
                }
            });
        }, __('View'));
    }
});

// Helper Functions
function show_efris_success_dialog(frm, result) {
    let data = result.data || {};
    let records = data.records || [];
    
    if (records.length === 0) {
        frappe.msgprint({
            title: __('EFRIS Stock'),
            message: __('No stock information found'),
            indicator: 'orange'
        });
        return;
    }
    
    let record = records[0];
    let stock = record.stock || '0';
    let unit = record.measureUnit || '';
    
    frappe.msgprint({
        title: __('EFRIS Stock'),
        message: `Stock: <span style="color: green;">${stock}</span> ${unit}`,
        indicator: 'green'
    });
}

function show_efris_error_dialog(result) {
    let msg = result.message || 'Unknown error';
    if (result.return_code) {
        msg = `(${result.return_code}) ${msg}`;
    }
    
    frappe.msgprint({
        title: __('EFRIS Response'),
        message: msg,
        indicator: 'red'
    });
}