# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, now_datetime, getdate


class AmazonProfitabilityEngine:
	"""
	Phase 4: Amazon Order & SKU Profitability Engine
	- Ingests ERPNext Revenue (authoritative from Sales Invoice / Sales Order)
	- Ingests ERPNext Product COGS (SLE valuation rate / Item valuation / standard rate)
	- Categorizes Amazon Financial Breakdowns (Item direct vs Order allocatable)
	- Allocates Order-level expenses (Proportional by Revenue, Qty, or Equal)
	- Incorporates Amazon Refunds and Reimbursements
	- Retains ProductAdsPayment at Account Level unless explicitly allocated
	- Manages Estimated vs Final vs Recalculated lifecycle
	"""

	def __init__(self, allocation_method: str = "Proportional by Revenue"):
		"""
		allocation_method options:
		- 'Proportional by Revenue' (default)
		- 'Proportional by Quantity'
		- 'Equal'
		"""
		self.allocation_method = allocation_method

	def get_item_cogs(self, item_code: str, order_date=None, qty: float = 1.0, alt_sku: str | None = None, item_name: str | None = None) -> float:
		"""
		Resolves ERPNext Product COGS for an item:
		1. Historical Stock Ledger Entry valuation rate around/before order date.
		2. Item master valuation_rate.
		3. Item master standard_rate.
		4. Latest purchase rate / Item Price.
		Also searches by internal SKU / item_name if ASIN item has no stock valuation.
		"""
		if qty <= 0:
			return 0.0

		candidate_codes = []
		if item_code:
			candidate_codes.append(item_code)
		if alt_sku and alt_sku not in candidate_codes and frappe.db.exists("Item", alt_sku):
			candidate_codes.append(alt_sku)
		if item_name and item_name not in candidate_codes and frappe.db.exists("Item", item_name):
			candidate_codes.append(item_name)

		for code in candidate_codes:
			unit_cost = 0.0

			# 1. Historical SLE on or before order_date
			if order_date:
				sle_val = frappe.db.sql(
					"""
					select valuation_rate from `tabStock Ledger Entry`
					where item_code = %s and posting_date <= %s and valuation_rate > 0
					order by posting_date desc, posting_time desc
					limit 1
					""",
					(code, order_date),
				)
				if sle_val and sle_val[0][0]:
					unit_cost = flt(sle_val[0][0])

			if not unit_cost:
				latest_sle = frappe.db.sql(
					"""
					select valuation_rate from `tabStock Ledger Entry`
					where item_code = %s and valuation_rate > 0
					order by posting_date desc, posting_time desc
					limit 1
					""",
					code,
				)
				if latest_sle and latest_sle[0][0]:
					unit_cost = flt(latest_sle[0][0])

			# 2. Item Master valuation_rate or standard_rate
			if not unit_cost and frappe.db.exists("Item", code):
				rates = frappe.db.get_value("Item", code, ["valuation_rate", "standard_rate"], as_dict=True)
				if rates:
					unit_cost = flt(rates.valuation_rate) or flt(rates.standard_rate)

			# 3. Item Price (Buying)
			if not unit_cost:
				ip = frappe.db.sql(
					"""
					select price_list_rate from `tabItem Price`
					where item_code = %s and buying = 1 and price_list_rate > 0
					order by valid_from desc limit 1
					""",
					code,
				)
				if ip and ip[0][0]:
					unit_cost = flt(ip[0][0])

			if unit_cost > 0:
				return round(unit_cost * flt(qty), 2)

		return 0.0

	def calculate_order_profitability(self, amazon_order_id: str) -> str | None:
		"""
		Calculates or recalculates profitability for a single Amazon Order ID.
		Returns name of Amazon Order Profitability document.
		"""
		if not amazon_order_id:
			return None

		# 1. Fetch Sales Order and Sales Invoice
		so_name = frappe.db.get_value("Sales Order", {"amazon_order_id": amazon_order_id}, "name")
		sinv_name = None
		order_date = None
		customer = None
		company = None
		currency = "INR"

		items_data = {}  # key -> dict of details

		if so_name:
			so = frappe.get_doc("Sales Order", so_name)
			order_date = so.transaction_date
			customer = so.customer
			company = so.company
			currency = so.currency or "INR"

			# Find linked Sales Invoice
			inv_rows = frappe.db.sql(
				"select distinct parent from `tabSales Invoice Item` where sales_order=%s",
				so_name,
				as_dict=True,
			)
			for r in inv_rows:
				if frappe.db.get_value("Sales Invoice", r.parent, "docstatus") == 1:
					sinv_name = r.parent
					break

			for row in so.items:
				key = row.item_code
				items_data[key] = {
					"item_code": row.item_code,
					"sku": None,
					"asin": None,
					"item_name": row.item_name,
					"qty": flt(row.qty),
					"revenue": flt(row.net_amount) if row.net_amount else flt(row.amount),
					"product_cogs": 0.0,
					"direct_amazon_fees": 0.0,
					"allocated_order_expense": 0.0,
					"refund_amount": 0.0,
				}

		if not sinv_name:
			sinv_name = frappe.db.get_value("Sales Invoice", {"custom_marketplace_order": amazon_order_id}, "name")

		# If Sales Invoice exists, override revenue with authoritative invoice amounts
		if sinv_name:
			sinv = frappe.get_doc("Sales Invoice", sinv_name)
			order_date = order_date or sinv.posting_date
			customer = customer or sinv.customer
			company = company or sinv.company
			currency = sinv.currency or currency

			# Clear and re-populate from invoice items if present
			if sinv.items:
				items_data.clear()
				for row in sinv.items:
					key = row.item_code
					items_data[key] = {
						"item_code": row.item_code,
						"sku": None,
						"asin": None,
						"item_name": row.item_name,
						"qty": flt(row.qty),
						# Exclude output GST / taxes: use net_amount (tax-exclusive)
						"revenue": flt(row.net_amount) if row.net_amount else flt(row.amount),
						"product_cogs": 0.0,
						"direct_amazon_fees": 0.0,
						"allocated_order_expense": 0.0,
						"refund_amount": 0.0,
					}

		# 2. Fetch all Amazon Financial Transactions linked to this order
		txs = frappe.get_all(
			"Amazon Financial Transaction",
			filters={"amazon_order_id": amazon_order_id},
			fields=["name", "transaction_type", "total_amount", "posted_date", "seller_sku", "asin"],
		)

		has_financials = len(txs) > 0
		has_shipment = any(t.transaction_type == "Shipment" for t in txs)

		# If neither SO nor SI exists, populate item skeleton from Amazon financial breakdowns
		if not items_data and has_financials:
			for t in txs:
				bds = frappe.get_all(
					"Amazon Financial Breakdown",
					filters={"parent": t.name},
					fields=["seller_sku", "category", "fee_type", "breakdown_type", "amount", "allocation_scope"],
				)
				for b in bds:
					sku = b.seller_sku or t.seller_sku
					if sku and sku not in items_data:
						# Resolve item_code from SKU/ASIN
						it_code = sku
						if not frappe.db.exists("Item", it_code):
							matched_it = frappe.db.get_value("Item", {"item_code": sku}, "name")
							it_code = matched_it or sku
						items_data[sku] = {
							"item_code": it_code if frappe.db.exists("Item", it_code) else None,
							"sku": sku,
							"asin": t.asin,
							"item_name": sku,
							"qty": 1.0,
							"revenue": 0.0,
							"product_cogs": 0.0,
							"direct_amazon_fees": 0.0,
							"allocated_order_expense": 0.0,
							"refund_amount": 0.0,
						}
					# If Sales breakdown exists in Amazon without SI/SO
					if b.breakdown_type in ["OurPricePrincipal", "ProductCharges"] and b.amount > 0:
						target = items_data.get(sku) or (list(items_data.values())[0] if items_data else None)
						if target:
							target["revenue"] += flt(b.amount)

		# Calculate COGS for each item
		for itm_key, d in items_data.items():
			d["product_cogs"] = self.get_item_cogs(
				item_code=d.get("item_code"),
				order_date=order_date,
				qty=d.get("qty") or 1.0,
				alt_sku=d.get("sku"),
				item_name=d.get("item_name"),
			)

		# 3. Process Amazon Breakdowns for Fees, Easy Ship, Refunds, Adjustments
		tot_direct_amazon_fees = 0.0
		tot_order_shipping_expense = 0.0
		tot_other_order_expense = 0.0
		tot_refund_amount = 0.0
		tot_adjustment_amount = 0.0

		for t in txs:
			bds = frappe.get_all(
				"Amazon Financial Breakdown",
				filters={"parent": t.name},
				fields=["category", "fee_type", "breakdown_type", "allocation_scope", "amount", "seller_sku"],
			)
			# Filter leaf breakdowns: if transaction has 'Item' scope breakdowns, ignore top-level duplicate 'Order' rollups
			has_item_scope = any(b.allocation_scope == "Item" for b in bds)
			target_bds = [b for b in bds if b.allocation_scope == "Item"] if has_item_scope else bds

			is_shipping_tx = False
			if t.transaction_type == "ServiceFee":
				# Check if description or fee indicates Easy Ship / MFN Postage
				try:
					import json
					raw_obj = json.loads(frappe.db.get_value("Amazon Financial Transaction", t.name, "raw_response") or "{}")
					desc = (raw_obj.get("description") or "").lower()
					if any(s in desc for s in ["postage", "easyship", "easy ship", "shipping", "delivery"]):
						is_shipping_tx = True
				except Exception:
					pass

			for b in target_bds:
				amt = flt(b.amount)
				cat_lower = (b.category or "").lower()
				bt_lower = (b.breakdown_type or "").lower()
				ft_lower = (b.fee_type or "").lower()

				# EXCLUDE pure statutory taxes from expenses:
				# TCS, TDS, and recoverable input GST on fees
				is_statutory = any(s in cat_lower or s in bt_lower or s in ft_lower for s in ["tcs", "tds", "taxcollected", "taxdeducted", "itemtds"])
				is_tax_on_fee = (bt_lower in ["tax", "igst", "cgst", "sgst"] or ft_lower in ["tax", "igst", "cgst", "sgst"]) and (
					cat_lower in ["amazonfees", "expenses", "fee", "refunded expenses"]
				)
				if is_statutory or is_tax_on_fee:
					continue

				# REFUND Handling
				if t.transaction_type == "Refund" or "refund" in cat_lower:
					# Negative customer price / principal is a refund deduction from revenue/payout
					if any(p in bt_lower or p in ft_lower for p in ["ourpriceprincipal", "productcharges", "shippingprincipal", "refunded sales"]):
						deduction = abs(amt)
						tot_refund_amount += deduction
						# If attached to SKU
						matched_key = self._find_matching_item_key(b.seller_sku, items_data)
						if matched_key:
							items_data[matched_key]["refund_amount"] += deduction
					elif cat_lower in ["amazonfees", "refunded expenses"] and amt > 0:
						# Fee refunded back by Amazon -> reduces amazon fees / increases net profit
						tot_direct_amazon_fees -= amt
					continue

				# ADJUSTMENT / SAFET REIMBURSEMENT
				if t.transaction_type == "Adjustment" or "reimbursement" in cat_lower or "reimbursement" in bt_lower or "reimbursement" in ft_lower:
					if amt > 0:
						tot_adjustment_amount += amt
					continue

				# SHIPPING / EASY SHIP EXPENSES
				is_shipping = is_shipping_tx or any(s in bt_lower or s in ft_lower for s in ["easyship", "easy ship", "shippingcharge", "fba weight handling", "postage", "mfnpostagefee"])
				if is_shipping:
					expense = abs(amt) if amt < 0 else amt
					tot_order_shipping_expense += expense
					continue

				# DIRECT ITEM-LEVEL AMAZON FEES (Commission, Closing Fee, Pick & Pack)
				if b.allocation_scope == "Item" and cat_lower in ["amazonfees", "expenses", "fee"] and not any(p in bt_lower for p in ["productcharges", "sales", "principal"]):
					expense = abs(amt) if amt < 0 else amt
					tot_direct_amazon_fees += expense
					matched_key = self._find_matching_item_key(b.seller_sku, items_data)
					if matched_key:
						items_data[matched_key]["direct_amazon_fees"] += expense
					continue

				# ORDER-LEVEL AMAZON FEES
				if b.allocation_scope == "Order" and cat_lower in ["amazonfees", "expenses", "fee"] and not any(p in bt_lower for p in ["productcharges", "sales", "principal"]):
					expense = abs(amt) if amt < 0 else amt
					tot_other_order_expense += expense
					continue

		# 4. Allocate Order-level expenses (Shipping + Other Order expenses) across items
		allocatable_pool = round(tot_order_shipping_expense + tot_other_order_expense, 2)
		self._allocate_order_expenses(allocatable_pool, items_data)

		# 5. Determine Calculation Status
		has_missing_cogs = any((d.get("product_cogs") or 0.0) == 0.0 and (d.get("qty") or 0.0) > 0 for d in items_data.values())

		if has_missing_cogs:
			status = "Estimated / COGS Missing"
		elif sinv_name and has_shipment:
			status = "Final"
		elif has_shipment:
			status = "Final" if so_name else "Estimated"
		else:
			status = "Estimated"

		# Check if existing record exists
		existing = frappe.db.get_value("Amazon Order Profitability", {"amazon_order_id": amazon_order_id}, "name")
		if existing:
			doc = frappe.get_doc("Amazon Order Profitability", existing)
			doc.calculation_status = "Recalculated" if doc.calculation_status == "Final" else status
			doc.items.clear()
		else:
			doc = frappe.new_doc("Amazon Order Profitability")
			doc.amazon_order_id = amazon_order_id
			doc.calculation_status = status

		# Fill Header Fields
		doc.sales_order = so_name
		doc.sales_invoice = sinv_name
		doc.order_date = order_date or (txs[0].posted_date if txs and txs[0].posted_date else frappe.utils.nowdate())
		doc.customer = customer
		doc.company = company or frappe.defaults.get_user_default("Company")
		doc.currency = currency
		doc.marketplace = "Amazon.in"

		# Total header amounts
		doc.revenue = round(sum(d["revenue"] for d in items_data.values()), 2)
		doc.product_cogs = round(sum(d["product_cogs"] for d in items_data.values()), 2)
		doc.amazon_fees = round(tot_direct_amazon_fees, 2)
		doc.shipping_expense = round(tot_order_shipping_expense, 2)
		doc.advertising_expense = 0.0  # Maintained at account level
		doc.other_amazon_expense = round(tot_other_order_expense, 2)
		doc.refund_amount = round(tot_refund_amount, 2)
		doc.adjustment_amount = round(tot_adjustment_amount, 2)
		doc.calculated_on = now_datetime()
		doc.notes = f"Allocated via {self.allocation_method} across {len(items_data)} items."

		# Populate Child Table Items
		for itm_key, d in items_data.items():
			doc.append("items", {
				"item_code": d.get("item_code"),
				"sku": d.get("sku"),
				"asin": d.get("asin"),
				"item_name": d.get("item_name") or d.get("item_code"),
				"qty": d.get("qty") or 1.0,
				"revenue": round(d.get("revenue", 0.0), 2),
				"product_cogs": round(d.get("product_cogs", 0.0), 2),
				"direct_amazon_fees": round(d.get("direct_amazon_fees", 0.0), 2),
				"allocated_order_expense": round(d.get("allocated_order_expense", 0.0), 2),
				"refund_amount": round(d.get("refund_amount", 0.0), 2),
			})

		doc.save()

		# Update Sales Invoice summary fields if linked
		if sinv_name and frappe.db.exists("Sales Invoice", sinv_name):
			try:
				frappe.db.set_value(
					"Sales Invoice",
					sinv_name,
					{
						"amazon_order_profit": doc.net_profit,
						"amazon_profit_margin": doc.profit_margin,
						"amazon_profitability_status": doc.calculation_status,
						"amazon_profitability_link": doc.name,
					},
					update_modified=False,
				)
			except Exception:
				pass

		frappe.db.commit()
		return doc.name

	def _find_matching_item_key(self, sku: str | None, items_data: dict) -> str | None:
		if not items_data:
			return None
		if sku and sku in items_data:
			return sku
		# Search by sku attribute inside dict
		if sku:
			for k, v in items_data.items():
				if v.get("sku") == sku or v.get("item_code") == sku:
					return k
		# Default fallback if single item order
		if len(items_data) == 1:
			return list(items_data.keys())[0]
		return None

	def _allocate_order_expenses(self, total_expense: float, items_data: dict):
		if not items_data or total_expense <= 0:
			return

		items_list = list(items_data.values())
		n = len(items_list)

		if self.allocation_method == "Proportional by Quantity":
			tot_qty = sum(flt(d["qty"]) for d in items_list)
			if tot_qty > 0:
				allocated_sum = 0.0
				for idx, d in enumerate(items_list):
					if idx == n - 1:
						d["allocated_order_expense"] = round(total_expense - allocated_sum, 2)
					else:
						share = round(total_expense * (flt(d["qty"]) / tot_qty), 2)
						d["allocated_order_expense"] = share
						allocated_sum += share
				return

		elif self.allocation_method == "Equal":
			share = round(total_expense / n, 2)
			allocated_sum = 0.0
			for idx, d in enumerate(items_list):
				if idx == n - 1:
					d["allocated_order_expense"] = round(total_expense - allocated_sum, 2)
				else:
					d["allocated_order_expense"] = share
					allocated_sum += share
			return

		# Default: 'Proportional by Revenue'
		tot_rev = sum(flt(d["revenue"]) for d in items_list)
		if tot_rev > 0:
			allocated_sum = 0.0
			for idx, d in enumerate(items_list):
				if idx == n - 1:
					d["allocated_order_expense"] = round(total_expense - allocated_sum, 2)
				else:
					share = round(total_expense * (flt(d["revenue"]) / tot_rev), 2)
					d["allocated_order_expense"] = share
					allocated_sum += share
		else:
			# Fallback to equal allocation if zero revenue
			share = round(total_expense / n, 2)
			allocated_sum = 0.0
			for idx, d in enumerate(items_list):
				if idx == n - 1:
					d["allocated_order_expense"] = round(total_expense - allocated_sum, 2)
				else:
					d["allocated_order_expense"] = share
					allocated_sum += share


def calculate_amazon_order_profitability(amazon_order_id: str, allocation_method: str = "Proportional by Revenue") -> str | None:
	engine = AmazonProfitabilityEngine(allocation_method=allocation_method)
	return engine.calculate_order_profitability(amazon_order_id)


def recalculate_batch_profitability(order_ids: list[str], allocation_method: str = "Proportional by Revenue") -> dict:
	engine = AmazonProfitabilityEngine(allocation_method=allocation_method)
	success = 0
	failed = 0
	for oid in order_ids:
		try:
			engine.calculate_order_profitability(oid)
			success += 1
		except Exception as e:
			frappe.log_error(f"Failed to calculate profitability for {oid}: {str(e)}", "Amazon Profitability")
			failed += 1
	return {"success": success, "failed": failed}
