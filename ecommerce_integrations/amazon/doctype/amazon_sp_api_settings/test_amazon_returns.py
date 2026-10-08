import unittest
import frappe
from frappe.utils import flt
from unittest.mock import patch, MagicMock
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import AmazonRepository
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.test_amazon_sp_api_settings import TestAmazonRepository, TestOrders

class TestAmazonReturns(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql("DELETE FROM `tabSales Order` WHERE po_no LIKE 'TEST-RET-%'")
		frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE po_no LIKE 'TEST-RET-%'")
		frappe.db.sql("DELETE FROM `tabSales Invoice` WHERE po_no LIKE 'TEST-RET-%'")
		cls.repo = TestAmazonRepository()
		cls.amz_setting = cls.repo.amz_setting
		cls.amz_setting.create_item_if_not_exists = 1

	def setUp(self):
		frappe.db.sql("DELETE FROM `tabSales Order Item` WHERE parent IN (SELECT name FROM `tabSales Order` WHERE amazon_order_id LIKE 'TEST-RET-%')")
		frappe.db.sql("DELETE FROM `tabSales Order` WHERE amazon_order_id LIKE 'TEST-RET-%'")
		frappe.db.sql("DELETE FROM `tabDelivery Note Item` WHERE parent IN (SELECT name FROM `tabDelivery Note` WHERE po_no LIKE 'TEST-RET-%')")
		frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE po_no LIKE 'TEST-RET-%'")
		frappe.db.sql("DELETE FROM `tabSales Invoice Item` WHERE parent IN (SELECT name FROM `tabSales Invoice` WHERE po_no LIKE 'TEST-RET-%')")
		frappe.db.sql("DELETE FROM `tabSales Invoice` WHERE po_no LIKE 'TEST-RET-%'")
		frappe.db.commit()

	def get_mock_order(self, order_id, order_status="Shipped"):
		return {
			"AmazonOrderId": order_id,
			"PurchaseDate": "2023-01-01T12:00:00Z",
			"LastUpdateDate": "2023-01-02T12:00:00Z",
			"LatestShipDate": "2023-01-05T12:00:00Z",
			"OrderStatus": order_status,
			"SalesChannel": "Amazon.com",
			"FulfillmentChannel": "MFN",
			"BuyerEmail": "test@example.com",
			"BuyerName": "Test Buyer",
			"ShippingAddress": {
				"Name": "Test Buyer",
				"AddressLine1": "123 Main St",
				"City": "Anytown",
				"StateOrRegion": "Maharashtra",
				"PostalCode": "400001",
				"CountryCode": "IN"
			},
			"OrderTotal": {"CurrencyCode": "INR", "Amount": "11.80"}
		}

	def get_mock_order_items(self):
		return [
			{
				"OrderItemId": "111",
				"SellerSKU": "SKU-RET-1",
				"ASIN": "ASINRET1",
				"Title": "Return Item 1",
				"QuantityOrdered": 3,
				"QuantityShipped": 3,
				"ItemPrice": {"CurrencyCode": "INR", "Amount": "30.00"}
			},
			{
				"OrderItemId": "222",
				"SellerSKU": "SKU-RET-2",
				"ASIN": "ASINRET2",
				"Title": "Return Item 2",
				"QuantityOrdered": 2,
				"QuantityShipped": 2,
				"ItemPrice": {"CurrencyCode": "INR", "Amount": "20.00"}
			}
		]

	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_product_gst_template")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_amazon_hsn")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_charges_and_fees")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.test_amazon_sp_api_settings.TestAmazonRepository.call_sp_api_method")
	def test_full_return(self, mock_call_sp_api, mock_get_charges, mock_get_hsn, mock_get_gst):
		mock_get_gst.return_value = None
		mock_get_charges.return_value = {"charges": [], "fees": []}
		mock_get_hsn.return_value = "85171110"

		def side_effect(sp_api_method, **kwargs):
			method_name = getattr(sp_api_method, "__name__", str(sp_api_method))
			print(f"DEBUG SIDE_EFFECT full return: {method_name}")
			if "get_order_items" in method_name:
				return {"OrderItems": self.get_mock_order_items()}
			if "list_financial_events_by_order_id" in method_name:
				return {
					"FinancialEvents": {
						"ShipmentEventList": [
							{
								"ShipmentItemList": [
									{"SellerSKU": "SKU-RET-1", "QuantityShipped": 3, "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": 30.0}}]},
									{"SellerSKU": "SKU-RET-2", "QuantityShipped": 2, "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": 20.0}}]}
								]
							}
						],
						"RefundEventList": [
							{
								"ShipmentItemAdjustmentList": [
									{"SellerSKU": "SKU-RET-1", "QuantityShipped": 3},
									{"SellerSKU": "SKU-RET-2", "QuantityShipped": 2}
								]
							}
						]
					}
				}
			return {}

		import uuid
		order_id = f"TEST-RET-01-{uuid.uuid4().hex[:6]}"
		TestOrders.order_items_by_id[order_id] = {
			"status": 200,
			"json": {"payload": {"OrderItems": self.get_mock_order_items()}}
		}

		mock_call_sp_api.side_effect = side_effect
		order = self.get_mock_order(order_id)
		order["OrderTotal"] = {"CurrencyCode": "INR", "Amount": "59.0"}

		# Patch get_amazon_shipment_status to simulate full shipment
		with patch.object(self.repo, "get_amazon_shipment_status") as mock_shipment:
			mock_shipment.return_value = {
				"has_shipped_quantity": True,
				"items": [
					{"seller_sku": "SKU-RET-1", "qty_shipped": 3},
					{"seller_sku": "SKU-RET-2", "qty_shipped": 2}
				]
			}
			so_name, si_name, pe_name, dn_name = self.repo.sync_order(order)
			print(f"DEBUG SYNC ORDER: so={so_name}, si={si_name}, pe={pe_name}, dn={dn_name}")
		
		# Validation
		dns = frappe.get_all("Delivery Note", filters={"against_sales_order": so_name, "docstatus": 1}, distinct=1)
		self.assertEqual(len(dns), 2) # 1 original, 1 return
		
		original_dn = next(d for d in dns if not frappe.db.get_value("Delivery Note", d.name, "is_return"))
		return_dn = next(d for d in dns if frappe.db.get_value("Delivery Note", d.name, "is_return"))
		
		dn_doc = frappe.get_doc("Delivery Note", return_dn.name)
		self.assertEqual(dn_doc.is_return, 1)
		self.assertEqual(dn_doc.return_against, original_dn.name)
		self.assertEqual(len(dn_doc.items), 2)
		self.assertEqual(dn_doc.items[0].qty, -3)
		self.assertEqual(dn_doc.items[1].qty, -2)

		sis = frappe.get_all("Sales Invoice", filters={"po_no": order["AmazonOrderId"], "docstatus": 1})
		self.assertEqual(len(sis), 2) # 1 original, 1 return
		original_si = next(s for s in sis if not frappe.db.get_value("Sales Invoice", s.name, "is_return"))
		return_si = next(s for s in sis if frappe.db.get_value("Sales Invoice", s.name, "is_return"))
		
		si_doc = frappe.get_doc("Sales Invoice", return_si.name)
		self.assertEqual(si_doc.is_return, 1)
		self.assertEqual(si_doc.return_against, original_si.name)
		self.assertEqual(si_doc.update_stock, 0)
		self.assertEqual(len(si_doc.items), 2)
		self.assertEqual(si_doc.items[0].qty, -3)
		self.assertEqual(si_doc.items[1].qty, -2)

	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_product_gst_template")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_amazon_hsn")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_charges_and_fees")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.test_amazon_sp_api_settings.TestAmazonRepository.call_sp_api_method")
	def test_partial_return_and_idempotency(self, mock_call_sp_api, mock_get_charges, mock_get_hsn, mock_get_gst):
		mock_get_gst.return_value = None
		mock_get_charges.return_value = {"charges": [], "fees": []}
		mock_get_hsn.return_value = "85171110"

		refund_payload = {
			"FinancialEvents": {
				"ShipmentEventList": [
					{
						"ShipmentItemList": [
							{"SellerSKU": "SKU-RET-1", "QuantityShipped": 3, "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": 30.0}}]},
							{"SellerSKU": "SKU-RET-2", "QuantityShipped": 2, "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": 20.0}}]}
						]
					}
				],
				"RefundEventList": [
					{
						"ShipmentItemAdjustmentList": [
							{"SellerSKU": "SKU-RET-1", "QuantityShipped": 1},
						]
					}
				]
			}
		}

		def side_effect(sp_api_method, **kwargs):
			method_name = getattr(sp_api_method, "__name__", str(sp_api_method))
			print(f"DEBUG SIDE_EFFECT partial return: {method_name}")
			if "get_order_items" in method_name:
				return {"OrderItems": self.get_mock_order_items()}
			if "list_financial_events_by_order_id" in method_name:
				return refund_payload
			return {}

		import uuid
		order_id = f"TEST-RET-02-{uuid.uuid4().hex[:6]}"
		TestOrders.order_items_by_id[order_id] = {
			"status": 200,
			"json": {"payload": {"OrderItems": self.get_mock_order_items()}}
		}
		mock_call_sp_api.side_effect = side_effect
		order = self.get_mock_order(order_id)
		order["OrderTotal"] = {"CurrencyCode": "INR", "Amount": "59.0"}

		with patch.object(self.repo, "get_amazon_shipment_status") as mock_shipment:
			mock_shipment.return_value = {
				"has_shipped_quantity": True,
				"items": [
					{"seller_sku": "SKU-RET-1", "qty_shipped": 3},
					{"seller_sku": "SKU-RET-2", "qty_shipped": 2}
				]
			}
			so_name, si_name, pe_name, dn_name = self.repo.sync_order(order)
		
		dns = frappe.get_all("Delivery Note", filters={"against_sales_order": so_name, "docstatus": 1, "is_return": 1}, distinct=1)
		self.assertEqual(len(dns), 1)
		
		dn_doc = frappe.get_doc("Delivery Note", dns[0].name)
		self.assertEqual(len(dn_doc.items), 1)
		self.assertEqual(dn_doc.items[0].qty, -1)

		# Sync again with same refund - Idempotency
		with patch.object(self.repo, "get_amazon_shipment_status") as mock_shipment:
			mock_shipment.return_value = {
				"has_shipped_quantity": True,
				"items": [
					{"seller_sku": "SKU-RET-1", "qty_shipped": 3},
					{"seller_sku": "SKU-RET-2", "qty_shipped": 2}
				]
			}
			self.repo.sync_order(order)
		
		# Should still be 1 return DN
		dns_after = frappe.get_all("Delivery Note", filters={"against_sales_order": so_name, "docstatus": 1, "is_return": 1}, distinct=1)
		self.assertEqual(len(dns_after), 1)

		# Now simulate additional return
		refund_payload["FinancialEvents"]["RefundEventList"][0]["ShipmentItemAdjustmentList"][0]["QuantityShipped"] = 3
		
		with patch.object(self.repo, "get_amazon_shipment_status") as mock_shipment:
			mock_shipment.return_value = {
				"has_shipped_quantity": True,
				"items": [
					{"seller_sku": "SKU-RET-1", "qty_shipped": 3},
					{"seller_sku": "SKU-RET-2", "qty_shipped": 2}
				]
			}
			self.repo.sync_order(order)

		dns_final = frappe.get_all("Delivery Note", filters={"against_sales_order": so_name, "docstatus": 1, "is_return": 1}, order_by="`tabDelivery Note`.creation asc", distinct=1)
		self.assertEqual(len(dns_final), 2)
		dn_doc_2 = frappe.get_doc("Delivery Note", dns_final[1].name)
		self.assertEqual(len(dn_doc_2.items), 1)
		self.assertEqual(dn_doc_2.items[0].qty, -2) # The remaining 2

	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_product_gst_template")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_amazon_hsn")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.get_charges_and_fees")
	@patch("ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.AmazonRepository.call_sp_api_method")
	def test_preshipment_cancellation(self, mock_call_sp_api, mock_get_charges, mock_get_hsn, mock_get_gst):
		mock_get_gst.return_value = None
		mock_get_charges.return_value = {"charges": [], "fees": []}
		mock_get_hsn.return_value = "85171110"

		def side_effect(sp_api_method, **kwargs):
			method_name = getattr(sp_api_method, "__name__", "")
			if "get_order_items" in method_name:
				return {"payload": {"OrderItems": self.get_mock_order_items()}}
			if "list_financial_events_by_order_id" in method_name:
				return {
					"payload": {
						"FinancialEvents": {
							"RefundEventList": [
								{
									"ShipmentItemAdjustmentList": [
										{"SellerSKU": "SKU-RET-1", "QuantityShipped": 3},
									]
								}
							]
						}
					}
				}
			return {}

		mock_call_sp_api.side_effect = side_effect
		order = self.get_mock_order("TEST-RET-03", order_status="Canceled")

		# First create a drafted SO by syncing an un-shipped order
		order["OrderStatus"] = "Pending"
		
		# Mock create_sales_invoice to prevent SI creation so SO can be cancelled
		with patch.object(self.repo, 'create_sales_invoice', return_value=None):
			so_name, _, _, _ = self.repo.sync_order(order)
		
		# Now cancel it
		order["OrderStatus"] = "Canceled"
		self.repo.sync_order(order)
		
		# Since it's drafted and cancelled, SO should be Cancelled
		docstatus = frappe.db.get_value("Sales Order", so_name, "docstatus")
		self.assertEqual(docstatus, 2)
		
		# No returns should be created
		dns = frappe.get_all("Delivery Note", filters={"against_sales_order": so_name})
		self.assertEqual(len(dns), 0)

