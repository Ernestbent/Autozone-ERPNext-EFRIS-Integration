frappe.ui.form.on('Validate Tax Payer Information', {
    validate: function(frm) {  
        if (frm.doc.validate && frm.doc.tax_id) {  
            frm.set_df_property('validate', 'description', 'Validating TIN...');
            
            frappe.call({
                method: "autozoneura.custom_scripts.query_tax_payer_tin.query_tax_payer",
                args: {
                    tax_id: frm.doc.tax_id,
                    customer_name: frm.doc.name
                },
                callback: function(r) {
                    if (r.message && r.message.success) {
                        let data = r.message;
                        
                        frm.set_value('legal_name', data.business_name);
                        frm.set_value('ninbrn', data.nin_brn);
                        frm.set_value('tax_payer_type', data.taxpayer_type);
                        frm.set_value('contact_email', data.contact_email);
                        frm.set_value('contact_number', data.contact_number);
                        frm.set_value('address', data.address);
                        frm.set_value('government_tin', data.government_tin);
                        // frm.set_value('name', data.legal_name || data.business_name);  // CHANGED: customer_name → name
                        
                        frappe.msgprint({
                            title: 'TIN Validation Success',
                            message: 'Taxpayer verified!',
                            indicator: 'green'
                        });
                        
                        frm.set_df_property('validate', 'description', '');
                        frm.set_value('validate', 0);  
                    }
                },
                error: function(r) {
                    frm.set_value('validate', 0);
                    frm.set_df_property('validate', 'description', 'Validation failed. Try again.');
                    frappe.msgprint({
                        title: 'Validation Failed',
                        message: r.message || 'TIN validation failed.',
                        indicator: 'red'
                    });
                }
            });
        } else if (!frm.doc.tax_id) {
            frm.set_value('validate', 0);
            frappe.msgprint('Please enter Tax ID first');
        }
    },
    
    tax_id: function(frm) {  // This matches fieldname="tax_id"
        if (frm.doc.tax_id && frm.doc.validate) {
            frm.trigger('validate');
        }
    }
});
