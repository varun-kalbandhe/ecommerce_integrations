# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 140,
		},
		{
			"label": _("Seller SKU"),
			"fieldname": "sku",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("ASIN"),
			"fieldname": "asin",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("Item Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Units Sold"),
			"fieldname": "qty",
			"fieldtype": "Float",
			"width": 90,
		},
		{
			"label": _("Revenue"),
			"fieldname": "revenue",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Product COGS"),
			"fieldname": "product_cogs",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Direct Amazon Fees"),
			"fieldname": "direct_amazon_fees",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Shipping"),
			"fieldname": "shipping_expense",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Advertising"),
			"fieldname": "advertising_expense",
			"fieldtype": "Currency",
			"width": 100,
		},
		{
			"label": _("Refunds"),
			"fieldname": "refund_amount",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Reimbursements"),
			"fieldname": "reimbursement_amount",
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
			"label": _("Profit Margin %"),
			"fieldname": "profit_margin",
			"fieldtype": "Percent",
			"width": 110,
		},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("parent_doc.order_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("parent_doc.order_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("item_code"):
		conditions.append("child.item_code = %(item_code)s")
		values["item_code"] = filters["item_code"]

	if filters.get("sku"):
		conditions.append("child.sku like %(sku)s")
		values["sku"] = f"%{filters['sku']}%"

	if filters.get("asin"):
		conditions.append("child.asin like %(asin)s")
		values["asin"] = f"%{filters['asin']}%"

	where_clause = f"where {' and '.join(conditions)}" if conditions else ""

	raw_data = frappe.db.sql(
		f"""
		select
			child.item_code,
			child.sku,
			child.asin,
			child.item_name,
			sum(child.qty) as qty,
			sum(child.revenue) as revenue,
			sum(child.product_cogs) as product_cogs,
			sum(child.direct_amazon_fees) as direct_amazon_fees,
			sum(child.allocated_order_expense) as shipping_expense,
			0.0 as advertising_expense,
			sum(child.refund_amount) as refund_amount,
			0.0 as reimbursement_amount,
			sum(child.net_profit) as net_profit
		from `tabAmazon Order Profitability Item` child
		inner join `tabAmazon Order Profitability` parent_doc on parent_doc.name = child.parent
		{where_clause}
		group by child.item_code, child.sku, child.asin, child.item_name
		order by revenue desc
		""",
		values,
		as_dict=True,
	)

	for row in raw_data:
		rev = flt(row.get("revenue"))
		profit = flt(row.get("net_profit"))
		if rev > 0:
			row["profit_margin"] = round((profit / rev) * 100.0, 2)
		else:
			row["profit_margin"] = 0.0

	return raw_data
