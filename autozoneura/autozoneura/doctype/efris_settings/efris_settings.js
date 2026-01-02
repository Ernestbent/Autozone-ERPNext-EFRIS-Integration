frappe.ui.form.on('EFRIS Settings', {
    refresh: function(frm) {
        // Add custom button to the form
        frm.add_custom_button(__('Test Connection'), function() {
            frappe.call({
                method: 'autozoneura.autozoneura.doctype.efris_settings.efris_settings.test_efris_connection',
                args: {
                    docname: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Testing EFRIS Connection...'),
                callback: function(r) {
                    if (r.message && r.message.status === 'success') {
                        frappe.msgprint(__('Success'));
                    } else if (r.message && r.message.status === 'error') {
                        frappe.msgprint(__('Connection Failed: ' + r.message.message));
                    }
                }
            });
        });
    }
});