frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if the document is submitted and is a return (credit note)
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


function get_credit_note_number(frm) {
    // Validate required fields before calling API
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
            custom_reference_number: frm.doc.custom_reference_number,
            custom_fdn:              frm.doc.custom_fdn,
        },
        freeze: true,
        freeze_message: __('Querying EFRIS for Credit Note details...'),
        callback: function(response) {
            if (response.message && response.message.status === "success") {
                let data = response.message;

                if (data.credit_note_no || data.id) {
                    // Set values on the form
                    frm.set_value("custom_credit_note_number", data.credit_note_no || "");
                    frm.set_value("custom_id", data.id || "");

                    // Save the document to persist the values
                    frm.save().then(() => {
                        frappe.show_alert({
                            message: __('Credit Note details saved successfully.'),
                            indicator: 'green'
                        }, 5);
                    });

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

// Function to get verification code for credit note
function get_verification_code_for_cn(frm) {
    if (!frm.doc.custom_credit_note_number) {
        frappe.msgprint(__('Please enter Credit Note Number before proceeding.'));
        return;
    }

    frappe.call({
        method: "autozoneura.custom_scripts.query_verification_code_ccn.query_verification_code_cn",
        args: {
            credit_note_number: frm.doc.custom_credit_note_number
        },
        callback: function(response) {
            if (response.message && response.message.status === "success") {
                let data = response.message;

                if (data.verification_code || data.qr_code_efris) {
                    frm.set_value("custom_verification_codecn", data.verification_code || "");  
                    frm.set_value("custom_qr_code_credit_note_", data.qr_code_efris || "");  

                    frappe.msgprint(__('Verification Code retrieved successfully.'));
                    frm.refresh(); 
                } else {
                    frappe.msgprint(__('Verification Code missing in the response.'));
                }
            } else {
                let errorMessage = response.message ? response.message.message : "Unknown error";
                frappe.msgprint(__('Failed to retrieve Verification Code: ') + errorMessage);
            }
        },
        error: function(error) {
            frappe.msgprint(__('Error in API call: ') + error.message);
        }
    });
}