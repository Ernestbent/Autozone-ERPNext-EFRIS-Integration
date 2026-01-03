frappe.ui.form.on('EFRIS Settings', {
    refresh: function(frm) {

        // Button to test connection to EFRIS
        frm.add_custom_button('Test Connection', function() {
            frappe.call({
                method: 'autozoneura.autozoneura.doctype.efris_settings.efris_settings.test_efris_connection',
                args: { docname: frm.doc.name },
                freeze: true,
                freeze_message: 'Testing EFRIS Connection...',
                callback: function(r) {
                    if (r.message && r.message.status === 'success') {
                        frappe.msgprint('Success');
                    } else if (r.message && r.message.status === 'error') {
                        frappe.msgprint('Connection Failed: ' + r.message.message);
                    } else {
                        frappe.msgprint('Unexpected response from server.');
                    }
                }
            });
        });

        // Button to fetch UOMs from EFRIS
        frm.add_custom_button('Get UOMs', function() {
            frappe.call({
                method: 'autozoneura.autozoneura.utilities.efris_uoms.get_uoms_from_efris',
                freeze: true,
                freeze_message: 'Fetching UOMs from EFRIS...',
                callback: function(r) {
                    if (r.message && r.message.status === 'success') {
                        frappe.msgprint(r.message.message);
                        frm.reload_doc(); // Reload form to reflect updated UOMs
                    } else if (r.message && r.message.status === 'error') {
                        frappe.msgprint('Error: ' + r.message.message);
                    } else {
                        frappe.msgprint('Unexpected response from server.');
                    }
                }
            });
        });

    }
});
