frappe.ui.form.on('EFRIS Settings', {
    refresh: function(frm) {
        // Refresh AES Key
        frm.add_custom_button(__('Refresh AES Key'), function() {
            frappe.call({
                method: 'autozoneura.autozoneura.background_tasks.efris_key_manager.test_efris_complete_flow',
                freeze: true,
                freeze_message: __('Getting AES Key...'),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.msgprint(__('AES Key refreshed successfully!'));
                    } else {
                        frappe.msgprint(__('Error: ' + (r.message.error || 'Unknown error')));
                    }
                }
            });
        });
        
        // Get UOMs
        frm.add_custom_button(__('Get UOMs'), function() {
            // IMPORTANT: Make sure document is saved
            if (frm.is_new()) {
                frappe.msgprint(__('Please save the document first'));
                return;
            }
            
            frappe.call({
                method: 'autozoneura.autozoneura.utilities.efris_uoms.get_uoms_from_efris',
                args: {
                    docname: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Fetching UOMs...'),
                callback: function(r) {
                    console.log('Response:', r);
                    if (r.message && r.message.status === 'success') {
                        frappe.msgprint(r.message.message);
                    } else if (r.message) {
                        frappe.msgprint(__('Error: ' + r.message.message));
                    }
                }
            });
        });
    }
});