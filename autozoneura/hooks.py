app_name = "autozoneura"
app_title = "Autozoneura"
app_publisher = "Craftson Auto Parts formerly known as Autozone Professional Limited."
app_description = "The Customization Integrates ERPNext with EFRIS for E-Invoices."
app_email = "ernestben69@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "autozoneura",
# 		"logo": "/assets/autozoneura/logo.png",
# 		"title": "Autozoneura",
# 		"route": "/autozoneura",
# 		"has_permission": "autozoneura.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/autozoneura/css/autozoneura.css"
# app_include_js = "/assets/autozoneura/js/autozoneura.js"

# include js, css files in header of web template
# web_include_css = "/assets/autozoneura/css/autozoneura.css"
# web_include_js = "/assets/autozoneura/js/autozoneura.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "autozoneura/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Item": [
        "public/js/goods_configuration.js",
        "public/js/efris_stock_button.js"
    ],
    "Customer": "public/js/get_tax_payer_information.js",
    
}

    
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "autozoneura/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "autozoneura.utils.jinja_methods",
# 	"filters": "autozoneura.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "autozoneura.install.before_install"
# after_install = "autozoneura.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "autozoneura.uninstall.before_uninstall"
# after_uninstall = "autozoneura.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "autozoneura.utils.before_app_install"
# after_app_install = "autozoneura.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "autozoneura.utils.before_app_uninstall"
# after_app_uninstall = "autozoneura.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "autozoneura.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Item": {
        "validate": "autozoneura.custom_scripts.goods_configuration.on_save"
    },
    "Purchase Receipt": {
        "on_submit": "autozoneura.custom_scripts.stock_in.on_stock"
    },
    "Stock Entry": {
        "on_submit": "autozoneura.custom_scripts.stock_adjustment.stock_adjust"
    },
    "Sales Invoice":{
        "on_submit": "autozoneura.custom_scripts.upload_invoice.on_send"
    }
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"autozoneura.autozoneura.background_tasks.efris_key_manager.test_efris_complete_flow"
	],
}

# Testing
# -------

# before_tests = "autozoneura.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "autozoneura.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "autozoneura.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["autozoneura.utils.before_request"]
# after_request = ["autozoneura.utils.after_request"]

# Job Events
# ----------
# before_job = ["autozoneura.utils.before_job"]
# after_job = ["autozoneura.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"autozoneura.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

