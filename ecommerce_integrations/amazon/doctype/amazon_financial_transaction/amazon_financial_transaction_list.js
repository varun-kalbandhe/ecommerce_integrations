frappe.listview_settings["Amazon Financial Transaction"] = {
	add_fields: [
		"amazon_order_id",
		"amazon_transaction_id",
		"transaction_type",
		"transaction_status",
		"accounting_status",
		"total_amount",
		"currency",
		"posted_date",
		"sales_order",
		"sales_invoice"
	],
	hide_name_filter: true,
	get_indicator: function (doc) {
		if (doc.accounting_status === "Posted") {
			return [__("Posted"), "green", "accounting_status,=,Posted"];
		} else if (doc.accounting_status === "Ready") {
			return [__("Ready"), "blue", "accounting_status,=,Ready"];
		} else if (doc.accounting_status === "Failed") {
			return [__("Failed"), "red", "accounting_status,=,Failed"];
		} else if (doc.accounting_status === "Reconciled") {
			return [__("Reconciled"), "green", "accounting_status,=,Reconciled"];
		} else {
			return [__("Pending"), "orange", "accounting_status,=,Pending"];
		}
	}
};
