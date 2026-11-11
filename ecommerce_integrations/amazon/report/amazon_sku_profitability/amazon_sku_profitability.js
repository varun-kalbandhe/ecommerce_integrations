frappe.query_reports["Amazon SKU Profitability"] = {
	"filters": [
		{
			"fieldname": "from_date",
			"label": __("From Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.add_months(frappe.datetime.get_today(), -1)
		},
		{
			"fieldname": "to_date",
			"label": __("To Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today()
		},
		{
			"fieldname": "item_code",
			"label": __("Item Code"),
			"fieldtype": "Link",
			"options": "Item"
		},
		{
			"fieldname": "sku",
			"label": __("Seller SKU"),
			"fieldtype": "Data"
		},
		{
			"fieldname": "asin",
			"label": __("ASIN"),
			"fieldtype": "Data"
		}
	]
};
