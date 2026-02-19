frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show buttons if the document is submitted and is a return (credit note)
        if (frm.doc.docstatus === 1 && frm.doc.is_return) {

            frm.add_custom_button(__('Query Credit Note Details'), function() {
                get_credit_note_number(frm);
            }, __("Credit Note Actions"));

            frm.add_custom_button(__('Get Verification Code for CN'), function() {
                get_verification_code_for_cn(frm);
            }, __("Credit Note Actions"));
        }
    }
});

// Query Credit Note Number + ID (T111)
function get_credit_note_number(frm) {
    if (!frm.doc.custom_reference_number || !frm.doc.custom_fdn) {
        frappe.msgprint({
            title: __('Missing Fields'),
            message: __('Please ensure both <b>Reference Number</b> and <b>FDN</b> are filled before querying.'),
            indicator: 'orange'
        });
        return;
    }

    frappe.call({
        method: "autozoneura.custom_scripts.query_credit_note_details.query_credit_note",
        args: {
            invoice_name:            frm.doc.name,
            custom_reference_number: frm.doc.custom_reference_number,
            custom_fdn:              frm.doc.custom_fdn,
        },
        freeze: true,
        freeze_message: __('Querying EFRIS for Credit Note details...'),
        callback: function(response) {
            if (response.message && response.message.status === "success") {
                let data = response.message;

                if (data.credit_note_no || data.id) {
                    frappe.show_alert({
                        message: __('Credit Note details saved successfully.'),
                        indicator: 'green'
                    }, 5);
                    frm.reload_doc(); 
                } else {
                    frappe.msgprint({
                        title: __('Incomplete Response'),
                        message: __('Credit Note Number or ID is missing in the EFRIS response.'),
                        indicator: 'orange'
                    });
                }

            } else {
                let error_msg = (response.message && response.message.message)
                    ? response.message.message
                    : __('Unknown error occurred.');

                frappe.msgprint({
                    title: __('Query Failed'),
                    message: __('Failed to retrieve Credit Note details: ') + error_msg,
                    indicator: 'red'
                });
            }
        },
        error: function(error) {
            frappe.msgprint({
                title: __('API Error'),
                message: __('An error occurred while calling the API: ') + (error.message || ''),
                indicator: 'red'
            });
        }
    });
}

// Query Verification Code + QR Code (T108)
function get_verification_code_for_cn(frm) {
    if (!frm.doc.custom_credit_note_number) {
        frappe.msgprint({
            title: __('Missing Field'),
            message: __('Please query the <b>Credit Note Number</b> first before fetching the verification code.'),
            indicator: 'orange'
        });
        return;
    }

    frappe.call({
        method: "autozoneura.custom_scripts.query_verification_code_ccn.query_verification_code_cn",
        args: {
            invoice_name:       frm.doc.name,                     
            credit_note_number: frm.doc.custom_credit_note_number,
        },
        freeze: true,
        freeze_message: __('Querying EFRIS for Verification Code...'),
        callback: function(response) {
            if (response.message && response.message.status === "success") {
                frappe.show_alert({
                    message: __('Verification Code saved successfully.'),
                    indicator: 'green'
                }, 5);
                frm.reload_doc(); 
            } else {
                let error_msg = (response.message && response.message.message)
                    ? response.message.message
                    : __('Unknown error occurred.');

                frappe.msgprint({
                    title: __('Query Failed'),
                    message: __('Failed to retrieve Verification Code: ') + error_msg,
                    indicator: 'red'
                });
            }
        },
        error: function(error) {
            frappe.msgprint({
                title: __('API Error'),
                message: __('An error occurred while calling the API: ') + (error.message || ''),
                indicator: 'red'
            });
        }
    });
}