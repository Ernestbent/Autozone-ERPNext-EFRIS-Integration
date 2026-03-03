frappe.ui.form.on('EFRIS Settings', {
    refresh: function(frm) {
        // Test Connection button - UNCHANGED ✓
        frm.add_custom_button(__('Test Connection'), function() {
            frappe.call({
                method: "autozoneura.autozoneura.doctype.efris_settings.efris_settings.test_efris_connection",
                args: { docname: frm.doc.name },
                freeze: true,
                freeze_message: __("Testing connection..."),
                callback: function(r) {
                    if (r.message && r.message.status === "success") {
                        frappe.msgprint({
                            title: __("Success"),
                            message: JSON.stringify(r.message.message, null, 2),
                            indicator: "green"
                        });
                    } else {
                        frappe.msgprint({
                            title: __("Error"),
                            message: r.message ? r.message.message : __("Failed to connect"),
                            indicator: "red"
                        });
                    }
                }
            });
        });

        // Sync UOMs button - UNCHANGED ✓
        frm.add_custom_button(__('Sync UOMs from EFRIS'), function() {
            frappe.call({
                method: "autozoneura.autozoneura.utilities.efris_uoms.get_uoms_from_efris",
                args: { docname: frm.doc.name },
                freeze: true,
                freeze_message: __("Syncing UOMs from EFRIS..."),
                callback: function(r) {
                    if (r.message && r.message.status === "success") {
                        frappe.msgprint({
                            title: __("Success"),
                            message: r.message.message,
                            indicator: "green"
                        });
                    }
                },
                error: function(r) {
                    frappe.msgprint({
                        title: __("Error"),
                        message: __("Failed to sync UOMs. Check error log."),
                        indicator: "red"
                    });
                }
            });
        });

        // NEW BUTTON ADDED HERE ← Only this is new
        frm.add_custom_button(__('Sync Pending Items to EFRIS'), function() {
            frappe.call({
                method: 'autozoneura.custom_scripts.goods_configuration.sync_pending_items_to_efris',
                args: { batch_size: 50 },
                freeze: true,
                freeze_message: __("Syncing pending items to EFRIS..."),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.msgprint({
                            title: __("Success"),
                            message: r.message.message,
                            indicator: "green"
                        });
                    } else {
                        frappe.msgprint({
                            title: __("Error"),
                            message: r.message ? r.message.message : __("Failed to sync items"),
                            indicator: "red"
                        });
                    }
                }
            });
        });
    }
});