frappe.ui.form.on('Customer', {
    custom_validate_tin: function(frm) {
        if (frm.doc.custom_validate_tin && frm.doc.tax_id) {
            frm.set_df_property('custom_validate_tin', 'description', 'Validating TIN...');
            
            frappe.call({
                method: "autozoneura.custom_scripts.query_tax_payer_tin.query_tax_payer",
                args: {
                    tax_id: frm.doc.tax_id,
                    customer_name: frm.doc.name
                },
                callback: function(r) {
                    if (r.message && r.message.success) {
                        let data = r.message;
                        
                        frm.set_value('custom_business_name', data.business_name);
                        frm.set_value('custom_ninbrn', data.nin_brn);
                        frm.set_value('custom_tax_payer_type', data.taxpayer_type);
                        frm.set_value('custom_contact_email', data.contact_email);
                        frm.set_value('custom_contact_number', data.contact_number);
                        frm.set_value('custom_address', data.address);
                        frm.set_value('custom_government_tin', data.government_tin);
                        frm.set_value('customer_name', data.legal_name || data.business_name);
                        
                        frappe.msgprint({
                            title: 'TIN Validation Success',
                            message: 'Taxpayer verified!<br>Business: ' + data.business_name + '<br>TIN: ' + data.tax_id,
                            indicator: 'green'
                        });
                        
                        frm.set_df_property('custom_validate_tin', 'description', '');
                    }
                },
                error: function(r) {
                    frm.set_value('custom_validate_tin', 0);
                    frm.set_df_property('custom_validate_tin', 'description', 'Validation failed. Try again.');
                    frappe.msgprint({
                        title: 'Validation Failed',
                        message: r.message || 'TIN validation failed.',
                        indicator: 'red'
                    });
                }
            });
        } else if (!frm.doc.tax_id) {
            frm.set_value('custom_validate_tin', 0);
            frappe.msgprint('Please enter TIN first');
        }
    },
    
    tax_id: function(frm) {
        if (frm.doc.tax_id && frm.doc.custom_validate_tin) {
            frm.trigger('custom_validate_tin');
        }
    }
});
