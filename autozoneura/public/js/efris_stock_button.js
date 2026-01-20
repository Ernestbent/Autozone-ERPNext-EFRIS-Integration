frappe.ui.form.on('Item', {
    refresh: function(frm) {
        // Only show button if item has item_code (new items won't have it yet)
        if (!frm.doc.item_code) return;

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


//  Helper Functions 

function show_efris_success_dialog(frm, result) {
    let data = result.data || {};
    
    // You can make this much more beautiful later
    let message = `
        <div style="font-size: 1.1em;">
            <strong>Item:</strong> ${frm.doc.item_name || frm.doc.item_code}<br>
            <strong>EFRIS Status:</strong> Success (${result.return_code})<br><br>
            <strong>Message:</strong> ${result.message || 'OK'}<br>
        </div>
        <hr>
        <pre style="background:#f8f8f8; padding:12px; border-radius:4px; font-size:0.95em; overflow:auto; max-height:300px;">
${JSON.stringify(data, null, 2)}
        </pre>
    `;

    frappe.msgprint({
        title: __('EFRIS Goods Inquiry - Success'),
        message: message,
        wide: true,
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
        message: `<div style="color:#c0392b; font-weight:bold;">${msg}</div>`,
        indicator: 'red'
    });
}