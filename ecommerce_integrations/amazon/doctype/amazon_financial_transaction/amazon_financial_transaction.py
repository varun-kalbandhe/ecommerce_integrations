# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AmazonFinancialTransaction(Document):
	pass


@frappe.whitelist()
def transaction_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""
	Custom query for Amazon Financial Transaction search.
	Reliably searches across amazon_order_id, amazon_transaction_id, and name.
	"""
	fields = ["name", "amazon_order_id", "amazon_transaction_id", "transaction_type", "total_amount", "currency"]
	fields_sql = ", ".join([f"`tabAmazon Financial Transaction`.`{f}`" for f in fields])

	conditions = []
	values = {"txt": f"%{txt}%", "start": start or 0, "page_len": page_len or 20}

	if txt:
		conditions.append(
			"(`tabAmazon Financial Transaction`.name LIKE %(txt)s "
			"OR `tabAmazon Financial Transaction`.amazon_order_id LIKE %(txt)s "
			"OR `tabAmazon Financial Transaction`.amazon_transaction_id LIKE %(txt)s)"
		)

	where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""
		SELECT {fields_sql}
		FROM `tabAmazon Financial Transaction`
		{where_clause}
		ORDER BY `tabAmazon Financial Transaction`.modified DESC
		LIMIT %(start)s, %(page_len)s
		""",
		values,
		as_dict=as_dict,
	)
