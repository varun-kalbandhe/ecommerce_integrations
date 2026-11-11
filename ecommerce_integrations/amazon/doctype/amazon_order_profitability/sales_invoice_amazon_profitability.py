# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.utils import flt, fmt_money


def get_sales_invoice_amazon_order_id(invoice_doc_or_name) -> str | None:
	"""
	Resolves Amazon Order ID linked to a Sales Invoice.
	Checks:
	1. Direct custom_marketplace_order on Sales Invoice.
	2. po_no on Sales Invoice.
	3. Linked Sales Order amazon_order_id on Sales Invoice Items.
	4. Amazon Order Profitability linked to this Sales Invoice.
	5. Amazon Financial Transaction linked to this Sales Invoice.
	"""
	if not invoice_doc_or_name:
		return None

	if isinstance(invoice_doc_or_name, str):
		inv_name = invoice_doc_or_name
		m_order = frappe.db.get_value("Sales Invoice", inv_name, "custom_marketplace_order")
		if m_order:
			return m_order.strip()

		po_no = frappe.db.get_value("Sales Invoice", inv_name, "po_no")
		if po_no and ("-" in po_no or len(po_no) > 8):
			return po_no.strip()

		# Check linked AOP
		aop_oid = frappe.db.get_value("Amazon Order Profitability", {"sales_invoice": inv_name}, "amazon_order_id")
		if aop_oid:
			return aop_oid.strip()

		# Check linked Amazon Financial Transaction
		aft_oid = frappe.db.get_value("Amazon Financial Transaction", {"sales_invoice": inv_name}, "amazon_order_id")
		if aft_oid:
			return aft_oid.strip()

		# Check linked Sales Order via items
		so_names = frappe.db.sql(
			"select distinct sales_order from `tabSales Invoice Item` where parent = %s and sales_order is not null and sales_order != ''",
			inv_name,
			pluck=True,
		)
		for so_name in so_names:
			so_oid = frappe.db.get_value("Sales Order", so_name, "amazon_order_id")
			if so_oid:
				return so_oid.strip()
		return None
	else:
		doc = invoice_doc_or_name
		if getattr(doc, "custom_marketplace_order", None):
			return doc.custom_marketplace_order.strip()

		if getattr(doc, "po_no", None) and ("-" in doc.po_no or len(doc.po_no) > 8):
			return doc.po_no.strip()

		if doc.name:
			aop_oid = frappe.db.get_value("Amazon Order Profitability", {"sales_invoice": doc.name}, "amazon_order_id")
			if aop_oid:
				return aop_oid.strip()

			aft_oid = frappe.db.get_value("Amazon Financial Transaction", {"sales_invoice": doc.name}, "amazon_order_id")
			if aft_oid:
				return aft_oid.strip()

		for item in getattr(doc, "items", []):
			if item.sales_order:
				so_oid = frappe.db.get_value("Sales Order", item.sales_order, "amazon_order_id")
				if so_oid:
					return so_oid.strip()
		return None


def extract_sales_invoice_charge_lines(amazon_order_id: str, invoice_name: str | None = None) -> tuple[list[dict], dict, str]:
	"""
	Retrieves granular Amazon financial breakdown line items for an Amazon order/invoice.
	Produces standardized columns:
	- Charge Type (e.g. Amazon Commission, Fixed Closing Fee, Easy Ship Fee, TCS, TDS, Refund, Reimbursement)
	- Description
	- Amount (Base pre-tax)
	- Tax Amount (Fee GST)
	- Total Amount (Base + Tax)
	- Amazon Financial Transaction (link)
	- Accounting Status

	Calculates:
	- charges_summary dict
	- total_amazon_charges: sum of operating seller expenses (excludes TCS/TDS withholdings & Fee GST balance sheet assets)
	- overall accounting_status: Pending / Partially Accounted / Accounted / Failed
	"""
	default_summary = {
		"commission": 0.0,
		"fixed_closing_fee": 0.0,
		"easy_ship": 0.0,
		"other_fees": 0.0,
		"fee_gst": 0.0,
		"tcs": 0.0,
		"tds": 0.0,
		"refund_impact": 0.0,
		"reimbursements": 0.0,
		"total_amazon_charges": 0.0,
		"total_amazon_expenses": 0.0,
	}

	if not amazon_order_id:
		return [], dict(default_summary), "Pending"

	filters = {"amazon_order_id": amazon_order_id}
	if invoice_name and not frappe.db.exists("Amazon Financial Transaction", filters):
		filters = {"sales_invoice": invoice_name}

	txs = frappe.get_all(
		"Amazon Financial Transaction",
		filters=filters,
		fields=["name", "transaction_type", "total_amount", "accounting_status", "journal_entry", "raw_response"],
		order_by="posted_date asc, creation asc",
	)

	if not txs:
		return [], dict(default_summary), "Pending"

	lines = []
	charges_summary = dict(default_summary)

	statuses = set()

	for t in txs:
		t_status = t.accounting_status or "Pending"
		statuses.add(t_status)

		raw = json.loads(t.raw_response or "{}")
		desc = (raw.get("description") or "").lower()
		is_shipping = t.transaction_type == "ServiceFee" and any(
			s in desc for s in ["postage", "easyship", "easy ship", "shipping", "delivery", "fba weight"]
		)

		items = raw.get("items") or []
		bds_sources = []
		if items:
			for itm in items:
				for b in itm.get("breakdowns") or []:
					bds_sources.append(b)
		else:
			for b in raw.get("breakdowns") or []:
				bds_sources.append(b)

		lines_added_for_tx = 0

		def traverse(node, path=[]):
			nonlocal lines_added_for_tx
			if not isinstance(node, dict):
				return
			b_type = node.get("breakdownType") or ""
			curr_path = path + [b_type]
			subs = node.get("breakdowns") or []
			amt = float((node.get("breakdownAmount") or {}).get("currencyAmount") or 0.0)

			if subs:
				base_amt = 0.0
				tax_amt = 0.0
				has_base_tax = False
				for sub in subs:
					sub_type = (sub.get("breakdownType") or "").lower()
					sub_amt = float((sub.get("breakdownAmount") or {}).get("currencyAmount") or 0.0)
					if sub_type == "base":
						base_amt = abs(sub_amt)
						has_base_tax = True
					elif sub_type == "tax":
						tax_amt = abs(sub_amt)
						has_base_tax = True

				if has_base_tax:
					path_str = " ".join(curr_path).lower()
					if "commission" in path_str:
						ctype = "Amazon Commission"
						cdesc = "Amazon referral commission on sold items"
						charges_summary["commission"] += base_amt
					elif "closing" in path_str:
						ctype = "Fixed Closing Fee"
						cdesc = "Fixed closing fee per order item"
						charges_summary["fixed_closing_fee"] += base_amt
					elif is_shipping or any(k in path_str for k in ["postage", "shipping", "easyship", "mfnpostagefee"]):
						ctype = "Easy Ship Fee"
						cdesc = "Easy Ship delivery postage fee"
						charges_summary["easy_ship"] += base_amt
					else:
						ctype = "Other Amazon Fees"
						cdesc = f"{b_type} fee"
						charges_summary["other_fees"] += base_amt

					charges_summary["fee_gst"] += tax_amt
					lines.append({
						"charge_type": ctype,
						"description": cdesc,
						"amount": round(base_amt, 2),
						"tax_amount": round(tax_amt, 2),
						"total_amount": round(base_amt + tax_amt, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
					lines_added_for_tx += 1
					return
				else:
					for sub in subs:
						traverse(sub, curr_path)
			else:
				path_str = " ".join(curr_path).lower()
				val = abs(amt)
				if any(x in path_str for x in ["tcs", "taxcollectedatsource"]):
					charges_summary["tcs"] += val
					lines.append({
						"charge_type": "TCS",
						"description": f"Tax Collected at Source ({b_type})",
						"amount": round(val, 2),
						"tax_amount": 0.0,
						"total_amount": round(val, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
					lines_added_for_tx += 1
				elif any(x in path_str for x in ["tds", "taxwithholding", "taxdeductedatsource", "itemtds"]):
					charges_summary["tds"] += val
					lines.append({
						"charge_type": "TDS",
						"description": f"Tax Deducted at Source ({b_type} u/s 194-O)",
						"amount": round(val, 2),
						"tax_amount": 0.0,
						"total_amount": round(val, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
					lines_added_for_tx += 1
				elif t.transaction_type == "Refund" or "refund" in path_str:
					if any(p in path_str for p in ["principal", "productcharges", "sales", "shippingprincipal"]):
						charges_summary["refund_impact"] += val
						lines.append({
							"charge_type": "Refund",
							"description": f"Customer Refund ({b_type})",
							"amount": round(val, 2),
							"tax_amount": 0.0,
							"total_amount": round(val, 2),
							"amazon_financial_transaction": t.name,
							"accounting_status": t_status,
						})
						lines_added_for_tx += 1
				elif t.transaction_type == "Adjustment" or "reimbursement" in path_str:
					charges_summary["reimbursements"] += val
					lines.append({
						"charge_type": "Reimbursement",
						"description": f"Seller Reimbursement ({b_type})",
						"amount": round(val, 2),
						"tax_amount": 0.0,
						"total_amount": round(val, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
					lines_added_for_tx += 1

		for b in bds_sources:
			traverse(b)

		# Fallback if raw_response did not produce lines (e.g. mock test fixture with child table only)
		if lines_added_for_tx == 0:
			child_bds = frappe.get_all(
				"Amazon Financial Breakdown",
				filters={"parent": t.name},
				fields=["breakdown_type", "category", "fee_type", "amount", "tax_amount", "description"],
			)
			for cb in child_bds:
				cb_amt = abs(flt(cb.amount))
				cb_tax = abs(flt(cb.tax_amount))
				cb_cat = (cb.category or "").lower()
				cb_fee = (cb.fee_type or "").lower()
				cb_type = (cb.breakdown_type or "").lower()

				# Skip customer sales principal / output tax rows
				if any(s in cb_cat for s in ["sales", "productcharges"]) and cb_type != "tax":
					continue

				if "taxcollectedatsource" in cb_cat or "tcs" in cb_fee or "tcs" in cb_type:
					charges_summary["tcs"] += cb_amt
					lines.append({
						"charge_type": "TCS",
						"description": cb.description or f"Tax Collected at Source ({cb.fee_type or cb.breakdown_type})",
						"amount": round(cb_amt, 2),
						"tax_amount": 0.0,
						"total_amount": round(cb_amt, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif "taxwithholding" in cb_cat or "taxdeductedatsource" in cb_cat or "tds" in cb_fee or "tds" in cb_type:
					charges_summary["tds"] += cb_amt
					lines.append({
						"charge_type": "TDS",
						"description": cb.description or f"Tax Deducted at Source ({cb.fee_type or cb.breakdown_type})",
						"amount": round(cb_amt, 2),
						"tax_amount": 0.0,
						"total_amount": round(cb_amt, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif cb_type == "tax" or (cb_cat in ["tax", "amazonfees"] and cb_type == "tax"):
					charges_summary["fee_gst"] += cb_amt
					lines.append({
						"charge_type": "Fee GST",
						"description": cb.description or "Amazon Fee GST (Input Tax)",
						"amount": 0.0,
						"tax_amount": round(cb_amt, 2),
						"total_amount": round(cb_amt, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif "commission" in cb_fee or "commission" in cb_type or "commission" in cb_cat:
					charges_summary["commission"] += cb_amt
					charges_summary["fee_gst"] += cb_tax
					lines.append({
						"charge_type": "Amazon Commission",
						"description": cb.description or "Amazon referral commission on sold items",
						"amount": round(cb_amt, 2),
						"tax_amount": round(cb_tax, 2),
						"total_amount": round(cb_amt + cb_tax, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif "closing" in cb_fee or "closing" in cb_type:
					charges_summary["fixed_closing_fee"] += cb_amt
					charges_summary["fee_gst"] += cb_tax
					lines.append({
						"charge_type": "Fixed Closing Fee",
						"description": cb.description or "Fixed closing fee per order item",
						"amount": round(cb_amt, 2),
						"tax_amount": round(cb_tax, 2),
						"total_amount": round(cb_amt + cb_tax, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif is_shipping or any(k in (cb_fee + cb_type + cb_cat) for k in ["postage", "shipping", "easyship"]):
					charges_summary["easy_ship"] += cb_amt
					charges_summary["fee_gst"] += cb_tax
					lines.append({
						"charge_type": "Easy Ship Fee",
						"description": cb.description or "Easy Ship delivery postage fee",
						"amount": round(cb_amt, 2),
						"tax_amount": round(cb_tax, 2),
						"total_amount": round(cb_amt + cb_tax, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})
				elif cb_amt > 0:
					charges_summary["other_fees"] += cb_amt
					charges_summary["fee_gst"] += cb_tax
					lines.append({
						"charge_type": "Other Amazon Fees",
						"description": cb.description or f"{cb.category or cb.breakdown_type} fee",
						"amount": round(cb_amt, 2),
						"tax_amount": round(cb_tax, 2),
						"total_amount": round(cb_amt + cb_tax, 2),
						"amazon_financial_transaction": t.name,
						"accounting_status": t_status,
					})

	# Round all summary fields
	for k in charges_summary:
		charges_summary[k] = round(charges_summary[k], 2)

	charges_summary["total_amazon_charges"] = round(
		charges_summary["commission"]
		+ charges_summary["fixed_closing_fee"]
		+ charges_summary["easy_ship"]
		+ charges_summary["other_fees"],
		2,
	)
	charges_summary["total_amazon_expenses"] = charges_summary["total_amazon_charges"]

	# Overall accounting status
	if "Failed" in statuses:
		overall_status = "Failed"
	elif statuses and all(s == "Posted" for s in statuses):
		overall_status = "Accounted"
	elif "Posted" in statuses and any(s in ["Ready", "Pending"] for s in statuses):
		overall_status = "Partially Accounted"
	else:
		overall_status = "Pending"

	return lines, charges_summary, overall_status


def extract_amazon_charges(amazon_order_id: str) -> dict:
	"""
	Backward-compatible helper returning charges_summary dict.
	"""
	_, charges_summary, _ = extract_sales_invoice_charge_lines(amazon_order_id)
	return charges_summary



def update_sales_invoice_amazon_charges(invoice_name: str, sync_aop: bool = True) -> dict:
	"""
	Synchronizes the Amazon Charges child table and summary fields on the Sales Invoice:
	- Matches via Sales Invoice / Sales Order / Amazon Order ID
	- Replaces child table rows in Amazon Sales Invoice Charge safely
	- Sets total_amazon_charges, amazon_accounting_status, and profitability fields
	- Never changes Sales Invoice Net Total, Grand Total, Customer Outstanding, or GL Entries
	"""
	data = get_sales_invoice_amazon_profitability_data(invoice_name, sync_aop=sync_aop)
	if not data or not data.get("is_amazon_invoice"):
		return data

	amazon_order_id = data.get("amazon_order_id")
	if not amazon_order_id or not data.get("has_amazon_data"):
		return data

	# Link any unlinked Amazon Financial Transactions
	frappe.db.sql(
		"""
		UPDATE `tabAmazon Financial Transaction`
		SET sales_invoice = %s
		WHERE amazon_order_id = %s AND (sales_invoice IS NULL OR sales_invoice = '')
		""",
		(invoice_name, amazon_order_id),
	)

	charge_lines = data.get("charge_lines") or []
	summary = data.get("charges") or {}
	prof = data.get("profitability") or {}

	# Cleanly refresh child table rows using direct DB inserts to support submitted invoices
	frappe.db.delete("Amazon Sales Invoice Charge", {"parent": invoice_name})
	for idx, row in enumerate(charge_lines, 1):
		child = frappe.new_doc("Amazon Sales Invoice Charge")
		child.parent = invoice_name
		child.parenttype = "Sales Invoice"
		child.parentfield = "amazon_charges"
		child.idx = idx
		child.charge_type = row.get("charge_type")
		child.description = row.get("description")
		child.amount = flt(row.get("amount"))
		child.tax_amount = flt(row.get("tax_amount"))
		child.total_amount = flt(row.get("total_amount"))
		child.amazon_financial_transaction = row.get("amazon_financial_transaction")
		child.accounting_status = row.get("accounting_status")
		child.db_insert()

	# Update summary fields on Sales Invoice
	update_values = {
		"total_amazon_charges": flt(summary.get("total_amazon_charges")),
		"amazon_accounting_status": data.get("accounting_status") or "Pending",
		"amazon_order_profit": flt(prof.get("net_profit")),
		"amazon_profit_margin": flt(prof.get("profit_margin")),
		"amazon_profitability_status": prof.get("status") or "Estimated",
		"amazon_profitability_link": data.get("aop_name"),
	}

	for fieldname, val in update_values.items():
		if frappe.db.has_column("Sales Invoice", fieldname):
			frappe.db.set_value("Sales Invoice", invoice_name, fieldname, val, update_modified=False)

	return data


def get_sales_invoice_amazon_profitability_data(invoice_name: str, sync_aop: bool = True) -> dict:
	"""
	Assembles the complete data structure for the Sales Invoice
	Amazon Charges view and profitability dashboard.
	"""
	amazon_order_id = get_sales_invoice_amazon_order_id(invoice_name)
	if not amazon_order_id:
		return {
			"is_amazon_invoice": False,
			"invoice_name": invoice_name,
		}

	# Check for Amazon Financial Transactions
	tx_count = frappe.db.count("Amazon Financial Transaction", {"amazon_order_id": amazon_order_id})
	if tx_count == 0:
		return {
			"is_amazon_invoice": True,
			"has_amazon_data": False,
			"amazon_order_id": amazon_order_id,
			"invoice_name": invoice_name,
			"accounting_status": "Pending",
			"status": "Amazon financial data pending",
			"message": "Amazon financial data not available yet.",
		}

	# Extract granular charge lines and summary
	charge_lines, charges_summary, accounting_status = extract_sales_invoice_charge_lines(
		amazon_order_id, invoice_name=invoice_name
	)

	# Get or calculate Amazon Order Profitability
	aop_name = frappe.db.get_value("Amazon Order Profitability", {"amazon_order_id": amazon_order_id}, "name")
	if not aop_name and sync_aop:
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import (
			calculate_amazon_order_profitability,
		)
		aop_name = calculate_amazon_order_profitability(amazon_order_id)

	aop = frappe.get_doc("Amazon Order Profitability", aop_name) if aop_name else None

	if aop:
		# Ensure Sales Invoice link is maintained on AOP
		if aop.sales_invoice != invoice_name:
			aop.db_set("sales_invoice", invoice_name, update_modified=False)

		# Harmonize total if detailed breakdown sum matches AOP validated fees
		total_expenses = round(flt(aop.amazon_fees) + flt(aop.shipping_expense) + flt(aop.other_amazon_expense), 2)
		if charges_summary.get("total_amazon_charges", 0.0) == 0.0 and total_expenses > 0.0:
			charges_summary["easy_ship"] = round(flt(aop.shipping_expense), 2)
			charges_summary["other_fees"] = round(flt(aop.other_amazon_expense), 2)
			charges_summary["commission"] = round(flt(aop.amazon_fees), 2)
			charges_summary["total_amazon_charges"] = total_expenses
		else:
			charges_summary["total_amazon_charges"] = total_expenses
		charges_summary["total_amazon_expenses"] = charges_summary["total_amazon_charges"]

		if aop.refund_amount > 0 and charges_summary.get("refund_impact", 0.0) == 0.0:
			charges_summary["refund_impact"] = round(flt(aop.refund_amount), 2)
		if aop.adjustment_amount > 0 and charges_summary.get("reimbursements", 0.0) == 0.0:
			charges_summary["reimbursements"] = round(flt(aop.adjustment_amount), 2)

		profitability = {
			"revenue": round(flt(aop.revenue), 2),
			"product_cogs": round(flt(aop.product_cogs), 2),
			"amazon_expenses": charges_summary["total_amazon_charges"],
			"reimbursements": round(flt(aop.adjustment_amount), 2),
			"refund_impact": round(flt(aop.refund_amount), 2),
			"net_profit": round(flt(aop.net_profit), 2),
			"profit_margin": round(flt(aop.profit_margin), 2),
			"status": aop.calculation_status or "Estimated",
		}

		items = []
		for it in aop.items:
			items.append({
				"item_code": it.item_code or "-",
				"sku": it.sku or it.asin or "-",
				"asin": it.asin or "-",
				"item_name": it.item_name or it.item_code or "-",
				"qty": flt(it.qty),
				"revenue": round(flt(it.revenue), 2),
				"cogs": round(flt(it.product_cogs), 2),
				"direct_amazon_fees": round(flt(it.direct_amazon_fees), 2),
				"allocated_shipping": round(flt(it.allocated_order_expense), 2),
				"refund": round(flt(it.refund_amount), 2),
				"reimbursement": 0.0,
				"net_profit": round(flt(it.net_profit), 2),
				"profit_margin": round(flt(it.profit_margin), 2),
			})
	else:
		profitability = {
			"revenue": 0.0,
			"product_cogs": 0.0,
			"amazon_expenses": charges_summary.get("total_amazon_charges", 0.0),
			"reimbursements": charges_summary.get("reimbursements", 0.0),
			"refund_impact": charges_summary.get("refund_impact", 0.0),
			"net_profit": 0.0,
			"profit_margin": 0.0,
			"status": "Estimated",
		}
		items = []

	return {
		"is_amazon_invoice": True,
		"has_amazon_data": True,
		"amazon_order_id": amazon_order_id,
		"aop_name": aop.name if aop else None,
		"invoice_name": invoice_name,
		"currency": (aop.currency if aop else "INR") or "INR",
		"charge_lines": charge_lines,
		"charges": charges_summary,
		"accounting_status": accounting_status,
		"profitability": profitability,
		"items": items,
	}


def render_amazon_profitability_html(data: dict) -> str:
	"""
	Renders a clean, responsive, aesthetic card view for Sales Invoice form.
	Shows:
	1. Amazon Charges Table with columns:
	   - Charge Type
	   - Description
	   - Amount
	   - Tax Amount
	   - Total Amount
	   - Amazon Financial Transaction
	   - Accounting Status
	2. Total Amazon Charges & Summary metrics
	3. Amazon Accounting Status badge
	4. Amazon Profitability Summary (Revenue, COGS, Charges, Net Profit, Margin, Status)
	5. Button: View Amazon Financials
	"""
	if not data or not data.get("is_amazon_invoice"):
		return ""

	amazon_order_id = data.get("amazon_order_id", "")
	aop_name = data.get("aop_name", "")
	inv_name = data.get("invoice_name", "")

	if not data.get("has_amazon_data"):
		return f"""
		<div class="amazon-profitability-container" style="margin: 10px 0; padding: 18px; border: 1px solid #f39c12; border-radius: 8px; background-color: #fffdf7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
			<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
				<div>
					<h4 style="margin: 0; color: #b7950b; font-size: 15px; font-weight: 600;">
						<i class="fa fa-amazon" style="margin-right: 6px; color: #ea580c;"></i> Amazon Charges &amp; Profitability
					</h4>
					<p style="margin: 6px 0 0 0; color: #7d6608; font-size: 13px;">
						Amazon Order ID: <strong>{amazon_order_id}</strong> &bull; Status: <span class="badge" style="background-color: #f39c12; color: #fff; font-weight: 600; padding: 3px 8px;">Amazon financial data pending</span>
					</p>
					<p style="margin: 6px 0 0 0; color: #8a6d3b; font-size: 13px; font-weight: 500;">
						<i class="fa fa-info-circle"></i> Amazon financial data not available yet.
					</p>
					<p style="margin: 4px 0 0 0; color: #a08244; font-size: 12px;">
						Financial transactions have not yet been synchronized from Amazon SP-API for this order. No charges or misleading zero values are calculated until financial events arrive.
					</p>
				</div>
				<div>
					<button class="btn btn-default btn-xs" onclick="frappe.set_route('List', 'Amazon Financial Transaction', {{'amazon_order_id': '{amazon_order_id}'}})">
						<i class="fa fa-list"></i> View Amazon Financials
					</button>
				</div>
			</div>
		</div>
		"""

	charge_lines = data.get("charge_lines", [])
	charges = data.get("charges", {})
	prof = data.get("profitability", {})
	items = data.get("items", [])
	prof_status = prof.get("status", "Estimated")
	act_status = data.get("accounting_status", "Pending")

	# Accounting status badge coloring
	act_color = "#28a745" if act_status == "Accounted" else ("#17a2b8" if act_status == "Partially Accounted" else ("#dc3545" if act_status == "Failed" else "#f39c12"))
	act_text_color = "#fff"

	# Profitability status badge coloring
	prof_color = "#28a745" if prof_status == "Final" else ("#17a2b8" if prof_status == "Recalculated" else ("#fd7e14" if "Missing" in prof_status else "#ffc107"))
	prof_text_color = "#fff" if prof_status in ["Final", "Recalculated", "Estimated / COGS Missing"] else "#212529"

	# Profit color
	net_profit = prof.get("net_profit", 0.0)
	margin = prof.get("profit_margin", 0.0)
	profit_color = "#28a745" if net_profit >= 0 else "#dc3545"

	def cur(val):
		return f"₹{fmt_money(abs(val), precision=2)}" if val is not None else "₹0.00"

	# Render Amazon Charges Table Rows
	charge_rows_html = ""
	for cl in charge_lines:
		cl_status = cl.get("accounting_status", "Pending")
		cl_status_color = "#28a745" if cl_status == "Posted" else ("#17a2b8" if cl_status == "Ready" else ("#dc3545" if cl_status == "Failed" else "#f39c12"))
		aft_link = cl.get("amazon_financial_transaction", "")
		aft_display = aft_link if len(aft_link) <= 24 else f"{aft_link[:10]}...{aft_link[-10:]}"

		charge_rows_html += f"""
		<tr style="border-bottom: 1px solid #edf2f7; font-size: 12px;">
			<td style="padding: 8px 10px; font-weight: 600; color: #1e293b;">{cl.get('charge_type')}</td>
			<td style="padding: 8px 10px; color: #475569;">{cl.get('description')}</td>
			<td style="padding: 8px 10px; text-align: right; font-weight: 500;">{cur(cl.get('amount'))}</td>
			<td style="padding: 8px 10px; text-align: right; color: #64748b;">{cur(cl.get('tax_amount'))}</td>
			<td style="padding: 8px 10px; text-align: right; font-weight: 600; color: #0f172a;">{cur(cl.get('total_amount'))}</td>
			<td style="padding: 8px 10px;">
				<a href="/app/amazon-financial-transaction/{aft_link}" target="_blank" style="color: #2563eb; text-decoration: underline;" title="{aft_link}">
					{aft_display}
				</a>
			</td>
			<td style="padding: 8px 10px; text-align: center;">
				<span class="badge" style="background-color: {cl_status_color}; color: #fff; padding: 2px 7px; font-size: 10px; border-radius: 4px;">
					{cl_status}
				</span>
			</td>
		</tr>
		"""

	# Generate item rows
	item_rows_html = ""
	for it in items:
		it_profit_color = "#28a745" if it.get("net_profit", 0) >= 0 else "#dc3545"
		item_rows_html += f"""
		<tr style="border-bottom: 1px solid #edf2f7; font-size: 12px;">
			<td style="padding: 8px 10px;"><strong>{it.get('item_code')}</strong></td>
			<td style="padding: 8px 10px; color: #6c757d;">{it.get('sku')}</td>
			<td style="padding: 8px 10px; text-align: center;">{int(it.get('qty', 1))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('revenue'))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('cogs'))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('direct_amazon_fees'))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('allocated_shipping'))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('refund'))}</td>
			<td style="padding: 8px 10px; text-align: right;">{cur(it.get('reimbursement'))}</td>
			<td style="padding: 8px 10px; text-align: right; font-weight: 600; color: {it_profit_color};">{cur(it.get('net_profit'))}</td>
			<td style="padding: 8px 10px; text-align: right; font-weight: 600; color: {it_profit_color};">{it.get('profit_margin', 0.0)}%</td>
		</tr>
		"""

	return f"""
	<div class="amazon-charges-card" style="margin: 10px 0 20px 0; border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.05); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
		<!-- Header -->
		<div style="padding: 12px 18px; border-bottom: 1px solid #edf2f7; background-color: #f8fafc; border-top-left-radius: 8px; border-top-right-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
			<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
				<span style="font-size: 15px; font-weight: 600; color: #1e293b;">
					<i class="fa fa-amazon" style="color: #ea580c; margin-right: 5px;"></i> Amazon Charges &amp; Profitability
				</span>
				<span style="font-size: 12px; color: #64748b;">Order: <strong style="color: #334155;">{amazon_order_id}</strong></span>
				<span class="badge" style="background-color: {act_color}; color: {act_text_color}; padding: 3px 8px; font-size: 11px; border-radius: 4px; font-weight: 600;" title="Accounting Status">
					Accounting: {act_status}
				</span>
				<span class="badge" style="background-color: {prof_color}; color: {prof_text_color}; padding: 3px 8px; font-size: 11px; border-radius: 4px; font-weight: 600;" title="Profitability Status">
					Profitability: {prof_status}
				</span>
			</div>
			<div style="display: flex; gap: 8px; flex-wrap: wrap;">
				<button class="btn btn-default btn-xs" onclick="frappe.set_route('List', 'Amazon Financial Transaction', {{'amazon_order_id': '{amazon_order_id}'}})">
					<i class="fa fa-list"></i> View Amazon Financials
				</button>
				{f'''<button class="btn btn-default btn-xs" onclick="frappe.set_route('Form', 'Amazon Order Profitability', '{aop_name}')">
					<i class="fa fa-external-link"></i> View Profitability
				</button>''' if aop_name else ''}
				<button class="btn btn-default btn-xs" onclick="frappe.call({{method: 'ecommerce_integrations.amazon.doctype.amazon_order_profitability.sales_invoice_amazon_profitability.recalculate_sales_invoice_profitability', args: {{invoice_name: '{inv_name}'}}, callback: function() {{ cur_frm.reload_doc(); }}}})">
					<i class="fa fa-refresh"></i> Recalculate
				</button>
				{f'''<button class="btn btn-primary btn-xs" onclick="frappe.call({{method: 'ecommerce_integrations.amazon.doctype.amazon_order_profitability.sales_invoice_amazon_profitability.post_sales_invoice_amazon_accounting', args: {{invoice_name: '{inv_name}'}}, callback: function(r) {{ if (r.message && r.message.success) {{ frappe.show_alert({{message: 'Amazon charges posted successfully', indicator: 'green'}}); cur_frm.reload_doc(); }} }}}})">
					<i class="fa fa-book"></i> Post Accounting
				</button>''' if act_status != 'Accounted' else ''}
			</div>
		</div>

		<!-- 1. Amazon Charges Table -->
		<div style="padding: 16px;">
			<div style="border: 1px solid #e2e8f0; border-radius: 6px; overflow-x: auto;">
				<table style="width: 100%; border-collapse: collapse; min-width: 650px;">
					<thead>
						<tr style="background-color: #f1f5f9; font-size: 11px; text-transform: uppercase; color: #475569; border-bottom: 1px solid #cbd5e1;">
							<th style="padding: 8px 10px; text-align: left;">Charge Type</th>
							<th style="padding: 8px 10px; text-align: left;">Description</th>
							<th style="padding: 8px 10px; text-align: right;">Amount</th>
							<th style="padding: 8px 10px; text-align: right;">Tax Amount</th>
							<th style="padding: 8px 10px; text-align: right;">Total Amount</th>
							<th style="padding: 8px 10px; text-align: left;">Amazon Financial Transaction</th>
							<th style="padding: 8px 10px; text-align: center;">Accounting Status</th>
						</tr>
					</thead>
					<tbody>
						{charge_rows_html}
					</tbody>
					<tfoot>
						<tr style="background-color: #f8fafc; font-weight: 700; border-top: 2px solid #cbd5e1; font-size: 12px;">
							<td colspan="2" style="padding: 10px; color: #0f172a;">
								Total Amazon Charges (Seller Operating Expenses):
							</td>
							<td style="padding: 10px; text-align: right; color: #0f172a; font-size: 13px;">
								{cur(charges.get('total_amazon_charges'))}
							</td>
							<td style="padding: 10px; text-align: right; color: #64748b;">
								{cur(charges.get('fee_gst'))} (Fee GST)
							</td>
							<td style="padding: 10px; text-align: right; color: #0f172a; font-size: 13px;">
								{cur(round(charges.get('total_amazon_charges', 0.0) + charges.get('fee_gst', 0.0), 2))}
							</td>
							<td colspan="2" style="padding: 10px; color: #64748b; font-size: 11px;">
								TCS: {cur(charges.get('tcs'))} &bull; TDS: {cur(charges.get('tds'))}
							</td>
						</tr>
					</tfoot>
				</table>
			</div>
			<p style="margin: 6px 0 0 0; color: #94a3b8; font-size: 11px;">
				* Total Amazon Charges represents seller operating expenses (Commission, Fixed Closing, Shipping/Easy Ship, Other Fees). TCS/TDS are balance sheet tax assets and excluded from expense totals.
			</p>
		</div>

		<!-- 2. Summary Grid: Accounting & Profitability -->
		<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; padding: 0 16px 16px 16px;">
			<!-- Accounting Status & Summary -->
			<div style="border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px; background: #ffffff;">
				<h5 style="margin: 0 0 10px 0; font-size: 13px; font-weight: 700; color: #334155; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">
					Amazon Accounting Summary
				</h5>
				<table style="width: 100%; font-size: 13px; line-height: 1.8;">
					<tr>
						<td style="color: #64748b;">Amazon Accounting Status</td>
						<td style="text-align: right;">
							<span class="badge" style="background-color: {act_color}; color: #fff; font-size: 11px; padding: 2px 7px;">
								{act_status}
							</span>
						</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Total Amazon Charges</td>
						<td style="text-align: right; font-weight: 700; color: #0f172a;">{cur(charges.get('total_amazon_charges'))}</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Recoverable Fee GST</td>
						<td style="text-align: right; font-weight: 500;">{cur(charges.get('fee_gst'))}</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Tax Withheld (TCS + TDS)</td>
						<td style="text-align: right; font-weight: 500;">{cur(round(charges.get('tcs', 0.0) + charges.get('tds', 0.0), 2))}</td>
					</tr>
					{"<tr><td style='color: #dc3545;'>Customer Refund Impact</td><td style='text-align: right; font-weight: 500; color: #dc3545;'>-" + cur(charges.get('refund_impact')) + "</td></tr>" if charges.get('refund_impact') else ""}
					{"<tr><td style='color: #28a745;'>Amazon Reimbursements</td><td style='text-align: right; font-weight: 500; color: #28a745;'>+" + cur(charges.get('reimbursements')) + "</td></tr>" if charges.get('reimbursements') else ""}
				</table>
			</div>

			<!-- Product Profitability -->
			<div style="border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px; background: #ffffff;">
				<h5 style="margin: 0 0 10px 0; font-size: 13px; font-weight: 700; color: #334155; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">
					Amazon Profitability
				</h5>
				<table style="width: 100%; font-size: 13px; line-height: 1.8;">
					<tr>
						<td style="color: #64748b;">Sales Revenue</td>
						<td style="text-align: right; font-weight: 500;">{cur(prof.get('revenue'))}</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Product COGS</td>
						<td style="text-align: right; font-weight: 500;">{cur(prof.get('product_cogs'))}</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Total Amazon Charges</td>
						<td style="text-align: right; font-weight: 500;">{cur(prof.get('amazon_expenses'))}</td>
					</tr>
					{"<tr><td style='color: #28a745;'>Reimbursements</td><td style='text-align: right; font-weight: 500; color: #28a745;'>+" + cur(prof.get('reimbursements')) + "</td></tr>" if prof.get('reimbursements') else ""}
					<tr style="border-top: 1px solid #cbd5e1; font-weight: 700;">
						<td style="padding-top: 6px; color: #0f172a;">Net Profit</td>
						<td style="padding-top: 6px; text-align: right; color: {profit_color}; font-size: 14px;">{cur(net_profit)}</td>
					</tr>
					<tr>
						<td style="color: #64748b; font-weight: 600;">Profit Margin</td>
						<td style="text-align: right; font-weight: 700; color: {profit_color}; font-size: 14px;">{margin}%</td>
					</tr>
					<tr>
						<td style="color: #64748b;">Profitability Status</td>
						<td style="text-align: right;"><span class="badge" style="background-color: {prof_color}; color: {prof_text_color}; font-size: 10px;">{prof_status}</span></td>
					</tr>
				</table>
			</div>
		</div>

		<!-- 3. Item-Level Breakdown Table -->
		{f'''
		<div style="padding: 0 16px 16px 16px;">
			<div style="border: 1px solid #e2e8f0; border-radius: 6px; overflow-x: auto;">
				<div style="padding: 8px 12px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">
					<h5 style="margin: 0; font-size: 12px; font-weight: 700; color: #334155; text-transform: uppercase; letter-spacing: 0.5px;">
						Item-Level Breakdown
					</h5>
				</div>
				<table style="width: 100%; border-collapse: collapse; min-width: 600px;">
					<thead>
						<tr style="background-color: #f1f5f9; font-size: 11px; text-transform: uppercase; color: #475569; border-bottom: 1px solid #cbd5e1;">
							<th style="padding: 8px 10px; text-align: left;">Item Code</th>
							<th style="padding: 8px 10px; text-align: left;">SKU / ASIN</th>
							<th style="padding: 8px 10px; text-align: center;">Qty</th>
							<th style="padding: 8px 10px; text-align: right;">Revenue</th>
							<th style="padding: 8px 10px; text-align: right;">COGS</th>
							<th style="padding: 8px 10px; text-align: right;">Direct Fees</th>
							<th style="padding: 8px 10px; text-align: right;">Shipping Exp</th>
							<th style="padding: 8px 10px; text-align: right;">Refund</th>
							<th style="padding: 8px 10px; text-align: right;">Reimbursement</th>
							<th style="padding: 8px 10px; text-align: right;">Net Profit</th>
							<th style="padding: 8px 10px; text-align: right;">Margin %</th>
						</tr>
					</thead>
					<tbody>
						{item_rows_html}
					</tbody>
				</table>
			</div>
			<p style="margin: 6px 0 0 0; color: #94a3b8; font-size: 11px; text-align: right;">
				* Read-only analytical view &bull; Standard customer invoice totals and general ledger entries are strictly preserved.
			</p>
		</div>
		''' if items else ''}
	</div>
	"""


@frappe.whitelist()
def get_sales_invoice_amazon_profitability(invoice_name: str) -> dict:
	"""
	Whitelisted API endpoint for Sales Invoice desk form.
	Synchronizes child table and summary fields, and returns parsed data and rendered HTML.
	"""
	data = update_sales_invoice_amazon_charges(invoice_name)
	html = render_amazon_profitability_html(data)
	return {
		"success": True,
		"data": data,
		"html": html,
	}


@frappe.whitelist()
def recalculate_sales_invoice_profitability(invoice_name: str) -> dict:
	"""
	Triggers recalculation for the linked Amazon Order ID and returns updated view.
	"""
	oid = get_sales_invoice_amazon_order_id(invoice_name)
	if not oid:
		return {"success": False, "message": "Not an Amazon Sales Invoice."}

	from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import (
		calculate_amazon_order_profitability,
	)

	aop_name = calculate_amazon_order_profitability(oid)
	frappe.db.commit()

	data = update_sales_invoice_amazon_charges(invoice_name)
	html = render_amazon_profitability_html(data)
	return {
		"success": True,
		"aop_name": aop_name,
		"data": data,
		"html": html,
	}


@frappe.whitelist()
def post_sales_invoice_amazon_accounting(invoice_name: str) -> dict:
	"""
	Requirement 3 & 4 Automatic/Idempotent Accounting Trigger:
	When the Amazon Financial Transaction becomes eligible for accounting:
	1. Finds linked Sales Invoice.
	2. Checks whether it has already been accounted.
	3. Checks existing Journal Entry / Payment Entry links.
	4. If already posted, does nothing.
	5. Otherwise creates the correct Amazon accounting entry using Phase 3 post_amazon_transaction.
	6. Updates accounting status.
	7. Links the JE back to the Amazon Financial Transaction.
	8. Recalculates Amazon Order Profitability.
	Must be fully idempotent.
	"""
	oid = get_sales_invoice_amazon_order_id(invoice_name)
	if not oid:
		return {"success": False, "message": "Not an Amazon Sales Invoice."}

	txs = frappe.get_all(
		"Amazon Financial Transaction",
		filters={"amazon_order_id": oid},
		fields=["name", "amazon_transaction_id", "accounting_status", "journal_entry", "total_amount", "sales_invoice"],
	)

	if not txs:
		return {"success": False, "message": "No Amazon Financial Transactions found for this order."}

	company = frappe.db.get_value("Sales Invoice", invoice_name, "company")
	amz_setting = frappe.db.get_value("Amazon SP API Settings", {"company": company}, "name")
	if not amz_setting:
		amz_setting = frappe.db.get_single_value("Amazon SP API Settings", "name") or "Amazon SP API Settings"

	from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import AmazonRepository

	repo = AmazonRepository(amz_setting)

	posted_jes = []
	errors = []

	for t in txs:
		# Ensure sales_invoice link is stored on the transaction
		if t.sales_invoice != invoice_name:
			frappe.db.set_value("Amazon Financial Transaction", t.name, "sales_invoice", invoice_name, update_modified=False)

		# Idempotency check 1: already posted with valid JE
		if t.accounting_status == "Posted" and t.journal_entry and frappe.db.exists("Journal Entry", t.journal_entry):
			posted_jes.append(t.journal_entry)
			continue

		# Idempotency check 2: matching JE already in ERPNext
		existing_je = frappe.db.get_value(
			"Journal Entry",
			{"user_remark": ["like", f"%{t.amazon_transaction_id}%"], "docstatus": ["!=", 2]},
			"name",
		)
		if existing_je:
			frappe.db.set_value(
				"Amazon Financial Transaction",
				t.name,
				{"journal_entry": existing_je, "accounting_status": "Posted", "sales_invoice": invoice_name},
				update_modified=False,
			)
			posted_jes.append(existing_je)
			continue

		if t.total_amount == 0.0:
			continue

		# Post transaction via Phase 3 repository
		try:
			je_name = repo.post_amazon_transaction(t.name)
			if je_name:
				posted_jes.append(je_name)
		except Exception as e:
			err_str = str(e)
			errors.append(f"{t.name}: {err_str}")
			frappe.log_error(title=f"Failed posting Amazon transaction {t.name}", message=frappe.get_traceback())

	# Recalculate profitability
	from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import (
		calculate_amazon_order_profitability,
	)

	calculate_amazon_order_profitability(oid)
	frappe.db.commit()

	# Update invoice view & child table
	data = update_sales_invoice_amazon_charges(invoice_name)
	html = render_amazon_profitability_html(data)

	return {
		"success": len(errors) == 0,
		"posted_jes": posted_jes,
		"errors": errors,
		"data": data,
		"html": html,
	}
