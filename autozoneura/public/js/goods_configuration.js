frappe.ui.form.on('Item', {
    refresh: function (frm) {

        // Show button ONLY for new (unsaved) items
        if (frm.is_new()) {

            frm.add_custom_button(__('Query Stock'), function () {

                frappe.call({
                    // method: "autozoneura.autozoneura.custom_scripts.goods_configuration.register_item_efris",
                    args: {
                        doc: frm.doc
                    },
                    freeze: true,
                    freeze_message: __("Syncing item with EFRIS...")
                });

            }, __("Efris Actions"));
        }
    }
});
