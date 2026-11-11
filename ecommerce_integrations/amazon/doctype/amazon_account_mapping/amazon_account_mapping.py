# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AmazonAccountMapping(Document):
	def get_account_for_breakdown(self, category: str, fee_type: str | None = None, breakdown_type: str | None = None) -> tuple[str | None, str]:
		"""
		Resolves (erpnext_account, account_role) for a given breakdown.
		Role is 'Debit' or 'Credit'.
		Fallback order:
		1. Specific rule in rules table matching category + fee_type
		2. Rule matching category or breakdown_type
		3. Dedicated master mapping field on document (TCS, TDS, Input GST, Clearing, etc.)
		"""
		cat_lower = (category or "").lower().strip()
		ft_lower = (fee_type or "").lower().strip() if fee_type else ""
		bt_lower = (breakdown_type or "").lower().strip() if breakdown_type else ""

		# 1. Customer Sales / Principal / Output Tax / Discounts (Debtors credit to settle invoice)
		is_customer_sale = (
			cat_lower in ["sales", "productcharges"]
			or any(s in bt_lower or s in ft_lower for s in [
				"principal", "ourpriceprincipal", "productcharges",
				"ourpricetax", "shippingtax", "ourpricediscount", "ourpricetaxdiscount",
				"promorebates"
			])
		)
		if is_customer_sale:
			return self.receivable_account, "Credit"

		# 2. Statutory Withholdings & Receivables (Balance Sheet assets)
		# TCS
		if any(k in cat_lower or k in bt_lower or k in ft_lower for k in ["tcs", "taxcollectedatsource"]):
			if self.tcs_receivable_account:
				return self.tcs_receivable_account, "Debit"

		# TDS
		if any(k in cat_lower or k in bt_lower or k in ft_lower for k in ["tds", "taxdeductedatsource", "itemtds", "section 194-o", "section194o"]):
			if self.tds_receivable_account:
				return self.tds_receivable_account, "Debit"

		# 3. GST on Amazon Fees (Input Tax Credit)
		is_fee_gst = (
			(cat_lower in ["amazonfees", "expenses", "fee", "other-transaction", "servicefee"]
			 and any(t in bt_lower or t in ft_lower for t in ["tax", "gst", "igst", "cgst", "sgst"]))
			or (not is_customer_sale and (any(t in ft_lower for t in ["igst", "cgst", "sgst"]) or bt_lower in ["tax", "igst", "cgst", "sgst"]))
		)
		if is_fee_gst:
			if "igst" in bt_lower or "igst" in ft_lower:
				return self.input_igst_account or self.input_cgst_account, "Debit"
			elif "cgst" in bt_lower or "cgst" in ft_lower:
				return self.input_cgst_account, "Debit"
			elif "sgst" in bt_lower or "sgst" in ft_lower:
				return self.input_sgst_account, "Debit"
			elif self.input_igst_account:
				return self.input_igst_account, "Debit"

		# 4. Settlement Payout / Fund Transfer
		if any(f in cat_lower or f in bt_lower or f in ft_lower for f in ["fundtransfer", "payout"]) or (
			cat_lower in ["transfer", "fundtransfer"] or bt_lower in ["transfer", "fundtransfer"] or ft_lower in ["transfer", "fundtransfer"]
		):
			return self.bank_account, "Debit"

		# 5. Specific Amazon Seller Fee Matching by fee_type
		if ft_lower:
			for r in self.rules:
				rule_cat = (r.amazon_category or "").lower().strip()
				rule_ft = (r.fee_type or "").lower().strip() if r.fee_type else ""
				if rule_ft and rule_ft == ft_lower:
					if not rule_cat or rule_cat in [cat_lower, bt_lower, "amazonfees", "expenses", "servicefee"]:
						return r.erpnext_account, r.account_role or "Debit"
				if rule_cat and rule_cat == ft_lower and not rule_ft:
					return r.erpnext_account, r.account_role or "Debit"

			# Specific Amazon Expense Account fallbacks by fee category
			if any(k in ft_lower or k in cat_lower for k in ["commission", "referral"]):
				acc = frappe.db.get_value("Account", {"account_name": ["like", "%Commission%"], "company": self.company, "is_group": 0}, "name")
				if acc:
					return acc, "Debit"
			if any(k in ft_lower or k in cat_lower for k in ["closing", "fixedclosing"]):
				acc = frappe.db.get_value("Account", {"account_name": ["like", "%Closing%"], "company": self.company, "is_group": 0}, "name")
				if acc:
					return acc, "Debit"
			if any(k in ft_lower or k in cat_lower for k in ["easyship", "postage", "shipping", "mfnpostagefee"]):
				acc = frappe.db.get_value("Account", {"account_name": ["like", "%Easy Ship%"], "company": self.company, "is_group": 0}, "name")
				if acc:
					return acc, "Debit"
			if any(k in ft_lower or k in cat_lower for k in ["packing", "fbaweighthandling", "pickandpack"]):
				acc = frappe.db.get_value("Account", {"account_name": ["like", "%Packing%"], "company": self.company, "is_group": 0}, "name")
				if acc:
					return acc, "Debit"

		# 6. Custom Rules Table Check by category
		for r in self.rules:
			rule_cat = (r.amazon_category or "").lower().strip()
			rule_ft = (r.fee_type or "").lower().strip() if r.fee_type else ""
			if not rule_ft and rule_cat == cat_lower:
				return r.erpnext_account, r.account_role or "Debit"

		# 7. Fallback for other Amazon fees
		if "other" in ft_lower or cat_lower in ["amazonfees", "expenses"]:
			acc = (
				frappe.db.get_value("Account", {"account_name": "Other Amazon Fees", "company": self.company, "is_group": 0}, "name")
				or frappe.db.get_value("Account", {"account_name": "Amazon Fees", "company": self.company, "is_group": 0}, "name")
			)
			if acc:
				return acc, "Debit"

		return None, "Debit"


def get_account_mapping(company: str | None = None) -> AmazonAccountMapping | None:
	if company:
		mapping_name = frappe.db.get_value("Amazon Account Mapping", {"company": company}, "name")
		if mapping_name:
			return frappe.get_doc("Amazon Account Mapping", mapping_name)
	
	# Fallback to the first available Amazon Account Mapping
	mapping_name = frappe.db.get_value("Amazon Account Mapping", {}, "name")
	if mapping_name:
		return frappe.get_doc("Amazon Account Mapping", mapping_name)
	return None

