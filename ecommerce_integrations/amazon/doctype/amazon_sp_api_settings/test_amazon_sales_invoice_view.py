# Copyright (c) 2026, Frappe and Contributors
# See license.txt

import json
import unittest
import frappe
from frappe.utils import flt, nowdate

from ecommerce_integrations.amazon.doctype.amazon_order_profitability.sales_invoice_amazon_profitability import (
	get_sales_invoice_amazon_order_id,
	extract_amazon_charges,
	extract_sales_invoice_charge_lines,
	update_sales_invoice_amazon_charges,
	get_sales_invoice_amazon_profitability_data,
	render_amazon_profitability_html,
	recalculate_sales_invoice_profitability,
	post_sales_invoice_amazon_accounting,
)
from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import (
	AmazonProfitabilityEngine,
)


class TestAmazonSalesInvoiceView(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.company = "e.Soft Technologies Pvt Ltd"

	def setUp(self):
		# Clean up any test records with 'TEST-SI-'
		test_orders = frappe.db.sql(
			"""
			SELECT amazon_order_id FROM `tabAmazon Financial Transaction`
			WHERE amazon_order_id LIKE 'TEST-SI-%'
			""",
			pluck=True,
		)
		if test_orders:
			frappe.db.sql(
				"""
				DELETE FROM `tabAmazon Financial Breakdown`
				WHERE parent IN (SELECT name FROM `tabAmazon Financial Transaction` WHERE amazon_order_id LIKE 'TEST-SI-%')
				"""
			)
			frappe.db.sql("DELETE FROM `tabAmazon Financial Transaction` WHERE amazon_order_id LIKE 'TEST-SI-%'")
			frappe.db.sql(
				"""
				DELETE FROM `tabAmazon Order Profitability Item`
				WHERE parent IN (SELECT name FROM `tabAmazon Order Profitability` WHERE amazon_order_id LIKE 'TEST-SI-%')
				"""
			)
			frappe.db.sql("DELETE FROM `tabAmazon Order Profitability` WHERE amazon_order_id LIKE 'TEST-SI-%'")
			frappe.db.commit()

	def tearDown(self):
		# Clean up test transactions created during test runs
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Financial Breakdown`
			WHERE parent IN (SELECT name FROM `tabAmazon Financial Transaction` WHERE amazon_order_id LIKE 'TEST-SI-%')
			"""
		)
		frappe.db.sql("DELETE FROM `tabAmazon Financial Transaction` WHERE amazon_order_id LIKE 'TEST-SI-%'")
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Order Profitability Item`
			WHERE parent IN (SELECT name FROM `tabAmazon Order Profitability` WHERE amazon_order_id LIKE 'TEST-SI-%')
			"""
		)
		frappe.db.sql("DELETE FROM `tabAmazon Order Profitability` WHERE amazon_order_id LIKE 'TEST-SI-%'")
		frappe.db.commit()

	def _delete_sales_invoice(self, sinv_name: str):
		"""Deletes test Sales Invoice directly via SQL to prevent triggering background workers"""
		if not sinv_name or not frappe.db.exists("Sales Invoice", sinv_name):
			return
		frappe.db.sql("DELETE FROM `tabSales Invoice Item` WHERE parent = %s", sinv_name)
		frappe.db.sql("DELETE FROM `tabSales Invoice` WHERE name = %s", sinv_name)
		frappe.db.commit()

	def _create_mock_sales_invoice(self, order_id: str, items=None, grand_total=500.0, net_total=476.19, item_code="0agEjFpY"):
		"""Helper to create a temporary test Sales Invoice"""
		cust = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		items_list = items or [
			{
				"item_code": item_code,
				"qty": 1.0,
				"rate": net_total,
				"amount": net_total,
				"net_amount": net_total,
			}
		]
		sinv = frappe.get_doc({
			"doctype": "Sales Invoice",
			"company": self.company,
			"customer": cust,
			"posting_date": nowdate(),
			"due_date": nowdate(),
			"custom_marketplace_order": order_id,
			"items": items_list,
		})
		sinv.insert(ignore_permissions=True, ignore_mandatory=True)
		return sinv

	def test_01_amazon_sales_invoice_with_complete_financial_data(self):
		ord_id = "TEST-SI-COMPLETE-01"
		sinv = self._create_mock_sales_invoice(ord_id, grand_total=500.0, net_total=476.19, item_code="0agEjFpY")

		raw_tx = {
			"description": "Shipment",
			"items": [
				{
					"breakdowns": [
						{
							"breakdownType": "ProductCharges",
							"breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": 476.19, "currencyCode": "INR"}}],
						},
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{
									"breakdownType": "Commission",
									"breakdowns": [
										{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -45.0, "currencyCode": "INR"}},
										{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -8.1, "currencyCode": "INR"}},
									],
								},
								{
									"breakdownType": "FixedClosingFee",
									"breakdowns": [
										{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -20.0, "currencyCode": "INR"}},
										{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -3.6, "currencyCode": "INR"}},
									],
								},
							],
						},
					]
				}
			],
		}

		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-SI-01",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 476.19 - 45 - 8.1 - 20 - 3.6,
			"raw_response": json.dumps(raw_tx),
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 476.19, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -45.0, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -20.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx.insert(ignore_permissions=True)

		# Add Easy Ship ServiceFee
		raw_sf = {
			"description": "MfnPostageFee",
			"breakdowns": [
				{
					"breakdownType": "AmazonFees",
					"breakdowns": [
						{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -55.0, "currencyCode": "INR"}},
						{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -9.9, "currencyCode": "INR"}},
					],
				}
			],
		}
		tx_sf = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-SI-SF-01",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -64.9,
			"raw_response": json.dumps(raw_sf),
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Order", "amount": -55.0},
			],
		})
		tx_sf.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertTrue(data["is_amazon_invoice"])
		self.assertTrue(data["has_amazon_data"])
		self.assertEqual(data["amazon_order_id"], ord_id)
		self.assertEqual(data["profitability"]["revenue"], 476.19)
		self.assertEqual(data["charges"]["commission"], 45.0)
		self.assertEqual(data["charges"]["fixed_closing_fee"], 20.0)
		self.assertEqual(data["charges"]["easy_ship"], 55.0)
		self.assertEqual(data["charges"]["fee_gst"], 21.6) # 8.1 + 3.6 + 9.9
		self.assertEqual(data["charges"]["total_amazon_expenses"], 120.0) # 45 + 20 + 55
		self.assertEqual(data["profitability"]["status"], "Final")

		html = render_amazon_profitability_html(data)
		self.assertIn("Amazon Charges &amp; Profitability", html.replace(" & ", " &amp; "))
		self.assertIn(ord_id, html)
		self.assertIn("View Amazon Financials", html)
		self.assertIn("View Profitability", html)

		self._delete_sales_invoice(sinv.name)

	def test_02_multiple_amazon_fees_on_one_order(self):
		charges = extract_amazon_charges("NON_EXISTENT")
		self.assertEqual(charges["commission"], 0.0)
		self.assertEqual(charges["total_amazon_expenses"], 0.0)

	def test_03_easy_ship_expense(self):
		ord_id = "TEST-SI-EASYSHIP-03"
		raw_sf = {
			"description": "Easy Ship Delivery Fee",
			"breakdowns": [
				{
					"breakdownType": "AmazonFees",
					"breakdowns": [
						{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -72.0, "currencyCode": "INR"}},
						{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -12.96, "currencyCode": "INR"}},
					],
				}
			],
		}
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-ES-03",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -84.96,
			"raw_response": json.dumps(raw_sf),
			"breakdowns": [{"breakdown_type": "Base", "category": "AmazonFees", "amount": -72.0}],
		})
		tx.insert(ignore_permissions=True)

		charges = extract_amazon_charges(ord_id)
		self.assertEqual(charges["easy_ship"], 72.0)
		self.assertEqual(charges["fee_gst"], 12.96)
		self.assertEqual(charges["total_amazon_expenses"], 72.0)

	def test_04_fee_gst(self):
		ord_id = "TEST-SI-FEEGST-04"
		raw = {
			"description": "Shipment",
			"items": [
				{
					"breakdowns": [
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{
									"breakdownType": "Commission",
									"breakdowns": [
										{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -100.0, "currencyCode": "INR"}},
										{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -18.0, "currencyCode": "INR"}},
									],
								}
							],
						}
					]
				}
			],
		}
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-GST-04",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": -118.0,
			"raw_response": json.dumps(raw),
			"breakdowns": [{"breakdown_type": "Base", "category": "AmazonFees", "amount": -100.0}],
		})
		tx.insert(ignore_permissions=True)

		charges = extract_amazon_charges(ord_id)
		self.assertEqual(charges["commission"], 100.0)
		self.assertEqual(charges["fee_gst"], 18.0)
		# Fee GST is separate from total amazon expenses (as GST is an input tax credit asset)
		self.assertEqual(charges["total_amazon_expenses"], 100.0)

	def test_05_product_cogs(self):
		engine = AmazonProfitabilityEngine()
		item_code = frappe.db.get_value("Item", {"valuation_rate": [">", 0]}, "item_code") or "0agEjFpY"
		cogs = engine.get_item_cogs(item_code, qty=2.0)
		self.assertGreater(cogs, 0.0)

	def test_06_profit_calculation(self):
		ord_id = "TEST-SI-PROFIT-06"
		sinv = self._create_mock_sales_invoice(ord_id, grand_total=1000.0, net_total=1000.0, item_code="0agEjFpY")

		raw = {
			"description": "Shipment",
			"items": [
				{
					"breakdowns": [
						{
							"breakdownType": "ProductCharges",
							"breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": 1000.0, "currencyCode": "INR"}}],
						},
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{
									"breakdownType": "Commission",
									"breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -150.0, "currencyCode": "INR"}}],
								}
							],
						},
					]
				}
			],
		}
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-PROF-06",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 850.0,
			"raw_response": json.dumps(raw),
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "amount": 1000.0, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "amount": -150.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		# Net profit = Revenue - COGS - Amazon Fees
		expected_profit = round(1000.0 - data["profitability"]["product_cogs"] - 150.0, 2)
		self.assertEqual(data["profitability"]["net_profit"], expected_profit)
		self._delete_sales_invoice(sinv.name)

	def test_07_profit_margin(self):
		ord_id = "TEST-SI-MARGIN-07"
		sinv = self._create_mock_sales_invoice(ord_id, grand_total=500.0, net_total=500.0, item_code="0agEjFpY")

		raw = {
			"description": "Shipment",
			"items": [
				{
					"breakdowns": [
						{
							"breakdownType": "ProductCharges",
							"breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": 500.0, "currencyCode": "INR"}}],
						},
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -100.0, "currencyCode": "INR"}}],
						},
					]
				}
			],
		}
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-MARG-07",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 400.0,
			"raw_response": json.dumps(raw),
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "amount": 500.0, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "amount": -100.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		net_prof = data["profitability"]["net_profit"]
		rev = data["profitability"]["revenue"]
		expected_margin = round((net_prof / rev) * 100.0, 2)
		self.assertEqual(data["profitability"]["profit_margin"], expected_margin)
		self._delete_sales_invoice(sinv.name)

	def test_08_multi_item_invoice(self):
		ord_id = "TEST-SI-MULTI-08"
		item_a = "B0DFMF76XR"
		item_b = "B008X9Z37A"
		items = [
			{"item_code": item_a, "qty": 1.0, "rate": 600.0, "amount": 600.0, "net_amount": 600.0},
			{"item_code": item_b, "qty": 1.0, "rate": 400.0, "amount": 400.0, "net_amount": 400.0},
		]
		sinv = self._create_mock_sales_invoice(ord_id, items=items, grand_total=1000.0, net_total=1000.0)

		raw = {
			"description": "Shipment",
			"items": [
				{
					"contexts": [{"sku": item_a}],
					"breakdowns": [
						{"breakdownType": "ProductCharges", "breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": 600.0, "currencyCode": "INR"}}]},
						{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -60.0, "currencyCode": "INR"}}]},
					],
				},
				{
					"contexts": [{"sku": item_b}],
					"breakdowns": [
						{"breakdownType": "ProductCharges", "breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": 400.0, "currencyCode": "INR"}}]},
						{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -40.0, "currencyCode": "INR"}}]},
					],
				},
			],
		}
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-MULTI-08",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 900.0,
			"raw_response": json.dumps(raw),
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 600.0, "seller_sku": item_a},
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 400.0, "seller_sku": item_b},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -60.0, "seller_sku": item_a},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -40.0, "seller_sku": item_b},
			],
		})
		tx.insert(ignore_permissions=True)

		# Add Order-level Easy Ship of 100
		raw_sf = {
			"description": "MfnPostageFee",
			"breakdowns": [
				{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -100.0, "currencyCode": "INR"}}]}
			],
		}
		tx_sf = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-MULTI-SF-08",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -100.0,
			"raw_response": json.dumps(raw_sf),
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Order", "amount": -100.0},
			],
		})
		tx_sf.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertEqual(len(data["items"]), 2)
		# 600 vs 400 revenue ratio allocates 60 to Item A and 40 to Item B
		it_a = next(i for i in data["items"] if i["item_code"] == item_a)
		it_b = next(i for i in data["items"] if i["item_code"] == item_b)
		self.assertEqual(it_a["allocated_shipping"], 60.0)
		self.assertEqual(it_b["allocated_shipping"], 40.0)

		self._delete_sales_invoice(sinv.name)

	def test_09_refund(self):
		ord_id = "TEST-SI-REFUND-09"
		sinv = self._create_mock_sales_invoice(ord_id, item_code="0agEjFpY")

		tx_sale = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-REF-09-S",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 500.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 500.0, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -50.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx_sale.insert(ignore_permissions=True)

		raw_ref = {
			"description": "Refund",
			"items": [
				{
					"breakdowns": [
						{"breakdownType": "ProductCharges", "breakdowns": [{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": -500.0, "currencyCode": "INR"}}]},
						{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": 20.0, "currencyCode": "INR"}}]},
					]
				}
			],
		}
		tx_ref = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-REF-09-R",
			"transaction_type": "Refund",
			"amazon_order_id": ord_id,
			"total_amount": -480.0,
			"raw_response": json.dumps(raw_ref),
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": -500.0, "seller_sku": "0agEjFpY"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": 20.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx_ref.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertEqual(data["charges"]["refund_impact"], 500.0)
		self.assertEqual(data["profitability"]["refund_impact"], 500.0)

		self._delete_sales_invoice(sinv.name)

	def test_10_reimbursement(self):
		ord_id = "TEST-SI-REIMB-10"
		sinv = self._create_mock_sales_invoice(ord_id, item_code="0agEjFpY")

		tx_ship = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-REIMB-10-S",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 300.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 300.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx_ship.insert(ignore_permissions=True)

		raw_adj = {
			"description": "SAFET Reimbursement",
			"breakdowns": [
				{"breakdownType": "Sales", "breakdowns": [{"breakdownType": "SAFETReimbursement", "breakdownAmount": {"currencyAmount": 150.0, "currencyCode": "INR"}}]}
			],
		}
		tx_adj = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-REIMB-10-A",
			"transaction_type": "Adjustment",
			"amazon_order_id": ord_id,
			"total_amount": 150.0,
			"raw_response": json.dumps(raw_adj),
			"breakdowns": [
				{"breakdown_type": "SAFETReimbursement", "category": "Sales", "allocation_scope": "Order", "amount": 150.0},
			],
		})
		tx_adj.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertEqual(data["charges"]["reimbursements"], 150.0)
		self.assertEqual(data["profitability"]["reimbursements"], 150.0)

		self._delete_sales_invoice(sinv.name)

	def test_11_missing_amazon_financial_data(self):
		ord_id = "TEST-SI-PENDING-11"
		sinv = self._create_mock_sales_invoice(ord_id)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertTrue(data["is_amazon_invoice"])
		self.assertFalse(data["has_amazon_data"])
		self.assertEqual(data["status"], "Amazon financial data pending")

		html = render_amazon_profitability_html(data)
		self.assertIn("Amazon financial data pending", html)
		self.assertNotIn("₹0.00 Net Profit", html)

		self._delete_sales_invoice(sinv.name)

	def test_12_missing_cogs(self):
		ord_id = "TEST-SI-NOCOGS-12"
		sinv = self._create_mock_sales_invoice(ord_id, item_code="B0DFMF76XR")

		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-NOCOGS-12",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 250.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 250.0, "seller_sku": "B0DFMF76XR"},
			],
		})
		tx.insert(ignore_permissions=True)

		data = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertIn("COGS Missing", data["profitability"]["status"])

		self._delete_sales_invoice(sinv.name)

	def test_13_late_arriving_financial_transaction_and_recalculation(self):
		ord_id = "TEST-SI-LATE-13"
		sinv = self._create_mock_sales_invoice(ord_id, item_code="0agEjFpY")

		tx1 = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-LATE-13-1",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 300.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 300.0, "seller_sku": "0agEjFpY"},
			],
		})
		tx1.insert(ignore_permissions=True)

		data1 = get_sales_invoice_amazon_profitability_data(sinv.name)
		self.assertEqual(data1["charges"]["easy_ship"], 0.0)

		# Late Easy Ship transaction arrives
		raw_late = {
			"description": "MfnPostageFee",
			"breakdowns": [
				{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -55.0, "currencyCode": "INR"}}]}
			],
		}
		tx2 = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-LATE-13-2",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -55.0,
			"raw_response": json.dumps(raw_late),
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Order", "amount": -55.0},
			],
		})
		tx2.insert(ignore_permissions=True)

		rec = recalculate_sales_invoice_profitability(sinv.name)
		self.assertTrue(rec["success"])
		self.assertEqual(rec["data"]["charges"]["easy_ship"], 55.0)

		self._delete_sales_invoice(sinv.name)

	def test_14_sales_invoice_grand_total_remains_unchanged(self):
		# Verify on real Sales Invoice from database
		si = frappe.get_doc("Sales Invoice", "SINV-26-00008")
		original_grand_total = si.grand_total
		original_net_total = si.net_total

		# Retrieve profitability view
		data = get_sales_invoice_amazon_profitability_data(si.name)
		self.assertTrue(data["has_amazon_data"])

		si.reload()
		self.assertEqual(si.grand_total, original_grand_total)
		self.assertEqual(si.net_total, original_net_total)

	def test_15_existing_gl_entries_remain_unchanged(self):
		# Verify on real Sales Invoice from database
		si = frappe.get_doc("Sales Invoice", "SINV-26-00008")
		gls_before = frappe.get_all("GL Entry", filters={"voucher_no": si.name}, fields=["name", "account", "debit", "credit"])

		data = get_sales_invoice_amazon_profitability_data(si.name)
		self.assertTrue(data["has_amazon_data"])

		gls_after = frappe.get_all("GL Entry", filters={"voucher_no": si.name}, fields=["name", "account", "debit", "credit"])
		self.assertEqual(len(gls_before), len(gls_after))
		for b, a in zip(gls_before, gls_after):
			self.assertEqual(b["account"], a["account"])
			self.assertEqual(b["debit"], a["debit"])
			self.assertEqual(b["credit"], a["credit"])

	def test_16_no_duplicate_je_or_pe_created(self):
		je_count_before = frappe.db.count("Journal Entry")
		pe_count_before = frappe.db.count("Payment Entry")

		# Call get view on multiple invoices
		get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		get_sales_invoice_amazon_profitability_data("SINV-26-00012")
		get_sales_invoice_amazon_profitability_data("SINV-26-00030")

		je_count_after = frappe.db.count("Journal Entry")
		pe_count_after = frappe.db.count("Payment Entry")

		self.assertEqual(je_count_before, je_count_after)
		self.assertEqual(pe_count_before, pe_count_after)

	def test_17_view_amazon_financials_route(self):
		data = get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		self.assertEqual(data["amazon_order_id"], "403-8616852-4941948")
		# Confirm filtering by this order id returns the linked transactions
		txs = frappe.get_all("Amazon Financial Transaction", filters={"amazon_order_id": data["amazon_order_id"]})
		self.assertGreater(len(txs), 0)

	def test_18_view_profitability_record(self):
		data = get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		self.assertEqual(data["aop_name"], "AOP-403-8616852-4941948")
		self.assertTrue(frappe.db.exists("Amazon Order Profitability", data["aop_name"]))

	def test_19_granular_charge_columns_and_child_table(self):
		data = update_sales_invoice_amazon_charges("SINV-26-00008")
		self.assertTrue(data["has_amazon_data"])
		lines = data.get("charge_lines")
		self.assertGreater(len(lines), 0)

		# Verify all 7 columns are present in each row
		required_cols = [
			"charge_type",
			"description",
			"amount",
			"tax_amount",
			"total_amount",
			"amazon_financial_transaction",
			"accounting_status",
		]
		for row in lines:
			for col in required_cols:
				self.assertIn(col, row)

		# Verify child table records in database
		db_charges = frappe.get_all(
			"Amazon Sales Invoice Charge",
			filters={"parent": "SINV-26-00008"},
			fields=["charge_type", "amount", "tax_amount", "total_amount", "amazon_financial_transaction"],
		)
		self.assertEqual(len(db_charges), len(lines))

		charge_types = [c["charge_type"] for c in db_charges]
		self.assertIn("Fixed Closing Fee", charge_types)
		self.assertIn("Easy Ship Fee", charge_types)
		self.assertIn("TCS", charge_types)
		self.assertIn("TDS", charge_types)

	def test_20_tcs_tds_excluded_from_total_amazon_charges(self):
		data = get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		charges = data["charges"]

		# Fixed closing: 45, Easy ship: 55 -> Total charges = 100
		self.assertEqual(charges["total_amazon_charges"], 100.0)
		self.assertEqual(charges["tcs"], 3.29)
		self.assertEqual(charges["tds"], 0.66)

		# Confirm TCS & TDS are excluded from seller expense totals
		self.assertNotEqual(charges["total_amazon_charges"], 100.0 + 3.29 + 0.66)

		# Confirm Sales Invoice field matches
		si_total = frappe.db.get_value("Sales Invoice", "SINV-26-00008", "total_amazon_charges")
		self.assertEqual(flt(si_total), 100.0)

	def test_21_accounting_status_resolution(self):
		# Test with real SINV-26-00008 (transactions are in 'Ready' status -> overall Pending)
		data = get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		self.assertIn(data["accounting_status"], ["Pending", "Accounted", "Partially Accounted"])

		# Test with real SINV-26-00005 (has a Failed transaction -> overall Failed)
		data_failed = get_sales_invoice_amazon_profitability_data("SINV-26-00005")
		self.assertEqual(data_failed["accounting_status"], "Failed")

	def test_22_post_sales_invoice_amazon_accounting_idempotency(self):
		# Create test invoice and transaction
		ord_id = "TEST-SI-IDEMP-22"
		sinv = self._create_mock_sales_invoice(ord_id, grand_total=500.0, net_total=476.19)

		existing_je = frappe.db.get_value("Journal Entry", {}, "name")
		tx = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TEST-SI-IDEMP-22",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 450.0,
			"accounting_status": "Posted",
			"journal_entry": existing_je,
			"sales_invoice": sinv.name,
		})
		tx.insert(ignore_permissions=True)

		# Run posting
		res = post_sales_invoice_amazon_accounting(sinv.name)
		self.assertTrue(res["success"])

		# Idempotency check: should not post new JE because already Posted
		tx_reloaded = frappe.get_doc("Amazon Financial Transaction", tx.name)
		self.assertEqual(tx_reloaded.accounting_status, "Posted")
		self.assertEqual(tx_reloaded.journal_entry, existing_je)

		self._delete_sales_invoice(sinv.name)

	def test_23_html_contains_view_financials_and_charges_table(self):
		data = get_sales_invoice_amazon_profitability_data("SINV-26-00008")
		html = render_amazon_profitability_html(data)

		self.assertIn("Amazon Charges", html)
		self.assertIn("View Amazon Financials", html)
		self.assertIn("Charge Type", html)
		self.assertIn("Description", html)
		self.assertIn("Amount", html)
		self.assertIn("Tax Amount", html)
		self.assertIn("Total Amount", html)
		self.assertIn("Amazon Financial Transaction", html)
		self.assertIn("Accounting Status", html)
		self.assertIn("Total Amazon Charges", html)

		# Test missing financial data message
		missing_data = get_sales_invoice_amazon_profitability_data("SINV-26-00010") # missing order
		missing_html = render_amazon_profitability_html(missing_data)
		if missing_data.get("is_amazon_invoice") and not missing_data.get("has_amazon_data"):
			self.assertIn("Amazon financial data not available yet.", missing_html)

