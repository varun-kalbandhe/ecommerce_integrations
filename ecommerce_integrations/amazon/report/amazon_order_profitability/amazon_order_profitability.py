# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Amazon Order ID"),
			"fieldname": "amazon_order_id",
			"fieldtype": "Link",
			"options": "Amazon Order Profitability",
			"width": 180,
		},
		{
			"label": _("Order Date"),
			"fieldname": "order_date",
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"label": _("Sales Order"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 140,
		},
		{
			"label": _("Sales Invoice"),
			"fieldname": "sales_invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 140,
		},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 150,
		},
		{
			"label": _("Revenue"),
			"fieldname": "revenue",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("COGS"),
			"fieldname": "product_cogs",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Amazon Fees"),
			"fieldname": "amazon_fees",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Shipping (Easy Ship)"),
			"fieldname": "shipping_expense",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Advertising"),
			"fieldname": "advertising_expense",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Refunds"),
			"fieldname": "refund_amount",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Other Expenses"),
			"fieldname": "other_amazon_expense",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Net Profit"),
			"fieldname": "net_profit",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Margin %"),
			"fieldname": "profit_margin",
			"fieldtype": "Percent",
			"width": 90,
		},
		{
			"label": _("Status"),
			"fieldname": "calculation_status",
			"fieldtype": "Data",
			"width": 100,
		},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("order_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("order_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("amazon_order_id"):
		conditions.append("amazon_order_id like %(amazon_order_id)s")
		values["amazon_order_id"] = f"%{filters['amazon_order_id']}%"

	if filters.get("customer"):
		conditions.append("customer = %(customer)s")
		values["customer"] = filters["customer"]

	if filters.get("sales_invoice"):
		conditions.append("sales_invoice = %(sales_invoice)s")
		values["sales_invoice"] = filters["sales_invoice"]

	if filters.get("calculation_status"):
		conditions.append("calculation_status = %(calculation_status)s")
		values["calculation_status"] = filters["calculation_status"]

	where_clause = f"where {' and '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		select
			name,
			amazon_order_id,
			order_date,
			sales_order,
			sales_invoice,
			customer,
			revenue,
			product_cogs,
			amazon_fees,
			shipping_expense,
			advertising_expense,
			other_amazon_expense,
			refund_amount,
			adjustment_amount,
			net_profit,
			profit_margin,
			calculation_status
		from `tabAmazon Order Profitability`
		{where_clause}
		order by order_date desc, modified desc
		""",
		values,
		as_dict=True,
	)
