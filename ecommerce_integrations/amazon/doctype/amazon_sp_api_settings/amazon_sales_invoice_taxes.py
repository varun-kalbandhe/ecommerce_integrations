# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.utils import flt
from ecommerce_integrations.amazon.doctype.amazon_account_mapping.amazon_account_mapping import get_account_mapping
from ecommerce_integrations.amazon.doctype.amazon_order_profitability.sales_invoice_amazon_profitability import (
	extract_sales_invoice_charge_lines,
	get_sales_invoice_amazon_order_id,
)


def is_amazon_tax_row(tax_row, acc_map, company: str) -> bool:
	"""
	Identifies whether a row in Sales Taxes and Charges represents an Amazon seller charge.
	"""
	desc = (tax_row.description or "").lower()
	acc = tax_row.account_head or ""
	
	if any(desc.startswith(prefix) for prefix in [
		"amazon ", "gst on amazon", "amazon settlement clearing"
	]):
		return True

	if acc_map:
		amz_accounts = {
			acc_map.clearing_account,
		}
		if acc_map.input_igst_account:
			amz_accounts.add(acc_map.input_igst_account)
		for r in getattr(acc_map, "rules", []):
			if r.erpnext_account:
				amz_accounts.add(r.erpnext_account)
		if acc in amz_accounts and "amazon" in acc.lower():
			return True

	fee_gst_acc = frappe.db.get_value("Account", {"company": company, "account_name": "GST Expense"}, "name")
	if fee_gst_acc and acc == fee_gst_acc and "amazon" in desc:
		return True

	return False


def get_fee_gst_account(company: str, acc_map=None) -> str:
	"""
	Resolves the appropriate expense account for applicable Amazon Fee GST.
	Must NOT use an Inward/Input GST tax account that india_compliance blocks on Sales Invoices.
	"""
	acc = frappe.db.get_value("Account", {"company": company, "account_name": "GST Expense", "is_group": 0}, "name")
	if acc:
		return acc
	acc = frappe.db.get_value("Account", {"company": company, "account_name": ["like", "%Amazon Fee GST%"], "is_group": 0}, "name")
	if acc:
		return acc
	acc = frappe.db.get_value("Account", {"company": company, "account_name": ["like", "%GST%Expense%"], "is_group": 0}, "name")
	if acc:
		return acc
	# Fallback to Other Amazon Fees
	return frappe.db.get_value("Account", {"company": company, "account_name": "Other Amazon Fees", "is_group": 0}, "name")


def sync_amazon_charges_to_sales_invoice(doc, method=None):
	"""
	STANDARD ERPNext Sales Invoice Taxes & Charges Amazon Flow:
	
	Amazon -> Amazon Financial Transaction -> Amazon charges identified
	-> Use standard Sales Invoice 'Sales Taxes and Charges' table
	-> Use Amazon expense accounts as Account Heads
	-> Let standard ERPNext Sales Invoice accounting create the GL entries.
	
	Guarantees:
	1. Charges appear in the standard Sales Taxes and Charges table.
	2. Seller expense accounts (Commission, Closing, Easy Ship, Packing, Other, Fee GST) are used.
	3. Customer payable / Grand Total is NOT changed (net ₹0 impact via offsetting clearing leg).
	4. Standard ERPNext accounting debits the expense accounts and credits Amazon Clearing Account.
	5. 100% Idempotent: Repeated saves or syncs cleanly replace Amazon tax rows without duplication.
	"""
	if doc.docstatus == 2:
		return

	# Only adjust draft invoices; submitted invoices preserve posted taxes
	if doc.docstatus == 1:
		return

	order_id = get_sales_invoice_amazon_order_id(doc)
	if not order_id:
		return

	# Check for linked Amazon Financial Transactions
	has_afts = frappe.db.exists("Amazon Financial Transaction", {"amazon_order_id": order_id})
	if not has_afts and doc.name:
		has_afts = frappe.db.exists("Amazon Financial Transaction", {"sales_invoice": doc.name})
	if not has_afts:
		return

	charge_lines, summary, status = extract_sales_invoice_charge_lines(order_id, doc.name)
	if not charge_lines and not summary:
		return

	acc_map = get_account_mapping(doc.company)
	if not acc_map:
		return

	# 1. Cleanly remove previous Amazon rows from taxes table (idempotency)
	existing_taxes = []
	for t in doc.get("taxes") or []:
		if not is_amazon_tax_row(t, acc_map, doc.company):
			existing_taxes.append(t)
	doc.set("taxes", existing_taxes)

	total_deductions = 0.0
	cost_center = doc.cost_center or frappe.get_cached_value("Company", doc.company, "cost_center")

	# 2. Commission
	commission = flt(summary.get("commission"), 2)
	if commission > 0:
		acc, _ = acc_map.get_account_for_breakdown("AmazonFees", "Commission")
		if acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": acc,
				"description": "Amazon Commission",
				"tax_amount": -commission,
				"tax_amount_after_discount_amount": -commission,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += commission

	# 3. Fixed Closing Fee
	closing_fee = flt(summary.get("fixed_closing_fee"), 2)
	if closing_fee > 0:
		acc, _ = acc_map.get_account_for_breakdown("AmazonFees", "FixedClosingFee")
		if acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": acc,
				"description": "Amazon Fixed Closing Fee",
				"tax_amount": -closing_fee,
				"tax_amount_after_discount_amount": -closing_fee,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += closing_fee

	# 4. Easy Ship Fee
	easy_ship = flt(summary.get("easy_ship"), 2)
	if easy_ship > 0:
		acc, _ = acc_map.get_account_for_breakdown("ServiceFee", "MFNPostageFee")
		if acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": acc,
				"description": "Amazon Easy Ship Fee",
				"tax_amount": -easy_ship,
				"tax_amount_after_discount_amount": -easy_ship,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += easy_ship

	# 5. Packing Fee
	packing_fee = flt(summary.get("packing_fee"), 2)
	if packing_fee > 0:
		acc, _ = acc_map.get_account_for_breakdown("AmazonFees", "Packing")
		if acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": acc,
				"description": "Amazon Packing Fee",
				"tax_amount": -packing_fee,
				"tax_amount_after_discount_amount": -packing_fee,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += packing_fee

	# 6. Other Amazon Fees
	other_fees = flt(summary.get("other_fees"), 2)
	if other_fees > 0:
		acc, _ = acc_map.get_account_for_breakdown("AmazonFees", "Other")
		if acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": acc,
				"description": "Other Amazon Fees",
				"tax_amount": -other_fees,
				"tax_amount_after_discount_amount": -other_fees,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += other_fees

	# 7. Fee GST
	fee_gst = flt(summary.get("fee_gst"), 2)
	if fee_gst > 0:
		fee_gst_acc = get_fee_gst_account(doc.company, acc_map)
		if fee_gst_acc:
			doc.append("taxes", {
				"charge_type": "Actual",
				"account_head": fee_gst_acc,
				"description": "GST on Amazon Fees",
				"tax_amount": -fee_gst,
				"tax_amount_after_discount_amount": -fee_gst,
				"cost_center": cost_center,
				"dont_recompute_tax": 1,
			})
			total_deductions += fee_gst

	# 8. Balancing Clearing Leg (Off-sets seller deductions to preserve customer payable exactly)
	if total_deductions > 0 and acc_map.clearing_account:
		doc.append("taxes", {
			"charge_type": "Actual",
			"account_head": acc_map.clearing_account,
			"description": "Amazon Settlement Clearing Offset",
			"tax_amount": round(total_deductions, 2),
			"tax_amount_after_discount_amount": round(total_deductions, 2),
			"cost_center": cost_center,
			"dont_recompute_tax": 1,
		})

	# Re-calculate taxes and totals
	doc.run_method("calculate_taxes_and_totals")


def on_sales_invoice_submit(doc, method=None):
	"""
	Marks linked Amazon Financial Transactions as Accounted via this Sales Invoice.
	Prevents separate custom Journal Entry generation.
	"""
	order_id = get_sales_invoice_amazon_order_id(doc)
	if not order_id:
		return

	# Check if this invoice accounted Amazon charges in taxes
	has_amz_taxes = False
	for t in doc.get("taxes") or []:
		desc = (t.description or "").lower()
		if desc.startswith("amazon ") or desc.startswith("gst on amazon"):
			has_amz_taxes = True
			break

	if not has_amz_taxes:
		return

	# Update linked AFTs to Accounted status and link invoice
	txs = frappe.get_all(
		"Amazon Financial Transaction",
		filters={"amazon_order_id": order_id},
		fields=["name", "sales_invoice", "accounting_status"],
	)

	for tx in txs:
		frappe.db.set_value(
			"Amazon Financial Transaction",
			tx.name,
			{
				"sales_invoice": doc.name,
				"accounting_status": "Accounted",
				"posting_date": doc.posting_date,
				"accounting_error": None,
			},
			update_modified=False,
		)
