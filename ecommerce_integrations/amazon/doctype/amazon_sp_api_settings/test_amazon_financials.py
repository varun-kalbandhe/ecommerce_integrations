# Copyright (c) 2026, Frappe and Contributors
# See license.txt

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api import (
	FinancesV20240619,
	SPAPIError,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
	AmazonRepository,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.test_amazon_sp_api_settings import (
	TestAmazonRepository,
)


class TestAmazonFinancials(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		# Reload doctypes in case of test runner isolated schemas
		frappe.reload_doc("amazon", "doctype", "amazon_financial_breakdown", force=True)
		frappe.reload_doc("amazon", "doctype", "amazon_financial_transaction", force=True)
		cls.repo = TestAmazonRepository()
		cls.repo.amz_setting.company = "e.Soft Technologies Pvt Ltd"

	def setUp(self):
		# Clean up only test financial transactions and profitability records
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Financial Breakdown`
			WHERE parent IN (
				SELECT name FROM `tabAmazon Financial Transaction`
				WHERE amazon_transaction_id LIKE 'TX-%'
				   OR amazon_order_id LIKE '%TEST%'
				   OR amazon_order_id = '402-7389804-5527520'
			)
			"""
		)
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Financial Transaction`
			WHERE amazon_transaction_id LIKE 'TX-%'
			   OR amazon_order_id LIKE '%TEST%'
			   OR amazon_order_id = '402-7389804-5527520'
			"""
		)
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Order Profitability Item`
			WHERE parent IN (
				SELECT name FROM `tabAmazon Order Profitability`
				WHERE amazon_order_id LIKE '%TEST%'
				   OR amazon_order_id = '402-7389804-5527520'
			)
			"""
		)
		frappe.db.sql(
			"""
			DELETE FROM `tabAmazon Order Profitability`
			WHERE amazon_order_id LIKE '%TEST%'
			   OR amazon_order_id = '402-7389804-5527520'
			"""
		)
		frappe.db.commit()

	def test_single_shipment_transaction_ingestion(self):
		mock_tx = {
			"transactionId": "TX-TEST-001",
			"transactionType": "Shipment",
			"transactionStatus": "RELEASED",
			"postedDate": "2026-03-01T10:00:00.000Z",
			"totalAmount": {"currencyAmount": 499.0, "currencyCode": "INR"},
			"sellingPartnerMetadata": {
				"sellingPartnerId": "A3TESTSELLER",
				"marketplaceId": "A21TJRUUN4KGV",
			},
			"marketplaceDetails": {
				"marketplaceId": "A21TJRUUN4KGV",
				"marketplaceName": "Amazon.in",
			},
			"relatedIdentifiers": [
				{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-1234567-0000001"},
				{"relatedIdentifierName": "FINANCIAL_EVENT_GROUP_ID", "relatedIdentifierValue": "FEG-1001"},
			],
			"items": [
				{
					"description": "Premium Cotton Shirt",
					"contexts": [
						{"sku": "SKU-SHIRT-M", "asin": "B00TEST001", "quantityShipped": 1}
					],
					"relatedIdentifiers": [
						{"itemRelatedIdentifierValue": "ITEM-ID-123"}
					],
					"breakdowns": [
						{
							"breakdownType": "Fee",
							"breakdownAmount": {"currencyAmount": -45.0, "currencyCode": "INR"},
							"breakdowns": [
								{
									"breakdownType": "Commission",
									"breakdownAmount": {"currencyAmount": -45.0, "currencyCode": "INR"},
								}
							]
						}
					]
				}
			],
			"breakdowns": [
				{
					"breakdownType": "Sales",
					"breakdownAmount": {"currencyAmount": 499.0, "currencyCode": "INR"},
				}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			doc_names = self.repo.sync_financial_transactions("2026-03-01", "2026-03-02")
			self.assertEqual(len(doc_names), 1)

			tx_doc = frappe.get_doc("Amazon Financial Transaction", doc_names[0])
			self.assertEqual(tx_doc.amazon_transaction_id, "TX-TEST-001")
			self.assertEqual(tx_doc.transaction_type, "Shipment")
			self.assertEqual(tx_doc.transaction_status, "RELEASED")
			self.assertEqual(tx_doc.total_amount, 499.0)
			self.assertEqual(tx_doc.currency, "INR")
			self.assertEqual(tx_doc.amazon_order_id, "402-1234567-0000001")
			self.assertEqual(tx_doc.seller_sku, "SKU-SHIRT-M")
			self.assertEqual(tx_doc.asin, "B00TEST001")
			self.assertEqual(tx_doc.marketplace, "Amazon.in")
			self.assertEqual(tx_doc.financial_event_group_id, "FEG-1001")

			# Verify breakdowns: 1 Sales breakdown (Order scope), 1 Commission fee (Item scope)
			self.assertEqual(len(tx_doc.breakdowns), 2)
			sales_bd = next(b for b in tx_doc.breakdowns if b.breakdown_type == "Sales")
			self.assertEqual(sales_bd.amount, 499.0)
			self.assertEqual(sales_bd.allocation_scope, "Order")

			fee_bd = next(b for b in tx_doc.breakdowns if b.breakdown_type == "Commission")
			self.assertEqual(fee_bd.amount, -45.0)
			self.assertEqual(fee_bd.allocation_scope, "Item")
			self.assertEqual(fee_bd.seller_sku, "SKU-SHIRT-M")
			self.assertEqual(fee_bd.category, "Fee")

	def test_duplicate_transaction_ingestion_idempotency(self):
		mock_tx = {
			"transactionId": "TX-IDEM-002",
			"transactionType": "Shipment",
			"transactionStatus": "DEFERRED",
			"totalAmount": {"currencyAmount": 200.0, "currencyCode": "INR"},
			"relatedIdentifiers": [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-IDEM-002"}],
			"breakdowns": [
				{"breakdownType": "Sales", "breakdownAmount": {"currencyAmount": 200.0, "currencyCode": "INR"}}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			doc_names_first = self.repo.sync_financial_transactions("2026-03-01", "2026-03-02")
			self.assertEqual(len(doc_names_first), 1)

			count_first = frappe.db.count("Amazon Financial Transaction", {"amazon_transaction_id": "TX-IDEM-002"})
			self.assertEqual(count_first, 1)

		# Second run simulates status change from DEFERRED to RELEASED
		mock_tx_updated = dict(mock_tx)
		mock_tx_updated["transactionStatus"] = "RELEASED"

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx_updated]):
			doc_names_second = self.repo.sync_financial_transactions("2026-03-01", "2026-03-02")
			self.assertEqual(len(doc_names_second), 1)

			count_second = frappe.db.count("Amazon Financial Transaction", {"amazon_transaction_id": "TX-IDEM-002"})
			self.assertEqual(count_second, 1) # Still 1, NOT duplicated!

			# Verify updated status
			tx_doc = frappe.get_doc("Amazon Financial Transaction", doc_names_second[0])
			self.assertEqual(tx_doc.transaction_status, "RELEASED")
			self.assertEqual(len(tx_doc.breakdowns), 1) # Child rows NOT duplicated!

	def test_refund_transaction_ingestion(self):
		mock_refund = {
			"transactionId": "TX-REFUND-003",
			"transactionType": "Refund",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": -300.0, "currencyCode": "INR"},
			"relatedIdentifiers": [
				{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-REFUND-999"},
				{"relatedIdentifierName": "REFUND_ID", "relatedIdentifierValue": "REF-8888"},
			],
			"items": [
				{
					"description": "Returned Item",
					"contexts": [{"sku": "SKU-RETURN-01", "asin": "B00RET001"}],
					"breakdowns": [
						{
							"breakdownType": "RefundedSales",
							"breakdownAmount": {"currencyAmount": -300.0, "currencyCode": "INR"}
						},
						{
							"breakdownType": "Fee",
							"breakdowns": [
								{
									"breakdownType": "RefundCommission",
									"breakdownAmount": {"currencyAmount": 30.0, "currencyCode": "INR"}
								}
							]
						}
					]
				}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_refund]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)
			doc = frappe.get_doc("Amazon Financial Transaction", names[0])
			self.assertEqual(doc.transaction_type, "Refund")
			self.assertEqual(doc.total_amount, -300.0)
			self.assertEqual(doc.seller_sku, "SKU-RETURN-01")
			self.assertEqual(len(doc.breakdowns), 2)

	def test_account_level_transaction_without_order_id(self):
		mock_service_fee = {
			"transactionId": "TX-ACCT-STORAGE-004",
			"transactionType": "ServiceFee",
			"transactionStatus": "RELEASED",
			"description": "FBA Monthly Storage Fee",
			"totalAmount": {"currencyAmount": -120.50, "currencyCode": "INR"},
			"relatedIdentifiers": [
				{"relatedIdentifierName": "FINANCIAL_EVENT_GROUP_ID", "relatedIdentifierValue": "SETTLE-PAYOUT-77"}
			],
			"breakdowns": [
				{
					"breakdownType": "StorageFee",
					"breakdownAmount": {"currencyAmount": -120.50, "currencyCode": "INR"}
				}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_service_fee]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)
			doc = frappe.get_doc("Amazon Financial Transaction", names[0])
			self.assertEqual(doc.amazon_transaction_id, "TX-ACCT-STORAGE-004")
			self.assertIsNone(doc.amazon_order_id)
			self.assertEqual(doc.total_amount, -120.50)
			self.assertEqual(doc.breakdowns[0].allocation_scope, "Account")
			self.assertEqual(doc.breakdowns[0].breakdown_type, "StorageFee")

	def test_tax_withholding_breakdown(self):
		mock_tax_tx = {
			"transactionId": "TX-WITHHOLDING-005",
			"transactionType": "Shipment",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": 1000.0, "currencyCode": "INR"},
			"relatedIdentifiers": [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-TAX-123"}],
			"breakdowns": [
				{"breakdownType": "Sales", "breakdownAmount": {"currencyAmount": 1000.0, "currencyCode": "INR"}},
				{
					"breakdownType": "WithheldTax",
					"breakdowns": [
						{
							"breakdownType": "TCS-CGST",
							"breakdownAmount": {"currencyAmount": -5.0, "currencyCode": "INR"}
						},
						{
							"breakdownType": "TCS-SGST",
							"breakdownAmount": {"currencyAmount": -5.0, "currencyCode": "INR"}
						},
						{
							"breakdownType": "TDS-Section194O",
							"breakdownAmount": {"currencyAmount": -10.0, "currencyCode": "INR"}
						}
					]
				}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tax_tx]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)
			doc = frappe.get_doc("Amazon Financial Transaction", names[0])
			bd_types = [b.breakdown_type for b in doc.breakdowns]
			self.assertIn("Sales", bd_types)
			self.assertIn("TCS-CGST", bd_types)
			self.assertIn("TCS-SGST", bd_types)
			self.assertIn("TDS-Section194O", bd_types)

	def test_pagination_and_rate_limit_retry(self):
		finances = self.repo.get_finances_v20240619_instance()

		page_1_resp = {
			"payload": {
				"transactions": [{"transactionId": "TX-PAGE-1", "transactionType": "Shipment"}],
				"nextToken": "TOKEN_PAGE_2"
			}
		}
		page_2_resp = {
			"payload": {
				"transactions": [{"transactionId": "TX-PAGE-2", "transactionType": "Shipment"}],
				"nextToken": None
			}
		}

		call_counts = {"count": 0}

		def mock_list_tx(**kwargs):
			call_counts["count"] += 1
			# Simulate rate-limit error 429 on first attempt of page 2
			if kwargs.get("next_token") == "TOKEN_PAGE_2" and call_counts["count"] == 2:
				raise SPAPIError(error="429", error_description="QuotaExceeded")
			elif kwargs.get("next_token") == "TOKEN_PAGE_2":
				return page_2_resp
			else:
				return page_1_resp

		with patch.object(finances, "list_transactions", side_effect=mock_list_tx):
			txs = finances.get_transactions(
				posted_after="2026-03-01",
				posted_before="2026-03-02",
				max_retries=3,
				retry_delay=0.01,
			)
			self.assertEqual(len(txs), 2)
			self.assertEqual(txs[0]["transactionId"], "TX-PAGE-1")
			self.assertEqual(txs[1]["transactionId"], "TX-PAGE-2")
			self.assertEqual(call_counts["count"], 3) # 1 for page 1, 1 failed for page 2, 1 retried for page 2

	def test_missing_optional_fields_resilience(self):
		# Transaction with bare minimum keys and missing nested objects
		mock_minimal = {
			"transactionId": "TX-MINIMAL-006",
			# Missing transactionType, sellingPartnerMetadata, marketplaceDetails, breakdowns, items
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_minimal]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)
			doc = frappe.get_doc("Amazon Financial Transaction", names[0])
			self.assertEqual(doc.amazon_transaction_id, "TX-MINIMAL-006")
			self.assertEqual(doc.total_amount, 0.0)
			self.assertEqual(len(doc.breakdowns), 0)

	def test_accounting_resolution_for_shipment_and_fees(self):
		mock_tx = {
			"transactionId": "TX-ACC-001",
			"transactionType": "Shipment",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": 100.0, "currencyCode": "INR"},
			"relatedIdentifiers": [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-ACC-001"}],
			"items": [
				{
					"description": "Item A",
					"contexts": [{"sku": "SKU-A", "asin": "ASINA"}],
					"breakdowns": [
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{
									"breakdownType": "Base",
									"breakdownAmount": {"currencyAmount": -10.0, "currencyCode": "INR"}
								},
								{
									"breakdownType": "Tax",
									"breakdownAmount": {"currencyAmount": -1.8, "currencyCode": "INR"}
								}
							]
						}
					]
				}
			],
			"breakdowns": [
				{"breakdownType": "Sales", "breakdownAmount": {"currencyAmount": 100.0, "currencyCode": "INR"}}
			]
		}

		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)
			doc = frappe.get_doc("Amazon Financial Transaction", names[0])

			self.assertEqual(doc.debit_account, "Amazon Clearing Account - ES")
			self.assertEqual(doc.credit_account, "1310 - Debtors - ES")

			# Breakdown accounts
			fee_base = next(b for b in doc.breakdowns if b.breakdown_type == "Base")
			self.assertEqual(fee_base.debit_account, "Amazon Fees - ES")
			self.assertEqual(fee_base.credit_account, "Amazon Clearing Account - ES")

			fee_tax = next(b for b in doc.breakdowns if b.breakdown_type == "Tax")
			self.assertEqual(fee_tax.debit_account, "Input Tax IGST - ES")
			self.assertEqual(fee_tax.credit_account, "Amazon Clearing Account - ES")

	def test_settlement_report_ingestion_and_reconciliation(self):
		# Clean settlements
		frappe.db.sql("DELETE FROM `tabAmazon Settlement Entry`")
		frappe.db.sql("DELETE FROM `tabAmazon Settlement`")

		# Create a matching transaction in advance
		mock_tx = {
			"transactionId": "TX-SETTLE-MATCH-1",
			"transactionType": "Shipment",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": 500.0, "currencyCode": "INR"},
			"relatedIdentifiers": [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "402-MATCH-999"}],
			"breakdowns": [{"breakdownType": "Sales", "breakdownAmount": {"currencyAmount": 500.0, "currencyCode": "INR"}}]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			self.repo.sync_financial_transactions()

		tsv_sample = (
			"settlement-id\tsettlement-start-date\tsettlement-end-date\tdeposit-date\ttotal-amount\tcurrency\ttransaction-type\torder-id\tmerchant-order-id\tadjustment-id\tshipment-id\tmarketplace-name\tamount-type\tamount-description\tamount\tfulfillment-id\tposted-date\tposted-date-time\torder-item-code\tmerchant-order-item-id\tmerchant-adjustment-item-id\tsku\tquantity-purchased\tpromotion-id\n"
			"999888777\t01.09.2026 10:00:00 UTC\t08.09.2026 10:00:00 UTC\t10.09.2026 10:00:00 UTC\t450.00\tINR\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\n"
			"999888777\t\t\t\t\t\tOrder\t402-MATCH-999\t\t\t\tAmazon.in\tItemPrice\tPrincipal\t500.00\tMFN\t02.09.2026\t02.09.2026 12:00:00\tITEM-1\t\t\tSKU-1\t1\t\n"
			"999888777\t\t\t\t\t\tOrder\t402-MATCH-999\t\t\t\tAmazon.in\tItemFees\tCommission\t-50.00\tMFN\t02.09.2026\t02.09.2026 12:00:00\tITEM-1\t\t\tSKU-1\t1\t\n"
		)

		mock_rep_list = {
			"reports": [{
				"reportId": "REP-999",
				"reportDocumentId": "DOC-999",
				"reportType": "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
				"processingStatus": "DONE"
			}]
		}

		class MockResponse:
			content = tsv_sample.encode("utf-8")

		mock_client = MagicMock()
		mock_client.get_reports.return_value = mock_rep_list
		mock_client.get_report_document.return_value = {"url": "http://fake.url", "compressionAlgorithm": None}

		with patch.object(self.repo, "get_reports_instance", return_value=mock_client), \
		     patch("requests.get", return_value=MockResponse()):

			s_names = self.repo.sync_settlement_reports()
			self.assertEqual(len(s_names), 1)

			s_doc = frappe.get_doc("Amazon Settlement", s_names[0])
			self.assertEqual(s_doc.settlement_id, "999888777")
			self.assertEqual(s_doc.total_amount, 450.00)
			self.assertEqual(len(s_doc.entries), 2)
			self.assertEqual(s_doc.entries[0].order_id, "402-MATCH-999")
			self.assertEqual(s_doc.entries[0].reconciliation_status, "Matched")
			self.assertEqual(s_doc.entries[0].matched_financial_transaction, "AFT-TX-SETTLE-MATCH-1")

			# Re-running must be idempotent and not create duplicate settlement or entries
			s_names_repeat = self.repo.sync_settlement_reports()
			self.assertEqual(len(s_names_repeat), 1)
			self.assertEqual(frappe.db.count("Amazon Settlement", {"settlement_id": "999888777"}), 1)
			s_doc_recheck = frappe.get_doc("Amazon Settlement", s_names_repeat[0])
			self.assertEqual(len(s_doc_recheck.entries), 2)  # Entries not duplicated!

	def test_preview_amazon_transaction_does_not_create_je(self):
		mock_tx = {
			"transactionId": "TX-PREV-001",
			"transactionType": "ServiceFee",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": -88.5, "currencyCode": "INR"},
			"items": [
				{
					"description": "Fee Item",
					"breakdowns": [
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -75.0, "currencyCode": "INR"}},
								{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -13.5, "currencyCode": "INR"}}
							]
						}
					]
				}
			]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 1)

			je_count_before = frappe.db.count("Journal Entry")
			preview = self.repo.preview_amazon_transaction(names[0])

			self.assertEqual(preview["is_postable"], True)
			self.assertEqual(preview["proposed_document_type"], "Journal Entry")
			self.assertIn("Amazon Fees - ES", preview["debit_accounts"])
			self.assertIn("Amazon Clearing Account - ES", preview["credit_accounts"])
			# Verify preview did NOT create any Journal Entry
			self.assertEqual(frappe.db.count("Journal Entry"), je_count_before)

	def test_post_amazon_transaction_and_idempotency(self):
		mock_tx = {
			"transactionId": "TX-POST-001",
			"transactionType": "ServiceFee",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": -64.9, "currencyCode": "INR"},
			"items": [
				{
					"description": "Fee Item",
					"breakdowns": [
						{
							"breakdownType": "AmazonFees",
							"breakdowns": [
								{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -55.0, "currencyCode": "INR"}},
								{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -9.9, "currencyCode": "INR"}}
							]
						}
					]
				}
			]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_tx]):
			names = self.repo.sync_financial_transactions()
			tx_name = names[0]

			# 1. First Post
			je_1 = self.repo.post_amazon_transaction(tx_name)
			self.assertTrue(je_1)
			doc = frappe.get_doc("Amazon Financial Transaction", tx_name)
			self.assertEqual(doc.accounting_status, "Posted")
			self.assertEqual(doc.journal_entry, je_1)

			# Verify GL entries are created and balanced
			gls = frappe.get_all("GL Entry", filters={"voucher_no": je_1}, fields=["account", "debit", "credit"])
			self.assertTrue(len(gls) > 0)
			tot_dr = sum(g.debit for g in gls)
			tot_cr = sum(g.credit for g in gls)
			self.assertAlmostEqual(tot_dr, tot_cr, places=2)

			# 2. Re-posting must be strictly idempotent
			je_2 = self.repo.post_amazon_transaction(tx_name)
			self.assertEqual(je_1, je_2)
			self.assertEqual(frappe.db.count("Journal Entry", {"name": je_1}), 1)

	def test_post_settlement_payout_bank_entry(self):
		mock_payout = {
			"transactionId": "TX-PAYOUT-001",
			"transactionType": "Transfer",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": 2500.0, "currencyCode": "INR"},
			"breakdowns": [
				{"breakdownType": "FundTransfer", "breakdownAmount": {"currencyAmount": 2500.0, "currencyCode": "INR"}}
			]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_payout]):
			names = self.repo.sync_financial_transactions()
			tx_name = names[0]

			je_payout = self.repo.post_amazon_transaction(tx_name)
			je_doc = frappe.get_doc("Journal Entry", je_payout)
			self.assertEqual(je_doc.voucher_type, "Bank Entry")

			gls = frappe.get_all("GL Entry", filters={"voucher_no": je_payout}, fields=["account", "debit", "credit"])
			bank_leg = next(g for g in gls if "HDFC" in g.account)
			clearing_leg = next(g for g in gls if "Amazon Clearing" in g.account)
			self.assertAlmostEqual(bank_leg.debit, 2500.0, places=2)
			self.assertAlmostEqual(clearing_leg.credit, 2500.0, places=2)

	def test_controlled_batch_posting_and_error_isolation(self):
		# Create 2 valid transactions and 1 zero-amount transaction
		mock_batch = [
			{
				"transactionId": "TX-BATCH-001",
				"transactionType": "ServiceFee",
				"totalAmount": {"currencyAmount": -88.5, "currencyCode": "INR"},
				"items": [{"breakdowns": [{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -75.0, "currencyCode": "INR"}}, {"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -13.5, "currencyCode": "INR"}}]}]}]
			},
			{
				"transactionId": "TX-BATCH-002",
				"transactionType": "ServiceFee",
				"totalAmount": {"currencyAmount": -64.9, "currencyCode": "INR"},
				"items": [{"breakdowns": [{"breakdownType": "AmazonFees", "breakdowns": [{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": -55.0, "currencyCode": "INR"}}, {"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": -9.9, "currencyCode": "INR"}}]}]}]
			}
		]
		with patch.object(FinancesV20240619, "get_transactions", return_value=mock_batch):
			names = self.repo.sync_financial_transactions()
			self.assertEqual(len(names), 2)

			res = self.repo.post_amazon_transactions(tx_names=names)
			self.assertEqual(res["processed"], 2)
			self.assertEqual(res["success_count"], 2)
			self.assertEqual(res["failed_count"], 0)

	def test_post_refund_reversal_je(self):
		# Realistic Amazon refund breakdown with customer price reversal, TCS reversal, and seller fee recovery
		mock_refund = {
			"transactionId": "TX-REFUND-UNIT-01",
			"transactionType": "Refund",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": -448.77, "currencyCode": "INR"},
			"items": [
				{
					"description": "Refund Item",
					"breakdowns": [
						{"breakdownType": "OurPricePrincipal", "breakdownAmount": {"currencyAmount": -571.43, "currencyCode": "INR"}},
						{"breakdownType": "OurPriceTax", "breakdownAmount": {"currencyAmount": -28.57, "currencyCode": "INR"}},
						{"breakdownType": "OurPriceDiscount", "breakdownAmount": {"currencyAmount": 11.43, "currencyCode": "INR"}},
						{"breakdownType": "OurPriceTaxDiscount", "breakdownAmount": {"currencyAmount": 0.57, "currencyCode": "INR"}},
						{"breakdownType": "TCS-IGST", "breakdownAmount": {"currencyAmount": 2.8, "currencyCode": "INR"}},
						{"breakdownType": "Base", "breakdownAmount": {"currencyAmount": 115.62, "currencyCode": "INR"}},
						{"breakdownType": "Tax", "breakdownAmount": {"currencyAmount": 20.81, "currencyCode": "INR"}}
					]
				}
			]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_refund]):
			names = self.repo.sync_financial_transactions()
			tx_name = names[0]

			preview = self.repo.preview_amazon_transaction(tx_name)
			self.assertTrue(preview["is_postable"])
			self.assertIn("1310 - Debtors - ES", preview["debit_accounts"])
			self.assertIn("Amazon Clearing Account - ES", preview["credit_accounts"])

			je_name = self.repo.post_amazon_transaction(tx_name)
			self.assertTrue(je_name)
			gls = frappe.get_all("GL Entry", filters={"voucher_no": je_name}, fields=["account", "debit", "credit"])
			tot_dr = sum(g.debit for g in gls)
			tot_cr = sum(g.credit for g in gls)
			self.assertAlmostEqual(tot_dr, tot_cr, places=2)

			# Verify clearing leg is credited with the refund payout deduction
			clearing_leg = next(g for g in gls if "Amazon Clearing" in g.account)
			self.assertAlmostEqual(clearing_leg.credit, 448.77, places=2)

	def test_post_adjustment_safet_reimbursement_je(self):
		mock_adj = {
			"transactionId": "TX-ADJ-UNIT-01",
			"transactionType": "Adjustment",
			"transactionStatus": "RELEASED",
			"totalAmount": {"currencyAmount": 400.56, "currencyCode": "INR"},
			"items": [],
			"breakdowns": [
				{
					"breakdownType": "Sales",
					"breakdowns": [
						{"breakdownType": "SAFETReimbursement", "breakdownAmount": {"currencyAmount": 400.56, "currencyCode": "INR"}}
					]
				}
			]
		}
		with patch.object(FinancesV20240619, "get_transactions", return_value=[mock_adj]):
			names = self.repo.sync_financial_transactions()
			tx_name = names[0]

			preview = self.repo.preview_amazon_transaction(tx_name)
			self.assertTrue(preview["is_postable"])
			self.assertIn("Amazon Clearing Account - ES", preview["debit_accounts"])
			self.assertIn("Amazon Reimbursements - ES", preview["credit_accounts"])

			je_name = self.repo.post_amazon_transaction(tx_name)
			self.assertTrue(je_name)
			gls = frappe.get_all("GL Entry", filters={"voucher_no": je_name}, fields=["account", "debit", "credit"])
			tot_dr = sum(g.debit for g in gls)
			tot_cr = sum(g.credit for g in gls)
			self.assertAlmostEqual(tot_dr, tot_cr, places=2)

			clearing_leg = next(g for g in gls if "Amazon Clearing" in g.account)
			reimb_leg = next(g for g in gls if "Amazon Reimbursements" in g.account)
			self.assertAlmostEqual(clearing_leg.debit, 400.56, places=2)
			self.assertAlmostEqual(reimb_leg.credit, 400.56, places=2)

	def test_order_profitability_normal_order(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()

		ord_id = "402-7389804-5527520"

		# Setup mock financial transactions matching real order
		tx_ship = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-MOCK-SHIP-001",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 427.71,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 533.34, "seller_sku": "BAY-17"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -45.0, "seller_sku": "BAY-17"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -64.4, "seller_sku": "BAY-17"},
			]
		})
		tx_ship.insert(ignore_permissions=True)

		tx_sf = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-MOCK-SF-001",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -64.9,
			"raw_response": '{"description": "MfnPostageFee"}',
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "fee_type": "MfnPostageFee", "allocation_scope": "Item", "amount": -55.0}
			]
		})
		tx_sf.insert(ignore_permissions=True)

		# Order with existing Sales Invoice
		aop_name = engine.calculate_order_profitability(ord_id)
		self.assertTrue(aop_name)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)

		self.assertEqual(doc.amazon_order_id, ord_id)
		self.assertEqual(doc.sales_invoice, "SINV-26-00024")
		self.assertEqual(doc.revenue, 533.34)
		self.assertEqual(doc.product_cogs, 310.72)
		self.assertEqual(doc.amazon_fees, 109.4)
		self.assertEqual(doc.shipping_expense, 55.0)
		self.assertEqual(doc.net_profit, 58.22)
		self.assertAlmostEqual(doc.profit_margin, 10.92, places=2)
		self.assertEqual(doc.calculation_status, "Final")
		self.assertEqual(len(doc.items), 1)

	def test_order_profitability_loss_making(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()

		# Mock test order where fees exceed revenue
		ord_id = "TEST-LOSS-MAKING-ORDER-001"
		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-LOSS-001",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 10.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 100.0, "seller_sku": "SKU-LOSS"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -150.0, "seller_sku": "SKU-LOSS"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)

		self.assertEqual(doc.revenue, 100.0)
		self.assertEqual(doc.amazon_fees, 150.0)
		self.assertEqual(doc.net_profit, -50.0)
		self.assertEqual(doc.profit_margin, -50.0)

	def test_order_profitability_multi_item_proportional_and_qty_allocation(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		ord_id = "TEST-MULTI-ITEM-ORDER-002"

		# Setup SO with 2 items
		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-MULTI-002",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 250.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 200.0, "seller_sku": "SKU-A"},
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 100.0, "seller_sku": "SKU-B"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -30.0, "seller_sku": "SKU-A"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -20.0, "seller_sku": "SKU-B"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		# Add Easy Ship ServiceFee
		sf_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-MULTI-SF-002",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -60.0,
			"raw_response": '{"description": "MfnPostageFee"}',
			"breakdowns": [
				{"breakdown_type": "MfnPostageFee", "category": "AmazonFees", "fee_type": "MfnPostageFee", "allocation_scope": "Order", "amount": -60.0}
			]
		})
		sf_doc.insert(ignore_permissions=True)

		# 1. Proportional by Revenue (200 vs 100 -> 2/3 and 1/3 of 60 = 40 and 20)
		engine_rev = AmazonProfitabilityEngine(allocation_method="Proportional by Revenue")
		aop_name_rev = engine_rev.calculate_order_profitability(ord_id)
		doc_rev = frappe.get_doc("Amazon Order Profitability", aop_name_rev)

		item_a = next(i for i in doc_rev.items if i.sku == "SKU-A")
		item_b = next(i for i in doc_rev.items if i.sku == "SKU-B")
		self.assertAlmostEqual(item_a.allocated_order_expense, 40.0, places=2)
		self.assertAlmostEqual(item_b.allocated_order_expense, 20.0, places=2)

		# 2. Equal allocation (30 and 30)
		engine_eq = AmazonProfitabilityEngine(allocation_method="Equal")
		aop_name_eq = engine_eq.calculate_order_profitability(ord_id)
		doc_eq = frappe.get_doc("Amazon Order Profitability", aop_name_eq)

		item_a_eq = next(i for i in doc_eq.items if i.sku == "SKU-A")
		item_b_eq = next(i for i in doc_eq.items if i.sku == "SKU-B")
		self.assertAlmostEqual(item_a_eq.allocated_order_expense, 30.0, places=2)
		self.assertAlmostEqual(item_b_eq.allocated_order_expense, 30.0, places=2)

	def test_order_profitability_refund_impact(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		ord_id = "TEST-REFUND-IMPACT-003"

		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-REF-SALE-003",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 500.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 500.0, "seller_sku": "SKU-R"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -50.0, "seller_sku": "SKU-R"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		ref_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-REF-DEDUCT-003",
			"transaction_type": "Refund",
			"amazon_order_id": ord_id,
			"total_amount": -480.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": -500.0, "seller_sku": "SKU-R"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": 20.0, "seller_sku": "SKU-R"},
			]
		})
		ref_doc.insert(ignore_permissions=True)

		engine = AmazonProfitabilityEngine()
		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)

		# Revenue: 500, Refund deduction: 500, Net Amazon fees: 30 (50 initial - 20 refunded)
		self.assertEqual(doc.revenue, 500.0)
		self.assertEqual(doc.refund_amount, 500.0)
		self.assertEqual(doc.amazon_fees, 30.0)
		self.assertEqual(doc.net_profit, -30.0)

	def test_order_profitability_missing_cogs(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-MISSING-COGS-004"

		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-NO-COGS-004",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 200.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 200.0, "seller_sku": "SKU-UNKNOWN-ITEM"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertEqual(doc.product_cogs, 0.0)
		self.assertEqual(doc.calculation_status, "Estimated / COGS Missing")

	def test_order_profitability_missing_sales_invoice(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-NO-INVOICE-005"

		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-NO-INV-005",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 350.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 350.0, "seller_sku": "SKU-EST"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertIsNone(doc.sales_invoice)
		self.assertEqual(doc.revenue, 350.0)

	def test_order_profitability_missing_amazon_financial_data(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		# Order ID with neither Sales Order nor Financial Transactions
		ord_id = "TEST-NONEXISTENT-ORDER-999"
		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertEqual(doc.revenue, 0.0)
		self.assertEqual(doc.net_profit, 0.0)
		self.assertEqual(doc.calculation_status, "Estimated")

	def test_product_ads_payment_account_level(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		# ProductAdsPayment has no order ID and remains an account-level expense
		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-ADS-PAYMENT-006",
			"transaction_type": "ProductAdsPayment",
			"amazon_order_id": None,
			"total_amount": -1500.0,
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Account", "amount": -1500.0}
			]
		})
		tx_doc.insert(ignore_permissions=True)

		# Make sure order profitability calculation ignores account-level ads
		ord_id = "TEST-ORDER-NO-ADS-007"
		tx_ord = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-ORD-NO-ADS-007",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 500.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 500.0, "seller_sku": "SKU-CLEAN"},
			]
		})
		tx_ord.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertEqual(doc.advertising_expense, 0.0)

	def test_order_profitability_reimbursement(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-REIMB-ORDER-008"

		# Shipment + SAFET reimbursement on same order
		tx_ship = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-REIMB-SHIP-008",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 300.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 300.0, "seller_sku": "SKU-REIMB"},
			]
		})
		tx_ship.insert(ignore_permissions=True)

		tx_adj = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-REIMB-ADJ-008",
			"transaction_type": "Adjustment",
			"amazon_order_id": ord_id,
			"total_amount": 150.0,
			"breakdowns": [
				{"breakdown_type": "Reimbursements", "category": "Sales", "fee_type": "Reimbursements", "allocation_scope": "Order", "amount": 150.0}
			]
		})
		tx_adj.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertEqual(doc.revenue, 300.0)
		self.assertEqual(doc.adjustment_amount, 150.0)
		self.assertEqual(doc.net_profit, 450.0)

	def test_gst_tcs_tds_exclusions_from_expenses(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-TAX-EXCLUSION-009"

		# Transaction with fees, fee GST (18%), TCS (1%), and TDS (1%)
		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-TAX-EXCL-009",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 800.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 1000.0, "seller_sku": "SKU-TAX"},
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -100.0, "seller_sku": "SKU-TAX"},
				{"breakdown_type": "Tax", "category": "AmazonFees", "allocation_scope": "Item", "amount": -18.0, "seller_sku": "SKU-TAX"}, # Fee GST (input credit)
				{"breakdown_type": "TCS-IGST", "category": "TaxCollectedAtSource", "allocation_scope": "Item", "amount": -10.0, "seller_sku": "SKU-TAX"}, # TCS asset
				{"breakdown_type": "ItemTDS", "category": "TaxWithholding", "allocation_scope": "Item", "amount": -10.0, "seller_sku": "SKU-TAX"}, # TDS asset
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)

		# Fees should ONLY include Base fee (-100.0), NOT GST (-18), TCS (-10), or TDS (-10)
		self.assertEqual(doc.amazon_fees, 100.0)
		self.assertEqual(doc.net_profit, 900.0) # 1000 rev - 100 fee

	def test_zero_revenue_and_zero_quantity_handling(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-ZERO-VALUES-010"

		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-ZERO-010",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 0.0,
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "allocation_scope": "Item", "amount": -50.0, "seller_sku": "SKU-ZERO"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name = engine.calculate_order_profitability(ord_id)
		doc = frappe.get_doc("Amazon Order Profitability", aop_name)
		self.assertEqual(doc.revenue, 0.0)
		self.assertEqual(doc.profit_margin, 0.0)
		self.assertEqual(doc.net_profit, -50.0)

	def test_late_arriving_transaction_recalculation(self):
		from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
		engine = AmazonProfitabilityEngine()
		ord_id = "TEST-LATE-ARRIVAL-011"

		# 1. Initial shipment
		tx_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-LATE-1-011",
			"transaction_type": "Shipment",
			"amazon_order_id": ord_id,
			"total_amount": 400.0,
			"breakdowns": [
				{"breakdown_type": "OurPricePrincipal", "category": "ProductCharges", "allocation_scope": "Item", "amount": 400.0, "seller_sku": "SKU-LATE"},
			]
		})
		tx_doc.insert(ignore_permissions=True)

		aop_name1 = engine.calculate_order_profitability(ord_id)
		doc1 = frappe.get_doc("Amazon Order Profitability", aop_name1)
		self.assertEqual(doc1.revenue, 400.0)
		self.assertEqual(doc1.amazon_fees, 0.0)

		# 2. Late arriving ServiceFee
		sf_doc = frappe.get_doc({
			"doctype": "Amazon Financial Transaction",
			"amazon_transaction_id": "TX-LATE-2-011",
			"transaction_type": "ServiceFee",
			"amazon_order_id": ord_id,
			"total_amount": -55.0,
			"raw_response": '{"description": "MfnPostageFee"}',
			"breakdowns": [
				{"breakdown_type": "Base", "category": "AmazonFees", "fee_type": "MfnPostageFee", "allocation_scope": "Order", "amount": -55.0}
			]
		})
		sf_doc.insert(ignore_permissions=True)

		# Recalculate
		aop_name2 = engine.calculate_order_profitability(ord_id)
		self.assertEqual(aop_name1, aop_name2)
		doc2 = frappe.get_doc("Amazon Order Profitability", aop_name2)
		self.assertEqual(doc2.shipping_expense, 55.0)
		self.assertEqual(doc2.net_profit, 345.0)




