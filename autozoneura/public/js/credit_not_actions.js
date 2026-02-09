frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if the document is submitted and a return
        if (frm.doc.docstatus === 1 && frm.doc.is_return) {  
            frm.add_custom_button(__('Query Credit Note Details (CN and ID)'), function() {
                get_credit_note_number(frm);
            }, __("Credit Note Actions"));
        }
    }
});

// Function to call the server-side method for retrieving credit note number
function get_credit_note_number(frm) {
    if (!frm.doc.custom_reference_number || !frm.doc.custom_fdn) {
        frappe.msgprint(__('Please enter both Custom Reference Number and Custom FDN before proceeding.'));
        return;
    }

    frappe.call({
        method: "autozoneura.custom_scripts.query_cnn.query_credit_note",  
        args: {
            custom_reference_number: frm.doc.custom_reference_number,  
            custom_fdn: frm.doc.custom_fdn  
        },
        callback: function(response) {
            if (response.message && response.message.status === "success") {
                let data = response.message;

                if (data.credit_note_no || data.id) {
                    frm.set_value("custom_credit_note_number", data.credit_note_no || "");  
                    frm.set_value("custom_id", data.id || "");  
                    
                    frappe.msgprint(__('Credit Note retrieved successfully.')); 
                    frm.refresh(); 
                } else {
                    frappe.msgprint(__('Credit Note Number or ID missing in the response.'));
                }
            } else {
                let errorMessage = response.message ? response.message.message : "Unknown error";
                frappe.msgprint(__('Failed to retrieve Credit Note Number: ') + errorMessage);
            }
        },
        error: function(error) {
            frappe.msgprint(__('Error in API call: ') + error.message);
        }
    });
}
