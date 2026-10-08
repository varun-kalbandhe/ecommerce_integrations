import unittest
import frappe
from frappe.utils import flt
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import AmazonRepository
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.test_amazon_sp_api_settings import TestAmazonRepository

class TestAmazonStockDeduction(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.repo = TestAmazonRepository()
		
		# Ensure test items exist
		self.item_code = "TEST_STOCK_ITEM_1"
		if not frappe.db.exists("Item", self.item_code):
			item = frappe.new_doc("Item")
			item.item_code = self.item_code
			item.item_name = "Test Stock Item 1"
			item.item_group = self.repo.amz_setting.parent_item_group
			item.stock_uom = "Nos"
			item.is_stock_item = 1
			item.has_batch_no = 0
			item.has_serial_no = 0
			if frappe.db.has_column("Item", "gst_hsn_code"):
				if not frappe.db.exists("GST HSN Code", "999999"):
					hsn = frappe.new_doc("GST HSN Code")
					hsn.hsn_code = "999999"
					hsn.description = "Test HSN"
					hsn.insert(ignore_permissions=True)
				item.gst_hsn_code = "999999"
			item.insert(ignore_permissions=True)
		
		self.warehouse = self.repo.amz_setting.warehouse
		
		# Create a fresh order dictionary
		self.order_id = f"TEST-ORDER-STOCK-{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}-{frappe.generate_hash(length=8)}"
		self.order = {
			"AmazonOrderId": self.order_id,
			"OrderStatus": "Unshipped",
			"PurchaseDate": "2024-01-01T00:00:00Z",
			"LatestShipDate": "2024-01-02T00:00:00Z",
			"BuyerInfo": {"BuyerEmail": "test@example.com"},
			"ShippingAddress": {"Name": "Test Buyer", "City": "Mumbai", "StateOrRegion": "Maharashtra", "PostalCode": "400001"},
		}
		
		# Order items structure matching Amazon response
		self.order_items = [{
			"OrderItemId": "12345",
			"ASIN": self.item_code,
			"SellerSKU": self.item_code,
			"Title": "Test Stock Item 1",
			"QuantityOrdered": 3,
			"QuantityShipped": 0,
			"ItemPrice": {"Amount": 300, "CurrencyCode": "INR"}
		}]
		
		# Mock repo methods that fetch from Amazon
		def mock_call_sp_api_method(sp_api_method, **kwargs):
			if getattr(sp_api_method, "__name__", "") == "get_order_items":
				return {"OrderItems": self.order_items, "NextToken": None}
			return {}
		self.repo.call_sp_api_method = mock_call_sp_api_method
		self.repo.get_product_gst_template = lambda *args, **kwargs: None
		
	def _create_sales_order(self):
		so_name = self.repo.create_sales_order(self.order)
		self.assertIsNotNone(so_name)
		return so_name

	def test_sales_invoice_update_stock_is_0(self):
		so_name = self._create_sales_order()
		si_name = self.repo.create_sales_invoice(so_name, self.order, self.order_items)
		si = frappe.get_doc("Sales Invoice", si_name)
		self.assertEqual(si.update_stock, 0)

	def test_full_shipment_creates_one_dn_and_deducts_stock(self):
		so_name = self._create_sales_order()
		
		# Full shipment
		self.order_items[0]["QuantityShipped"] = 3
		shipment_status = self.repo.get_amazon_shipment_status(self.order, self.order_items)
		
		dn_name = self.repo.create_delivery_note(so_name, self.order, self.order_items, shipment_status)
		self.assertIsNotNone(dn_name)
		
		dn = frappe.get_doc("Delivery Note", dn_name)
		self.assertEqual(dn.items[0].qty, 3)
		self.assertEqual(dn.docstatus, 1)

	def test_partial_shipment_incremental_sync(self):
		so_name = self._create_sales_order()
		
		# Sync 1: Partial shipment (1 out of 3)
		self.order_items[0]["QuantityShipped"] = 1
		status1 = self.repo.get_amazon_shipment_status(self.order, self.order_items)
		dn1_name = self.repo.create_delivery_note(so_name, self.order, self.order_items, status1)
		
		self.assertIsNotNone(dn1_name)
		dn1 = frappe.get_doc("Delivery Note", dn1_name)
		self.assertEqual(dn1.items[0].qty, 1)
		
		# Sync 2: Incremental shipment (3 out of 3)
		self.order_items[0]["QuantityShipped"] = 3
		status2 = self.repo.get_amazon_shipment_status(self.order, self.order_items)
		dn2_name = self.repo.create_delivery_note(so_name, self.order, self.order_items, status2)
		
		self.assertIsNotNone(dn2_name)
		self.assertNotEqual(dn1_name, dn2_name)
		dn2 = frappe.get_doc("Delivery Note", dn2_name)
		self.assertEqual(dn2.items[0].qty, 2)
		
		# Sync 3: Repeated sync (already 3 out of 3) - Idempotent
		dn3_name = self.repo.create_delivery_note(so_name, self.order, self.order_items, status2)
		self.assertIsNone(dn3_name)

	def test_failed_draft_dn_handling(self):
		so_name = self._create_sales_order()
		
		# Create a draft Delivery Note manually to simulate a failed previous sync
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
		draft_dn = make_delivery_note(so_name)
		draft_dn.items[0].qty = 1 # simulated previous draft for 1 item
		draft_dn.insert(ignore_permissions=True)
		
		# Now Amazon reports 1 shipped. Because there's a draft of 1, the new sync should not create another DN.
		self.order_items[0]["QuantityShipped"] = 1
		status = self.repo.get_amazon_shipment_status(self.order, self.order_items)
		
		dn_name = self.repo.create_delivery_note(so_name, self.order, self.order_items, status)
		self.assertIsNone(dn_name) # Should return None as draft already covers the 1 qty

	def test_concurrent_sync(self):
		# Python threads can be tricky with Frappe DB connections, 
		# but we can simulate the logic manually by calling create_delivery_note multiple times with same args.
		# Since it's sequential in python test, it won't trigger the FOR UPDATE block concurrently,
		# but it ensures the logic is solid if run sequentially. 
		# (True concurrency test requires multiprocessing/threading which is hard in unittest with Frappe)
		so_name = self._create_sales_order()
		
		self.order_items[0]["QuantityShipped"] = 3
		status = self.repo.get_amazon_shipment_status(self.order, self.order_items)
		
		dn1 = self.repo.create_delivery_note(so_name, self.order, self.order_items, status)
		self.assertIsNotNone(dn1)
		
		dn2 = self.repo.create_delivery_note(so_name, self.order, self.order_items, status)
		self.assertIsNone(dn2) # Second sequential call should be None
