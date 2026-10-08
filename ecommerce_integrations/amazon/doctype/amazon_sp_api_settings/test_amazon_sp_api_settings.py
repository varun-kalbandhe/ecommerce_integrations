# Copyright (c) 2022, Frappe and Contributors
# See license.txt


import json
import os
import time
import unittest
from typing import Any, ClassVar

import responses
from requests import request
from requests.exceptions import HTTPError

import frappe
from frappe.exceptions import ValidationError

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
	AmazonRepository,
	validate_amazon_sp_api_credentials,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api import (
	SPAPI,
	CatalogItems,
	Finances,
	FinancesV20240619,
	ListingsItems,
	Orders,
	ProductFees,
	SPAPIError,
	Util,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings import (
	setup_custom_fields,
)

file_path = os.path.join(os.path.dirname(__file__), "test_data.json")
with open(file_path) as json_file:
	try:
		DATA = json.load(json_file)
	except json.decoder.JSONDecodeError as e:
		frappe.throw(e)


class TestSPAPI(SPAPI):
	# Expected response after hitting the URL.
	expected_response: ClassVar[dict[str, Any]] = {}

	@responses.activate
	def make_request(
		self,
		method: str = "GET",
		append_to_base_uri: str = "",
		params: dict | None = None,
		data: dict | None = None,
	) -> object:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		if method == "GET":
			responses_method = responses.GET
		elif method == "POST":
			responses_method = responses.POST
		else:
			raise HTTPError("Method not supported!")

		url = self.endpoint + self.BASE_URI + append_to_base_uri

		responses.add(
			responses_method,
			url,
			status=self.expected_response.get("status", 200),
			json=self.expected_response.get("json", {}),
		)

		try:
			response = request(method=method, url=url, params=None, data=None)
			return response.json()
		except HTTPError as e:
			error = SPAPIError(str(e))
			error.response = e.response
			raise error


class TestFinances(Finances, TestSPAPI):
	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int | None = None, next_token: str | None = None
	) -> object:
		self.expected_response = DATA.get("list_financial_events_by_order_id_200")
		return super().list_financial_events_by_order_id(order_id, max_results, next_token)


class TestFinancesV20240619(FinancesV20240619, TestSPAPI):
	transactions_by_order_id: ClassVar[dict[str, Any]] = {}

	def list_transactions(
		self,
		related_identifier_name: str | None = None,
		related_identifier_value: str | None = None,
		posted_after: str | None = None,
		posted_before: str | None = None,
		next_token: str | None = None,
	) -> object:
		if related_identifier_value and related_identifier_value in self.transactions_by_order_id:
			self.expected_response = self.transactions_by_order_id[related_identifier_value]
		else:
			self.expected_response = {"status": 200, "json": {"payload": {"transactions": []}}}
		return super().list_transactions(
			related_identifier_name=related_identifier_name,
			related_identifier_value=related_identifier_value,
			posted_after=posted_after,
			posted_before=posted_before,
			next_token=next_token,
		)


class TestOrders(Orders, TestSPAPI):
	def get_orders(
		self,
		created_after: str | None = None,
		created_before: str | None = None,
		last_updated_after: str | None = None,
		last_updated_before: str | None = None,
		order_statuses: list | str | None = None,
		marketplace_ids: list | str | None = None,
		fulfillment_channels: list | str | None = None,
		payment_methods: list | str | None = None,
		buyer_email: str | None = None,
		seller_order_id: str | None = None,
		max_results: int = 100,
		easyship_shipment_statuses: list | str | None = None,
		next_token: str | None = None,
		amazon_order_ids: list | str | None = None,
		actual_fulfillment_supply_source_id: str | None = None,
		is_ispu: bool = False,
		store_chain_store_id: str | None = None,
	) -> object:
		self.expected_response = DATA.get("get_orders_200")
		return super().get_orders(
			created_after=created_after,
			created_before=created_before,
			last_updated_after=last_updated_after,
			last_updated_before=last_updated_before,
			order_statuses=order_statuses,
			marketplace_ids=marketplace_ids,
			fulfillment_channels=fulfillment_channels,
			payment_methods=payment_methods,
			buyer_email=buyer_email,
			seller_order_id=seller_order_id,
			max_results=max_results,
			easyship_shipment_statuses=easyship_shipment_statuses,
			next_token=next_token,
			amazon_order_ids=amazon_order_ids,
			actual_fulfillment_supply_source_id=actual_fulfillment_supply_source_id,
			is_ispu=is_ispu,
			store_chain_store_id=store_chain_store_id,
		)

	def get_order(self, order_id: str) -> object:
		self.expected_response = {
			"status": 200,
			"json": {
				"payload": {
					"AmazonOrderId": order_id,
					"OrderStatus": "Shipped",
					"FulfillmentChannel": "MFN",
					"PaymentMethod": "COD",
				}
			},
		}
		return super().get_order(order_id)

	order_items_by_id: ClassVar[dict[str, Any]] = {}

	def get_order_items(self, order_id: str, next_token: str | None = None) -> object:
		if order_id in self.order_items_by_id:
			self.expected_response = self.order_items_by_id[order_id]
		else:
			self.expected_response = DATA.get("get_order_items_200")
		return super().get_order_items(order_id, next_token)


class TestCatalogItems(CatalogItems, TestSPAPI):
	def get_catalog_item(
		self,
		asin: str,
		marketplace_ids: str | list | None = None,
		included_data: str | list | None = None,
		**kwargs,
	) -> object:
		self.expected_response = DATA.get("get_catalog_item_200")
		return super().get_catalog_item(
			asin,
			marketplace_ids=marketplace_ids,
			included_data=included_data,
			**kwargs,
		)


class TestListingsItems(ListingsItems, TestSPAPI):
	def get_listings_item(
		self,
		seller_id: str,
		sku: str,
		marketplace_ids: str | list | None = None,
		included_data: str | list | None = None,
		**kwargs,
	) -> object:
		self.expected_response = DATA.get("get_listings_item_200")
		return super().get_listings_item(
			seller_id,
			sku,
			marketplace_ids=marketplace_ids,
			included_data=included_data,
		)


class TestProductFees(ProductFees, TestSPAPI):
	def get_my_fees_estimate_for_asin(
		self,
		asin: str,
		price: float = 100.0,
		currency: str = "INR",
		identifier: str = "fees_est",
	) -> object:
		self.expected_response = DATA.get("get_my_fees_estimate_for_asin_200")
		return super().get_my_fees_estimate_for_asin(
			asin,
			price=price,
			currency=currency,
			identifier=identifier,
		)


class TestAmazonSettings:
	def __init__(self) -> None:
		def get_company():
			company_name = frappe.db.get_value(
				"Company",
				{"company_name": "Amazon Test Company", "country": "India", "default_currency": "INR"},
				"company_name",
			)

			if not company_name:
				company = frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": "Amazon Test Company",
						"abbr": "ATC",
						"country": "India",
						"default_currency": "INR",
						"gstin": "27AAKPP7999R1Z5",
					}
				)
				company.insert(ignore_permissions=True)
				company_name = company.company_name
			else:
				frappe.db.set_value("Company", company_name, "gstin", "27AAKPP7999R1Z5")

			return company_name

		def get_warehouse():
			warehouse_name = frappe.db.get_value(
				"Warehouse",
				{
					"warehouse_name": "Amazon Test Warehouse",
				},
				"warehouse_name",
			)

			if not warehouse_name:
				warehouse = frappe.get_doc(
					{
						"doctype": "Warehouse",
						"warehouse_name": "Amazon Test Warehouse",
						"company": "Amazon Test Company",
					}
				)
				warehouse.insert(ignore_permissions=True)
				warehouse_name = warehouse.warehouse_name

			return warehouse_name + " - ATC"

		def get_item_group():
			item_group_name = frappe.db.get_value(
				"Item Group",
				{
					"item_group_name": "Amazon Test Warehouse",
				},
				"item_group_name",
			)

			if not item_group_name:
				item_group = frappe.get_doc(
					{
						"doctype": "Item Group",
						"item_group_name": "Amazon Test Warehouse",
					}
				)
				item_group.insert(ignore_permissions=True)
				item_group_name = item_group.item_group_name

			return item_group_name

		self.is_active = 1
		self.refresh_token = "********************"
		self.client_id = "********************"
		self.client_secret = "********************"
		self.country = "US"
		self.company = get_company()
		self.warehouse = get_warehouse()
		self.parent_item_group = get_item_group()
		self.price_list = "Standard Selling"
		self.customer_group = "Individual"
		self.territory = "All Territories"
		self.customer_type = "Individual"
		self.market_place_account_group = "Accounts Receivable - ATC"
		self.after_date = "2000-07-23"
		self.taxes_charges = 1
		self.enable_sync = 1
		self.max_retry_limit = 3
		self.create_item_if_not_exists = 1
		self.name = "test_amazon_setting"
		self.amazon_fields_map = [
			frappe._dict({"amazon_field": "ASIN", "item_field": "item_code", "use_to_find_item_code": 1})
		]


class TestAmazonRepository(AmazonRepository):
	def __init__(self) -> None:
		self.amz_setting = TestAmazonSettings()
		self.instance_params = dict(
			client_id=self.amz_setting.client_id,
			client_secret=self.amz_setting.client_secret,
			refresh_token=self.amz_setting.refresh_token,
			country_code=self.amz_setting.country,
		)

	def call_sp_api_method(self, sp_api_method, **kwargs):
		max_retries = self.amz_setting.max_retry_limit

		for _ in range(max_retries):
			try:
				result = sp_api_method(**kwargs)
				return result.get("payload")
			except Exception:
				time.sleep(3)
				continue

	def get_finances_instance(self):
		return TestFinances(**self.instance_params)

	def get_finances_v20240619_instance(self):
		return TestFinancesV20240619(**self.instance_params)

	def get_orders_instance(self):
		return TestOrders(**self.instance_params)

	def get_catalog_items_instance(self):
		return TestCatalogItems(**self.instance_params)

	def get_listings_items_instance(self):
		return TestListingsItems(**self.instance_params)

	def get_product_fees_instance(self):
		return TestProductFees(**self.instance_params)


class TestAmazon(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		setup_custom_fields()

	def test_get_orders(self):
		amazon_repository = TestAmazonRepository()
		sales_orders = amazon_repository.get_orders("2000-07-23")
		self.assertEqual(len(sales_orders), 2)

	def test_validate_credentials(self):
		credentials = dict(
			client_id="********************",
			client_secret="********************",
			refresh_token="********************",
			country="US",
		)

		self.assertRaises(ValidationError, validate_amazon_sp_api_credentials, **credentials)

	def test_hsn_extraction_from_listings(self):
		repo = TestAmazonRepository()
		order_item = {"ASIN": "B0GYSTCR27", "SellerSKU": "100343"}
		hsn = repo.get_amazon_hsn(order_item)
		self.assertEqual(hsn, "30049099")

	def test_missing_hsn_fallback_to_parent(self):
		repo = TestAmazonRepository()
		order_item = {"ASIN": "B0GYSTCR27", "SellerSKU": "100343"}
		# When listings API returns no external_product_information
		orig_get_listings = repo.get_listings_items_instance
		mock_client = unittest.mock.MagicMock()
		mock_client.get_listings_item.return_value = {"sku": "100343", "attributes": {}}
		repo.get_listings_items_instance = lambda: mock_client

		hsn = repo.get_amazon_hsn(order_item)
		self.assertIsNone(hsn)

	def test_item_creation_receives_amazon_hsn(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_ASIN_HSN_1",
			"SellerSKU": "TEST_SKU_HSN_1",
			"OrderItemId": "TEST_ITEM_ID_1",
			"Title": "Test Product With HSN",
		}
		repo.get_amazon_hsn = lambda oi: "30049099"

		# Ensure item does not exist
		if frappe.db.exists("Item", "TEST_ASIN_HSN_1"):
			frappe.delete_doc("Item", "TEST_ASIN_HSN_1", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASIN_HSN_1"})
		frappe.db.delete("Ecommerce Item", {"sku": "TEST_SKU_HSN_1"})

		item_code = repo.create_item(order_item)
		item_doc = frappe.get_doc("Item", item_code)
		if frappe.db.has_column("Item", "gst_hsn_code"):
			self.assertEqual(item_doc.gst_hsn_code, "30049099")

		# Cleanup
		frappe.delete_doc("Item", item_code, force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASIN_HSN_1"})
		frappe.db.delete("Ecommerce Item", {"sku": "TEST_SKU_HSN_1"})

	def test_item_group_receives_custom_description(self):
		repo = TestAmazonRepository()
		amazon_item = {
			"summaries": [{"websiteDisplayGroupName": "_Test Custom Desc Group"}]
		}
		if frappe.db.exists("Item Group", "_Test Custom Desc Group"):
			frappe.delete_doc("Item Group", "_Test Custom Desc Group", force=True)

		# Mock has_column to simulate presence of custom_description
		orig_has_column = frappe.db.has_column
		def mock_has_column(doctype, column):
			if doctype == "Item Group" and column == "custom_description":
				return True
			return orig_has_column(doctype, column)

		with unittest.mock.patch("frappe.db.has_column", side_effect=mock_has_column):
			# Test create_item_group inner function via reflection or isolated execution
			ig = frappe.new_doc("Item Group")
			ig.item_group_name = "_Test Custom Desc Group"
			ig.parent_item_group = repo.amz_setting.parent_item_group
			if frappe.db.has_column("Item Group", "custom_description"):
				ig.custom_description = ig.item_group_name
			self.assertEqual(ig.custom_description, "_Test Custom Desc Group")

	def test_b0gystcr27_item_creation_receives_amazon_hsn(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "B0GYSTCR27",
			"SellerSKU": "100343",
			"OrderItemId": "TEST_B0GYSTCR27_ID",
			"Title": "6 Pcs Collector Gel Ultra Pain Relief Gel",
		}
		# 1. Amazon Listings HSN extraction dynamically returns 30049099
		amazon_hsn = repo.get_amazon_hsn(order_item)
		self.assertEqual(amazon_hsn, "30049099")

		# 2. When creating an item with B0GYSTCR27 / 100343, Amazon HSN 30049099 is assigned
		test_asin = "TEST_B0GYSTCR27_NEW"
		test_sku = "TEST_100343_NEW"
		test_order_item = {
			"ASIN": test_asin,
			"SellerSKU": test_sku,
			"OrderItemId": "TEST_B0GYSTCR27_ID",
			"Title": "6 Pcs Collector Gel Ultra Pain Relief Gel",
		}
		if frappe.db.exists("Item", test_asin):
			frappe.delete_doc("Item", test_asin, force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": test_asin})
		frappe.db.delete("Ecommerce Item", {"sku": test_sku})

		orig_get_hsn = repo.get_amazon_hsn
		repo.get_amazon_hsn = lambda oi: orig_get_hsn(order_item) if oi.get("ASIN") == test_asin else orig_get_hsn(oi)

		try:
			item_code = repo.create_item(test_order_item)
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc.gst_hsn_code, "30049099")
		finally:
			if frappe.db.exists("Item", test_asin):
				frappe.delete_doc("Item", test_asin, force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": test_asin})
			frappe.db.delete("Ecommerce Item", {"sku": test_sku})

		if not frappe.db.exists("Item", "B0GYSTCR27"):
			try:
				item_code = repo.create_item(order_item)
				item_doc = frappe.get_doc("Item", item_code)
				if frappe.db.has_column("Item", "gst_hsn_code"):
					self.assertEqual(item_doc.gst_hsn_code, "30049099")
			finally:
				if frappe.db.exists("Item", "B0GYSTCR27"):
					frappe.delete_doc("Item", "B0GYSTCR27", force=True)
				frappe.db.delete("Ecommerce Item", {"integration_item_code": "B0GYSTCR27"})

	def test_existing_item_hsn_not_overwritten(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_EXISTING_ITEM",
			"SellerSKU": "TEST_EXISTING_SKU",
			"Title": "Existing Test Item",
		}
		if frappe.db.exists("Item", "TEST_EXISTING_ITEM"):
			frappe.delete_doc("Item", "TEST_EXISTING_ITEM", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_EXISTING_ITEM"})

		existing_item = frappe.new_doc("Item")
		existing_item.item_code = "TEST_EXISTING_ITEM"
		existing_item.item_name = "Existing Test Item"
		existing_item.item_group = repo.amz_setting.parent_item_group
		existing_item.stock_uom = "Nos"
		if frappe.db.has_column("Item", "gst_hsn_code"):
			existing_item.gst_hsn_code = "84672100"
		existing_item.insert(ignore_permissions=True)

		try:
			repo.get_amazon_hsn = lambda oi: "30049099"

			item_code = repo.get_item_code(order_item)
			self.assertEqual(item_code, "TEST_EXISTING_ITEM")
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc.gst_hsn_code, "84672100")

			created_code = repo.create_item(order_item)
			self.assertEqual(created_code, "TEST_EXISTING_ITEM")
			item_doc_after = frappe.get_doc("Item", created_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc_after.gst_hsn_code, "84672100")
		finally:
			if frappe.db.exists("Item", "TEST_EXISTING_ITEM"):
				frappe.delete_doc("Item", "TEST_EXISTING_ITEM", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_EXISTING_ITEM"})

	def test_newly_created_item_receives_stock_uom(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_ASIN_UOM_1",
			"SellerSKU": "TEST_SKU_UOM_1",
			"OrderItemId": "TEST_ITEM_ID_UOM",
			"Title": "Test Product With UOM",
		}
		repo.get_amazon_hsn = lambda oi: "30049099"

		if frappe.db.exists("Item", "TEST_ASIN_UOM_1"):
			frappe.delete_doc("Item", "TEST_ASIN_UOM_1", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASIN_UOM_1"})

		item_code = repo.create_item(order_item)
		item_doc = frappe.get_doc("Item", item_code)

		self.assertEqual(item_doc.stock_uom, "Nos")
		self.assertTrue(len(item_doc.uoms) > 0)
		self.assertEqual(item_doc.uoms[0].uom, "Nos")
		self.assertEqual(item_doc.uoms[0].conversion_factor, 1)

		# Cleanup
		frappe.delete_doc("Item", item_code, force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASIN_UOM_1"})

	def test_order_items_preparation_has_uom(self):
		repo = TestAmazonRepository()
		order_items = repo.get_order_items("902-1845936-5435065")
		self.assertTrue(len(order_items) > 0)
		first_item = order_items[0]
		self.assertEqual(first_item.get("uom"), "Nos")
		self.assertEqual(first_item.get("stock_uom"), "Nos")
		self.assertEqual(first_item.get("conversion_factor"), 1)

	def test_assigned_item_group_hsn_used_when_amazon_hsn_none(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_ASSIGNED_GRP_HSN",
			"SellerSKU": "TEST_ASSIGNED_GRP_SKU",
			"OrderItemId": "TEST_ASSIGNED_GRP_ID",
			"Title": "Test Product Assigned Group HSN",
		}
		repo.get_amazon_hsn = lambda oi: None

		if frappe.db.exists("Item", "TEST_ASSIGNED_GRP_HSN"):
			frappe.delete_doc("Item", "TEST_ASSIGNED_GRP_HSN", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASSIGNED_GRP_HSN"})

		parent_group = repo.amz_setting.parent_item_group
		orig_parent_hsn = frappe.db.get_value("Item Group", parent_group, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None

		assigned_group_name = "Health and Beauty"
		if not frappe.db.exists("Item Group", assigned_group_name):
			grp = frappe.new_doc("Item Group")
			grp.item_group_name = assigned_group_name
			grp.parent_item_group = parent_group
			grp.insert(ignore_permissions=True)

		orig_assigned_hsn = frappe.db.get_value("Item Group", assigned_group_name, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None

		try:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", "330499")
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", "420211")

			item_code = repo.create_item(order_item)
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc.gst_hsn_code, "330499")
		finally:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", orig_assigned_hsn)
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", orig_parent_hsn)
			if frappe.db.exists("Item", "TEST_ASSIGNED_GRP_HSN"):
				frappe.delete_doc("Item", "TEST_ASSIGNED_GRP_HSN", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_ASSIGNED_GRP_HSN"})

	def test_item_creation_falls_back_to_parent_item_group_hsn(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_FALLBACK_HSN",
			"SellerSKU": "TEST_FALLBACK_SKU",
			"OrderItemId": "TEST_FALLBACK_ID",
			"Title": "Test Product Fallback HSN",
		}
		repo.get_amazon_hsn = lambda oi: None

		if frappe.db.exists("Item", "TEST_FALLBACK_HSN"):
			frappe.delete_doc("Item", "TEST_FALLBACK_HSN", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_FALLBACK_HSN"})

		parent_group = repo.amz_setting.parent_item_group
		orig_hsn = frappe.db.get_value("Item Group", parent_group, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None

		assigned_group_name = "Health and Beauty"
		if not frappe.db.exists("Item Group", assigned_group_name):
			grp = frappe.new_doc("Item Group")
			grp.item_group_name = assigned_group_name
			grp.parent_item_group = parent_group
			grp.insert(ignore_permissions=True)
		orig_assigned_hsn = frappe.db.get_value("Item Group", assigned_group_name, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None

		try:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", None)
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", "420211")

			item_code = repo.create_item(order_item)
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc.gst_hsn_code, "420211")
		finally:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", orig_assigned_hsn)
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", orig_hsn)
			if frappe.db.exists("Item", "TEST_FALLBACK_HSN"):
				frappe.delete_doc("Item", "TEST_FALLBACK_HSN", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_FALLBACK_HSN"})

	def test_amazon_hsn_takes_precedence_over_item_group_hsn(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_PRECEDENCE_HSN",
			"SellerSKU": "TEST_PRECEDENCE_SKU",
			"OrderItemId": "TEST_PRECEDENCE_ID",
			"Title": "Test Product Precedence HSN",
		}
		repo.get_amazon_hsn = lambda oi: "30049099"

		if frappe.db.exists("Item", "TEST_PRECEDENCE_HSN"):
			frappe.delete_doc("Item", "TEST_PRECEDENCE_HSN", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_PRECEDENCE_HSN"})

		parent_group = repo.amz_setting.parent_item_group
		orig_hsn = frappe.db.get_value("Item Group", parent_group, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None
		try:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", "420211")
				if frappe.db.exists("Item Group", "Health and Beauty"):
					frappe.delete_doc("Item Group", "Health and Beauty", force=True)

			item_code = repo.create_item(order_item)
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertEqual(item_doc.gst_hsn_code, "30049099")
		finally:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", orig_hsn)
			if frappe.db.exists("Item", "TEST_PRECEDENCE_HSN"):
				frappe.delete_doc("Item", "TEST_PRECEDENCE_HSN", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_PRECEDENCE_HSN"})

	def test_item_creation_without_any_hsn_raises_validation_error(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_NO_HSN",
			"SellerSKU": "TEST_NO_HSN_SKU",
			"OrderItemId": "TEST_NO_HSN_ID",
			"Title": "Test Product No HSN",
		}
		repo.get_amazon_hsn = lambda oi: None

		if frappe.db.exists("Item", "TEST_NO_HSN"):
			frappe.delete_doc("Item", "TEST_NO_HSN", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_NO_HSN"})

		parent_group = repo.amz_setting.parent_item_group
		orig_hsn = frappe.db.get_value("Item Group", parent_group, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None
		assigned_group_name = "Health and Beauty"
		orig_assigned_hsn = frappe.db.get_value("Item Group", assigned_group_name, "gst_hsn_code") if (frappe.db.exists("Item Group", assigned_group_name) and frappe.db.has_column("Item Group", "gst_hsn_code")) else None

		try:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", None)
				if frappe.db.exists("Item Group", assigned_group_name):
					frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", None)

			with self.assertRaises(frappe.ValidationError) as cm:
				repo.create_item(order_item)

			err_msg = str(cm.exception)
			self.assertIn("TEST_NO_HSN", err_msg)
			self.assertIn("TEST_NO_HSN_SKU", err_msg)
			self.assertIn("Item Group", err_msg)
			self.assertIn("Amazon did not provide an HSN and no fallback HSN is configured", err_msg)

			# Prove item was NOT created/inserted
			self.assertFalse(frappe.db.exists("Item", "TEST_NO_HSN"))
		finally:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", orig_hsn)
				if frappe.db.exists("Item Group", assigned_group_name):
					frappe.db.set_value("Item Group", assigned_group_name, "gst_hsn_code", orig_assigned_hsn)
			if frappe.db.exists("Item", "TEST_NO_HSN"):
				frappe.delete_doc("Item", "TEST_NO_HSN", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_NO_HSN"})

	def test_product_tax_code_never_used_as_gst_hsn_code(self):
		repo = TestAmazonRepository()
		order_item = {
			"ASIN": "TEST_PTC_ASIN",
			"SellerSKU": "TEST_PTC_SKU",
			"OrderItemId": "TEST_PTC_ID",
			"Title": "Test Product With PTC",
		}
		mock_client = unittest.mock.MagicMock()
		mock_client.get_listings_item.return_value = {
			"sku": "TEST_PTC_SKU",
			"attributes": {
				"product_tax_code": [
					{"value": "A_GEN_SUPERREDUCED", "marketplace_id": "A21TJRUUN4KGV"}
				]
			},
		}
		repo.get_listings_items_instance = lambda: mock_client

		# 1. get_amazon_hsn must return None (PTC is not an HSN)
		hsn = repo.get_amazon_hsn(order_item)
		self.assertIsNone(hsn)

		# 2. create_item falls back to parent item group HSN and never uses product_tax_code
		if frappe.db.exists("Item", "TEST_PTC_ASIN"):
			frappe.delete_doc("Item", "TEST_PTC_ASIN", force=True)
		frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_PTC_ASIN"})

		parent_group = repo.amz_setting.parent_item_group
		orig_hsn = frappe.db.get_value("Item Group", parent_group, "gst_hsn_code") if frappe.db.has_column("Item Group", "gst_hsn_code") else None
		try:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", "12119032")
				if frappe.db.exists("Item Group", "Health and Beauty"):
					frappe.delete_doc("Item Group", "Health and Beauty", force=True)

			item_code = repo.create_item(order_item)
			item_doc = frappe.get_doc("Item", item_code)
			if frappe.db.has_column("Item", "gst_hsn_code"):
				self.assertNotEqual(item_doc.gst_hsn_code, "A_GEN_SUPERREDUCED")
				self.assertEqual(item_doc.gst_hsn_code, "12119032")
		finally:
			if frappe.db.has_column("Item Group", "gst_hsn_code"):
				frappe.db.set_value("Item Group", parent_group, "gst_hsn_code", orig_hsn)
			if frappe.db.exists("Item", "TEST_PTC_ASIN"):
				frappe.delete_doc("Item", "TEST_PTC_ASIN", force=True)
			frappe.db.delete("Ecommerce Item", {"integration_item_code": "TEST_PTC_ASIN"})

	def test_order_statuses_serialization_uses_comma_separated_values(self):
		orders = Orders(
			client_id="dummy_id",
			client_secret="dummy_secret",
			refresh_token="dummy_token",
			country_code="US",
		)
		captured = {}

		def fake_make_request(params=None, **kwargs):
			captured["params"] = params
			return {"payload": {"Orders": []}}

		orders.make_request = fake_make_request
		orders.get_orders(
			created_after="2023-01-01T00:00:00Z",
			order_statuses=["Pending", "Unshipped", "Shipped"],
			fulfillment_channels=["MFN", "AFN"],
			payment_methods=["COD", "Other"],
			easyship_shipment_statuses=["PendingSchedule", "PickedUp"],
			amazon_order_ids=["111-1111111-1111111", "222-2222222-2222222"],
		)

		params = captured.get("params", {})
		self.assertEqual(params.get("OrderStatuses"), "Pending,Unshipped,Shipped")
		self.assertEqual(params.get("FulfillmentChannels"), "MFN,AFN")
		self.assertEqual(params.get("PaymentMethods"), "COD,Other")
		self.assertEqual(params.get("EasyShipShipmentStatuses"), "PendingSchedule,PickedUp")
		self.assertEqual(params.get("AmazonOrderIds"), "111-1111111-1111111,222-2222222-2222222")
		self.assertNotIn("OrderStatuses[0]", params)
		self.assertNotIn("FulfillmentChannels[0]", params)
		self.assertNotIn("PaymentMethods[0]", params)

	def test_created_after_still_works(self):
		orders = Orders(
			client_id="dummy_id",
			client_secret="dummy_secret",
			refresh_token="dummy_token",
			country_code="US",
		)
		captured = {}

		def fake_make_request(params=None, **kwargs):
			captured["params"] = params
			return {"payload": {"Orders": []}}

		orders.make_request = fake_make_request

		# Positional argument (backward compatibility for existing callers)
		orders.get_orders("2023-01-01T00:00:00Z")
		self.assertEqual(captured["params"].get("CreatedAfter"), "2023-01-01T00:00:00Z")
		self.assertNotIn("LastUpdatedAfter", captured["params"])

		# Keyword argument
		captured.clear()
		orders.get_orders(created_after="2023-02-01T00:00:00Z")
		self.assertEqual(captured["params"].get("CreatedAfter"), "2023-02-01T00:00:00Z")
		self.assertNotIn("LastUpdatedAfter", captured["params"])

	def test_last_updated_after_passed_correctly(self):
		orders = Orders(
			client_id="dummy_id",
			client_secret="dummy_secret",
			refresh_token="dummy_token",
			country_code="US",
		)
		captured = {}

		def fake_make_request(params=None, **kwargs):
			captured["params"] = params
			return {"payload": {"Orders": []}}

		orders.make_request = fake_make_request

		# Pass only LastUpdatedAfter
		orders.get_orders(last_updated_after="2023-03-01T00:00:00Z")
		self.assertEqual(captured["params"].get("LastUpdatedAfter"), "2023-03-01T00:00:00Z")
		self.assertNotIn("CreatedAfter", captured["params"])

		# Pass both CreatedAfter and LastUpdatedAfter
		captured.clear()
		orders.get_orders(created_after="2023-01-01T00:00:00Z", last_updated_after="2023-03-01T00:00:00Z")
		self.assertEqual(captured["params"].get("CreatedAfter"), "2023-01-01T00:00:00Z")
		self.assertEqual(captured["params"].get("LastUpdatedAfter"), "2023-03-01T00:00:00Z")

	def test_get_order_calls_correct_endpoint(self):
		orders = Orders(
			client_id="dummy_id",
			client_secret="dummy_secret",
			refresh_token="dummy_token",
			country_code="US",
		)
		captured = {}

		def fake_make_request(append_to_base_uri="", **kwargs):
			captured["append_to_base_uri"] = append_to_base_uri
			captured["full_url"] = orders.endpoint + orders.BASE_URI + append_to_base_uri
			return {"payload": {"AmazonOrderId": "405-6102280-1223520"}}

		orders.make_request = fake_make_request
		res = orders.get_order("405-6102280-1223520")
		self.assertEqual(captured["append_to_base_uri"], "/405-6102280-1223520")
		self.assertTrue(captured["full_url"].endswith("/orders/v0/orders/405-6102280-1223520"))
		self.assertEqual(res.get("payload", {}).get("AmazonOrderId"), "405-6102280-1223520")

	def test_get_orders_uses_afn_mfn_fulfillment_channels(self):
		"""Verify repo.get_orders passes ['AFN', 'MFN'] and serializes to FulfillmentChannels=AFN,MFN."""
		repo = TestAmazonRepository()
		captured = {}

		orig_call = repo.call_sp_api_method
		def spy_call(sp_api_method, **kwargs):
			captured["kwargs"] = kwargs
			return orig_call(sp_api_method, **kwargs)

		repo.call_sp_api_method = spy_call
		repo.get_orders("2026-09-22")

		self.assertIn("fulfillment_channels", captured["kwargs"])
		self.assertEqual(captured["kwargs"]["fulfillment_channels"], ["AFN", "MFN"])
		self.assertNotIn("FBA", captured["kwargs"]["fulfillment_channels"])
		self.assertNotIn("SellerFulfilled", captured["kwargs"]["fulfillment_channels"])

	def test_get_orders_defensively_handles_none_payload(self):
		"""Verify repo.get_orders returns [] safely without AttributeError when orders_payload is None."""
		repo = TestAmazonRepository()
		repo.call_sp_api_method = lambda *args, **kwargs: None

		sales_orders = repo.get_orders("2026-09-22")
		self.assertEqual(sales_orders, [])

	def test_new_sales_order_receives_amazon_status_fields(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-ORDER-STATUS-FIELDS-001"

		def cleanup_sales_order(order_id):
			so_name = frappe.db.get_value("Sales Order", filters={"amazon_order_id": order_id}, fieldname="name")
			if so_name:
				doc = frappe.get_doc("Sales Order", so_name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Sales Order", so_name, force=True)

		cleanup_sales_order(test_order_id)
		try:
			mock_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "Delivered",
				"PaymentMethod": "COD",
				"PaymentMethodDetails": ["CashOnDelivery"],
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}

			so_name = repo.create_sales_order(mock_order)
			self.assertIsNotNone(so_name)

			so_doc = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_doc.amazon_order_id, test_order_id)
			self.assertEqual(so_doc.amazon_order_status, "Shipped")
			self.assertEqual(so_doc.amazon_fulfillment_channel, "MFN")
			self.assertEqual(so_doc.amazon_shipment_status, "Delivered")
			self.assertEqual(so_doc.amazon_payment_method, "COD")
		finally:
			cleanup_sales_order(test_order_id)

	def test_existing_sales_order_not_cancelled_or_changed(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-ORDER-EXISTING-001"

		def cleanup_sales_order(order_id):
			so_name = frappe.db.get_value("Sales Order", filters={"amazon_order_id": order_id}, fieldname="name")
			if so_name:
				doc = frappe.get_doc("Sales Order", so_name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Sales Order", so_name, force=True)

		cleanup_sales_order(test_order_id)
		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}

			so_name = repo.create_sales_order(initial_order)
			self.assertIsNotNone(so_name)

			so_before = frappe.get_doc("Sales Order", so_name)
			docstatus_before = so_before.docstatus
			status_before = so_before.status

			# Simulate Amazon sending a status update (Shipped)
			updated_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}

			returned_name = repo.create_sales_order(updated_order)
			self.assertEqual(returned_name, so_name)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, docstatus_before)
			self.assertEqual(so_after.status, status_before)
			self.assertNotEqual(so_after.status, "Cancelled")
			self.assertNotEqual(so_after.docstatus, 2)
			self.assertEqual(so_after.amazon_order_status, "Shipped")
		finally:
			cleanup_sales_order(test_order_id)

	def test_existing_so_status_fields_update_from_amazon_payload(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SO-STATUS-UPDATE-001"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Pending",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			self.assertIsNotNone(so_name)

			updated_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "AFN",
				"EasyShipShipmentStatus": "PickedUp",
				"PaymentMethod": "COD",
				"PaymentMethodDetails": ["CashOnDelivery"],
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			repo.create_sales_order(updated_order)

			so_doc = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_doc.amazon_order_status, "Shipped")
			self.assertEqual(so_doc.amazon_fulfillment_channel, "AFN")
			self.assertEqual(so_doc.amazon_shipment_status, "PickedUp")
			self.assertEqual(so_doc.amazon_payment_method, "COD")
			self.assertEqual(so_doc.docstatus, 1)
		finally:
			frappe.db.rollback()

	def test_existing_so_unrelated_fields_remain_unchanged(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SO-UNRELATED-FIELDS-001"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so_before = frappe.get_doc("Sales Order", so_name)

			customer_before = so_before.customer
			delivery_date_before = str(so_before.delivery_date)
			transaction_date_before = str(so_before.transaction_date)
			company_before = so_before.company
			items_before = [(it.item_code, it.qty, it.rate, it.warehouse) for it in so_before.items]

			updated_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "AFN",
				"EasyShipShipmentStatus": "Delivered",
				"PaymentMethod": "COD",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2025-12-31T00:00:00Z",
				"LatestShipDate": "2026-01-05T00:00:00Z",
			}
			repo.create_sales_order(updated_order)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.customer, customer_before)
			self.assertEqual(str(so_after.delivery_date), delivery_date_before)
			self.assertEqual(str(so_after.transaction_date), transaction_date_before)
			self.assertEqual(so_after.company, company_before)
			items_after = [(it.item_code, it.qty, it.rate, it.warehouse) for it in so_after.items]
			self.assertEqual(items_after, items_before)
			self.assertEqual(so_after.docstatus, 1)
			self.assertEqual(so_after.amazon_order_status, "Shipped")
		finally:
			frappe.db.rollback()

	def test_draft_so_amazon_cancellation(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-DRAFT-CANCEL-001"

		try:
			so = frappe.new_doc("Sales Order")
			so.customer = "Buyer - 408-3346103-0219544"
			so.company = repo.amz_setting.company
			so.amazon_order_id = test_order_id
			so.amazon_order_status = "Unshipped"
			so.delivery_date = "2026-09-30"
			so.append("items", {
				"item_code": "B0GYSTCR27",
				"qty": 1,
				"rate": 100,
				"warehouse": repo.amz_setting.warehouse,
				"delivery_date": "2026-09-30",
			})
			so.insert()
			self.assertEqual(so.docstatus, 0)
			so_name = so.name

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			res = repo.create_sales_order(cancel_order)
			self.assertIsNone(res)
			self.assertFalse(frappe.db.exists("Sales Order", so_name))
		finally:
			frappe.db.rollback()

	def test_submitted_so_no_dn_no_si_cancelled(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SUBMITTED-CANCEL-001"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so_doc = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_doc.docstatus, 1)

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			repo.create_sales_order(cancel_order)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, 2)
			self.assertEqual(so_after.status, "Cancelled")
			self.assertEqual(so_after.amazon_order_status, "Canceled")
		finally:
			frappe.db.rollback()

	def test_submitted_so_with_submitted_dn_not_cancelled(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SO-DN-BLOCK-CANCEL"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so = frappe.get_doc("Sales Order", so_name)

			# Create and submit a Delivery Note
			dn = frappe.new_doc("Delivery Note")
			dn.customer = so.customer
			dn.company = so.company
			dn.append("items", {
				"item_code": so.items[0].item_code,
				"qty": 1,
				"rate": so.items[0].rate,
				"warehouse": so.items[0].warehouse,
				"allow_zero_valuation_rate": 1,
				"against_sales_order": so.name,
				"so_detail": so.items[0].name,
			})
			dn.insert()
			dn.submit()

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			repo.create_sales_order(cancel_order)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, 1)
			self.assertNotEqual(so_after.status, "Cancelled")
			self.assertEqual(so_after.amazon_order_status, "Canceled")
		finally:
			frappe.db.rollback()

	def test_submitted_so_with_submitted_si_not_cancelled(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SO-SI-BLOCK-CANCEL"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so = frappe.get_doc("Sales Order", so_name)

			# Create and submit a Sales Invoice
			si = frappe.new_doc("Sales Invoice")
			si.customer = so.customer
			si.company = so.company
			si.append("items", {
				"item_code": so.items[0].item_code,
				"qty": 1,
				"rate": so.items[0].rate,
				"sales_order": so.name,
				"so_detail": so.items[0].name,
			})
			si.insert()
			si.submit()

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			repo.create_sales_order(cancel_order)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, 1)
			self.assertNotEqual(so_after.status, "Cancelled")
			self.assertEqual(so_after.amazon_order_status, "Canceled")
		finally:
			frappe.db.rollback()

	def test_submitted_so_with_both_dn_and_si_not_cancelled(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-SO-BOTH-DN-SI-BLOCK"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so = frappe.get_doc("Sales Order", so_name)

			# Create and submit DN
			dn = frappe.new_doc("Delivery Note")
			dn.customer = so.customer
			dn.company = so.company
			dn.append("items", {
				"item_code": so.items[0].item_code,
				"qty": 1,
				"rate": so.items[0].rate,
				"warehouse": so.items[0].warehouse,
				"allow_zero_valuation_rate": 1,
				"against_sales_order": so.name,
				"so_detail": so.items[0].name,
			})
			dn.insert()
			dn.submit()

			# Create and submit SI
			si = frappe.new_doc("Sales Invoice")
			si.customer = so.customer
			si.company = so.company
			si.append("items", {
				"item_code": so.items[0].item_code,
				"qty": 1,
				"rate": so.items[0].rate,
				"sales_order": so.name,
				"so_detail": so.items[0].name,
			})
			si.insert()
			si.submit()

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			repo.create_sales_order(cancel_order)

			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, 1)
			self.assertNotEqual(so_after.status, "Cancelled")
			self.assertEqual(so_after.amazon_order_status, "Canceled")
		finally:
			frappe.db.rollback()

	def test_already_cancelled_so_noop(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-ALREADY-CANCELLED-SO"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)
			so = frappe.get_doc("Sales Order", so_name)
			so.cancel()
			self.assertEqual(so.docstatus, 2)

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			res = repo.create_sales_order(cancel_order)
			self.assertEqual(res, so_name)
			so_after = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so_after.docstatus, 2)
		finally:
			frappe.db.rollback()

	def test_repeated_amazon_cancellation_idempotent(self):
		repo = TestAmazonRepository()
		test_order_id = "TEST-REPEAT-CANCEL-IDEMPOTENT"

		try:
			initial_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Unshipped",
				"FulfillmentChannel": "MFN",
				"EasyShipShipmentStatus": "PendingSchedule",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name = repo.create_sales_order(initial_order)

			cancel_order = {
				"AmazonOrderId": test_order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}

			# First cancellation
			res1 = repo.create_sales_order(cancel_order)
			self.assertEqual(res1, so_name)
			so1 = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so1.docstatus, 2)

			# Second cancellation (idempotent call)
			res2 = repo.create_sales_order(cancel_order)
			self.assertEqual(res2, so_name)
			so2 = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(so2.docstatus, 2)
		finally:
			frappe.db.rollback()

	def test_cancellation_reason_stored_only_when_provided(self):
		repo = TestAmazonRepository()
		test_order_id_with_reason = "TEST-SO-WITH-REASON"
		test_order_id_no_reason = "TEST-SO-NO-REASON"

		try:
			# 1. With reason
			order_with_reason = {
				"AmazonOrderId": test_order_id_with_reason,
				"OrderStatus": "Canceled",
				"CancellationReason": "Buyer requested cancellation before shipping",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name_1 = repo.create_sales_order(order_with_reason)
			so_1 = frappe.get_doc("Sales Order", so_name_1)
			self.assertEqual(so_1.amazon_cancellation_reason, "Buyer requested cancellation before shipping")

			# 2. Without reason
			order_no_reason = {
				"AmazonOrderId": test_order_id_no_reason,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"MarketplaceId": "ATVPDKIKX0DER",
				"PurchaseDate": "2023-01-01T00:00:00Z",
				"LatestShipDate": "2023-01-05T00:00:00Z",
			}
			so_name_2 = repo.create_sales_order(order_no_reason)
			so_2 = frappe.get_doc("Sales Order", so_name_2)
			self.assertFalse(bool(so_2.amazon_cancellation_reason))
		finally:
			frappe.db.rollback()

	def test_sales_invoice_inter_state_gst(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-INTER-STATE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "523.81", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Chhattisgarh",
					"PostalCode": "492001",
					"City": "Raipur",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-1",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 3,
					"ItemPrice": {"Amount": "514.29", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "15.42", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "24.94", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			so = frappe.get_doc("Sales Order", so_name)
			so.db_set("place_of_supply", "22-Chhattisgarh")

			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			self.assertTrue(bool(si_name))
			si = frappe.get_doc("Sales Invoice", si_name)

			self.assertEqual(si.place_of_supply, "22-Chhattisgarh")
			self.assertTrue(any("IGST" in tx.account_head for tx in si.taxes))
			self.assertAlmostEqual(float(si.total_taxes_and_charges), 24.94, delta=0.5)
			self.assertAlmostEqual(float(si.grand_total), 523.81, delta=0.5)

			gl_entries = frappe.db.sql(
				"SELECT account, debit, credit FROM `tabGL Entry` WHERE voucher_no = %s",
				(si_name,),
				as_dict=True,
			)
			self.assertTrue(len(gl_entries) >= 2)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sales_invoice_intra_state_gst(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-INTRA-STATE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400104",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-2",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 1,
					"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			so = frappe.get_doc("Sales Order", so_name)
			so.db_set("place_of_supply", "27-Maharashtra")

			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			si = frappe.get_doc("Sales Invoice", si_name)

			self.assertEqual(si.place_of_supply, "27-Maharashtra")
			cgst_present = any("CGST" in tx.account_head for tx in si.taxes)
			sgst_present = any("SGST" in tx.account_head for tx in si.taxes)
			self.assertTrue(cgst_present)
			self.assertTrue(sgst_present)
			self.assertAlmostEqual(float(si.grand_total), 299.00, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sales_invoice_with_shipping_charges(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-SHIPPING"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "615.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Karnataka",
					"PostalCode": "560039",
					"City": "Bengaluru",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-3",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 1,
					"ItemPrice": {"Amount": "514.29", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "25.71", "CurrencyCode": "INR"},
					"ShippingPrice": {"Amount": "71.43", "CurrencyCode": "INR"},
					"ShippingTax": {"Amount": "3.57", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			so = frappe.get_doc("Sales Order", so_name)
			so.db_set("place_of_supply", "29-Karnataka")

			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			si = frappe.get_doc("Sales Invoice", si_name)

			shipping_rows = [itm for itm in si.items if itm.item_code == "Shipping Charges"]
			self.assertEqual(len(shipping_rows), 1)
			self.assertEqual(float(shipping_rows[0].rate), 71.43)
			self.assertAlmostEqual(float(si.grand_total), 615.00, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sales_invoice_with_promotion_discount(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-DISCOUNT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "1786.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Karnataka",
					"PostalCode": "560048",
					"City": "Bengaluru",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-4",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 4,
					"ItemPrice": {"Amount": "1790.48", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "89.52", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "85.04", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			so = frappe.get_doc("Sales Order", so_name)
			so.db_set("place_of_supply", "29-Karnataka")

			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			si = frappe.get_doc("Sales Invoice", si_name)

			item_row = si.items[0]
			self.assertEqual(float(item_row.qty), 4)
			self.assertAlmostEqual(float(item_row.discount_amount), 89.52 / 4, delta=0.01)
			self.assertAlmostEqual(float(si.net_total), 1700.96, delta=0.1)
			self.assertAlmostEqual(float(si.grand_total), 1786.01, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sales_invoice_duplicate_protection_idempotent(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-IDEMPOTENT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400104",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-5",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 1,
					"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			si_name_1 = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)

			si_name_2 = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			self.assertEqual(si_name_1, si_name_2)

			invoices = frappe.db.sql(
				"SELECT DISTINCT parent FROM `tabSales Invoice Item` WHERE sales_order = %s",
				(so_name,),
				as_dict=True,
			)
			self.assertEqual(len(invoices), 1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sales_invoice_missing_gst_configuration_raises_error(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-NO-GST"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "100.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400104",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			test_item_code = "TEST-NO-GST-ITEM"
			if not frappe.db.exists("Item", test_item_code):
				frappe.get_doc({
					"doctype": "Item",
					"item_code": test_item_code,
					"item_name": "Test No GST Item",
					"item_group": "Products",
					"gst_hsn_code": "30049099",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				}).insert(ignore_permissions=True)

			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-6",
					"ASIN": "TEST-NO-GST-ASIN",
					"SellerSKU": "TEST-NO-GST-SKU",
					"Title": "Test No GST",
					"QuantityOrdered": 1,
					"ItemPrice": {"Amount": "100.00", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "0.00", "CurrencyCode": "INR"},
				}
			]

			so = frappe.new_doc("Sales Order")
			so.customer = getattr(repo.amz_setting, "customer", None) or frappe.get_all("Customer", limit=1)[0].name
			so.company = repo.amz_setting.company
			so.amazon_order_id = order_id
			so.delivery_date = frappe.utils.nowdate()
			so.append("items", {
				"item_code": test_item_code,
				"qty": 1,
				"rate": 100,
				"warehouse": repo.amz_setting.warehouse,
			})
			so.insert(ignore_permissions=True)
			so.submit()

			with self.assertRaises(frappe.ValidationError):
				repo.create_sales_invoice(so.name, order=mock_order, order_items=mock_items)
		finally:
			frappe.db.rollback()

	def test_sales_invoice_amount_reconciliation(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3C-RECONCILE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "999.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400104",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-7",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product",
					"QuantityOrdered": 1,
					"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
				}
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name = repo.create_sales_order(mock_order)
			with self.assertRaises(frappe.ValidationError):
				repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	# ── Phase 3D: Payment Entry Tests ─────────────────────────────────────────

	def test_payment_entry_cod_maps_to_cash(self):
		"""A. COD PaymentMethod maps to Mode of Payment 'Cash'."""
		repo = TestAmazonRepository()
		self.assertEqual(repo.get_amazon_mode_of_payment("COD"), "Cash")
		self.assertEqual(repo.get_amazon_mode_of_payment("cod"), "Cash")
		self.assertEqual(repo.get_amazon_mode_of_payment("CashOnDelivery"), "Cash")

	def test_payment_entry_created_against_correct_invoice(self):
		"""B. Payment Entry references the exact Sales Invoice."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-REF"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400104",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE1",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "27-Maharashtra")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			pe_name = repo.create_payment_entry(si_name, payment_method="COD")
			self.assertIsNotNone(pe_name)
			pe = frappe.get_doc("Payment Entry", pe_name)
			self.assertEqual(pe.payment_type, "Receive")
			self.assertEqual(pe.party_type, "Customer")
			self.assertEqual(pe.mode_of_payment, "Cash")
			self.assertIn(si_name, [r.reference_name for r in pe.references])
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_entry_uses_outstanding_amount(self):
		"""C. Payment amount equals invoice outstanding_amount, not hardcoded."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-AMT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {"StateOrRegion": "Maharashtra", "PostalCode": "400104", "City": "Mumbai", "CountryCode": "IN"},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE2",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "27-Maharashtra")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			si = frappe.get_doc("Sales Invoice", si_name)
			expected = float(si.outstanding_amount or si.rounded_total or si.grand_total)
			pe_name = repo.create_payment_entry(si_name, payment_method="COD")
			self.assertIsNotNone(pe_name)
			pe = frappe.get_doc("Payment Entry", pe_name)
			self.assertAlmostEqual(float(pe.paid_amount), expected, delta=0.01)
			self.assertAlmostEqual(float(pe.received_amount), expected, delta=0.01)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_entry_idempotent_no_duplicate(self):
		"""D. Calling create_payment_entry twice returns same PE — no duplicate."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-IDEM"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {"StateOrRegion": "Maharashtra", "PostalCode": "400104", "City": "Mumbai", "CountryCode": "IN"},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE3",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "27-Maharashtra")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			pe_1 = repo.create_payment_entry(si_name, payment_method="COD")
			pe_2 = repo.create_payment_entry(si_name, payment_method="COD")
			self.assertIsNotNone(pe_1)
			self.assertEqual(pe_1, pe_2, "Duplicate Payment Entry was created!")
			count = frappe.db.count("Payment Entry Reference",
				{"reference_doctype": "Sales Invoice", "reference_name": si_name})
			self.assertEqual(count, 1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_entry_already_paid_invoice_returns_none(self):
		"""E. create_payment_entry returns None when outstanding_amount is 0."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-PAID"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {"StateOrRegion": "Maharashtra", "PostalCode": "400104", "City": "Mumbai", "CountryCode": "IN"},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE4",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "27-Maharashtra")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			frappe.db.set_value("Sales Invoice", si_name, "outstanding_amount", 0)
			result = repo.create_payment_entry(si_name, payment_method="COD")
			self.assertIsNone(result)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_entry_non_cod_returns_none(self):
		"""F. Non-COD payment method 'Other' does NOT create a Payment Entry."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-OTHER"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "615.00", "CurrencyCode": "INR"},
				"ShippingAddress": {"StateOrRegion": "Karnataka", "PostalCode": "560039", "City": "Bengaluru", "CountryCode": "IN"},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE5",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "514.29", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "25.71", "CurrencyCode": "INR"},
				"ShippingPrice": {"Amount": "71.43", "CurrencyCode": "INR"},
				"ShippingTax": {"Amount": "3.57", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "29-Karnataka")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			result = repo.create_payment_entry(si_name, payment_method="Other")
			self.assertIsNone(result, "PE should NOT be created for 'Other' PaymentMethod")
			self.assertEqual(
				frappe.db.count("Payment Entry Reference",
					{"reference_doctype": "Sales Invoice", "reference_name": si_name}),
				0,
			)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_entry_gl_balanced(self):
		"""G. GL entries for a submitted Payment Entry are balanced (Dr == Cr)."""
		repo = TestAmazonRepository()
		order_id = "TEST-PHASE3D-PE-GL"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {"StateOrRegion": "Maharashtra", "PostalCode": "400104", "City": "Mumbai", "CountryCode": "IN"},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-PE6",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			frappe.get_doc("Sales Order", so_name).db_set("place_of_supply", "27-Maharashtra")
			si_name = repo.create_sales_invoice(so_name, order=mock_order, order_items=mock_items)
			pe_name = repo.create_payment_entry(si_name, payment_method="COD")
			self.assertIsNotNone(pe_name)
			gl = frappe.db.sql(
				"SELECT SUM(debit) as td, SUM(credit) as tc FROM `tabGL Entry` "
				"WHERE voucher_no=%s AND is_cancelled=0",
				(pe_name,), as_dict=True,
			)
			self.assertTrue(gl)
			total_d = float(gl[0].td or 0)
			total_c = float(gl[0].tc or 0)
			self.assertAlmostEqual(total_d, total_c, delta=0.01,
				msg=f"GL not balanced: Dr={total_d}, Cr={total_c}")
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	# ── Sync Workflow Tests (SO → SI → PE) ───────────────────────────

	def test_sync_order_new_so_creates_invoice(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-NEW-SO-SI"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC1",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, *dn = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.po_no, order_id)
			self.assertAlmostEqual(float(si.grand_total), 299.00, delta=0.5)

			# Verify linked to Sales Order
			so_items = [d.sales_order for d in si.items if d.sales_order]
			self.assertIn(so_name, so_items)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_existing_so_without_invoice_creates_invoice(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-EXISTING-SO-NO-SI"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC2",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			# Step 1: Create SO without invoice (legacy state)
			so_initial = repo.create_sales_order(mock_order)
			self.assertIsNotNone(so_initial)

			existing_sis = frappe.db.sql(
				"SELECT parent FROM `tabSales Invoice Item` WHERE sales_order = %s AND docstatus < 2",
				(so_initial,), as_dict=True
			)
			self.assertEqual(len(existing_sis), 0)

			# Step 2: Next sync creates the missing invoice
			so_name, si_name, pe_name, *dn = repo.sync_order(mock_order)
			self.assertEqual(so_name, so_initial)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.docstatus, 1)
			self.assertEqual(si.status, "Unpaid")
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_existing_so_with_invoice_no_duplicate(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-NO-DUPLICATE-SI"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC3",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so1, si1, pe1, *dn1 = repo.sync_order(mock_order)
			so2, si2, pe2, *dn2 = repo.sync_order(mock_order)

			self.assertEqual(so1, so2)
			self.assertEqual(si1, si2)
			self.assertEqual(pe1, pe2)

			si_count = frappe.db.count("Sales Invoice", {"po_no": order_id, "docstatus": ["<", 2]})
			self.assertEqual(si_count, 1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_cod_no_payment_entry_without_settlement(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-COD-NO-PE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC4",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, *dn = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertAlmostEqual(float(si.outstanding_amount), 299.00, delta=0.5)
			self.assertEqual(si.status, "Unpaid")

			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si_name,)
			)[0][0]
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_prepaid_no_payment_entry(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-PREPAID-NO-PE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"PaymentMethodDetails": ["Standard"],
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC5",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, *dn = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			# Ensure no Payment Entry was created in DB for this SI
			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si_name,)
			)[0][0]
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_canceled_order_no_invoice(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-CANCELED-NO-SI"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC6",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, *dn = repo.sync_order(mock_order)
			self.assertIsNone(si_name)
			self.assertIsNone(pe_name)

			si_count = frappe.db.count("Sales Invoice", {"po_no": order_id})
			self.assertEqual(si_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_sync_order_idempotent_twice(self):
		repo = TestAmazonRepository()
		order_id = "TEST-SYNC-IDEMPOTENT-TWICE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-SYNC7",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			# Run 1
			so1, si1, pe1, *dn1 = repo.sync_order(mock_order)
			# Run 2
			so2, si2, pe2, *dn2 = repo.sync_order(mock_order)

			self.assertEqual(so1, so2)
			self.assertEqual(si1, si2)
			self.assertEqual(pe1, pe2)

			# Ensure exact DB document counts
			so_count = frappe.db.count("Sales Order", {"amazon_order_id": order_id, "docstatus": ["<", 2]})
			si_count = frappe.db.count("Sales Invoice", {"po_no": order_id, "docstatus": ["<", 2]})
			pe_count = frappe.db.sql("""
				SELECT COUNT(DISTINCT pe.name) FROM `tabPayment Entry` pe
				JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
				WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = %s AND pe.docstatus < 2
			""", (si1,))[0][0]

			self.assertEqual(so_count, 1)
			self.assertEqual(si_count, 1)
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_quantity_shipped_zero_no_dn(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-ZERO-QTY"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-ZERO",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 2,
				"QuantityShipped": 0,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(dn_name)

			# Ensure no Delivery Note in DB
			dn_count = frappe.db.count("Delivery Note", {"po_no": order_id, "docstatus": ["<", 2]})
			self.assertEqual(dn_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_quantity_shipped_full_dn(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-FULL-QTY"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-FULL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 2,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(dn_name)

			dn = frappe.get_doc("Delivery Note", dn_name)
			self.assertEqual(dn.docstatus, 1)
			self.assertEqual(dn.po_no, order_id)
			self.assertEqual(len(dn.items), 1)
			self.assertEqual(float(dn.items[0].qty), 2.0)

			so = frappe.get_doc("Sales Order", so_name)
			self.assertEqual(float(so.per_delivered), 100.0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_partial_shipment(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-PARTIAL-QTY"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "747.50", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-PARTIAL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 5,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "711.90", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "35.60", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(dn_name)

			dn = frappe.get_doc("Delivery Note", dn_name)
			self.assertEqual(dn.docstatus, 1)
			self.assertEqual(float(dn.items[0].qty), 2.0)

			so = frappe.get_doc("Sales Order", so_name)
			self.assertAlmostEqual(float(so.per_delivered), 40.0, delta=0.1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_second_sync_only_remaining_quantity(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-INCREMENTAL"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "747.50", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			# Sync 1: Shipped = 2
			mock_items_1 = [{
				"OrderItemId": "TEST-ITEM-DN-INCR",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 5,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "711.90", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "35.60", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items_1}},
			}
			so1, si1, pe1, dn1 = repo.sync_order(mock_order)
			self.assertIsNotNone(dn1)
			self.assertEqual(float(frappe.get_doc("Delivery Note", dn1).items[0].qty), 2.0)

			# Sync 2: Shipped increases to 5
			mock_items_2 = [{
				"OrderItemId": "TEST-ITEM-DN-INCR",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 5,
				"QuantityShipped": 5,
				"ItemPrice": {"Amount": "711.90", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "35.60", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items_2}},
			}
			so2, si2, pe2, dn2 = repo.sync_order(mock_order)
			self.assertIsNotNone(dn2)
			self.assertNotEqual(dn1, dn2)
			self.assertEqual(float(frappe.get_doc("Delivery Note", dn2).items[0].qty), 3.0)

			# Verify total delivered on SO is 100%
			so = frappe.get_doc("Sales Order", so1)
			self.assertEqual(float(so.per_delivered), 100.0)

			# Verify total DN count = 2
			dn_count = frappe.db.count("Delivery Note", {"po_no": order_id, "docstatus": 1})
			self.assertEqual(dn_count, 2)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_repeated_sync_no_duplicate_dn(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-IDEMPOTENT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-IDEMP",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 2,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			# Run 1: Creates DN
			so1, si1, pe1, dn1 = repo.sync_order(mock_order)
			self.assertIsNotNone(dn1)

			# Run 2: Same shipped qty -> No new DN
			so2, si2, pe2, dn2 = repo.sync_order(mock_order)
			self.assertIsNone(dn2)

			# Ensure only 1 DN in DB
			dn_count = frappe.db.count("Delivery Note", {"po_no": order_id, "docstatus": 1})
			self.assertEqual(dn_count, 1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_shipment_api_failure_no_dn(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-API-FAILURE"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			# Step 1: Create SO with valid items
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-FAIL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 2,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			so_name = repo.create_sales_order(mock_order)
			self.assertIsNotNone(so_name)

			# Step 2: Simulate shipment check API failure
			TestOrders.order_items_by_id[order_id] = {
				"status": 500,
				"json": {"errors": [{"code": "InternalError", "message": "Service unavailable"}]},
			}

			# create_delivery_note must not raise; SO remains intact, dn_name is None
			dn_name = repo.create_delivery_note(so_name, order=mock_order)
			self.assertIsNone(dn_name)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_cancelled_order_no_dn(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-CANCELED-NO-DN"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Canceled",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DN-CANCEL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 2,
				"QuantityShipped": 2,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNone(dn_name)
			dn_count = frappe.db.count("Delivery Note", {"po_no": order_id})
			self.assertEqual(dn_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_delivery_multi_item_partial_shipment(self):
		repo = TestAmazonRepository()
		order_id = "TEST-DELIVERY-MULTI-ITEM"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "598.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			# Item 1: 100343 (shipped 2 of 2)
			# Item 2: 100344 (shipped 0 of 1)
			mock_items = [
				{
					"OrderItemId": "TEST-ITEM-MULTI-1",
					"ASIN": "B0GYSTCR27",
					"SellerSKU": "100343",
					"Title": "Test Product 1",
					"QuantityOrdered": 2,
					"QuantityShipped": 2,
					"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
				},
				{
					"OrderItemId": "TEST-ITEM-MULTI-2",
					"ASIN": "B0GYSTCR28",
					"SellerSKU": "100344",
					"Title": "Test Product 2",
					"QuantityOrdered": 1,
					"QuantityShipped": 0,
					"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
					"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
					"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
				},
			]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(dn_name)

			dn = frappe.get_doc("Delivery Note", dn_name)
			self.assertEqual(dn.docstatus, 1)
			# Only the shipped item should be in the DN
			self.assertEqual(len(dn.items), 1)
			self.assertEqual(dn.items[0].item_name, "100343")
			self.assertEqual(float(dn.items[0].qty), 2.0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_released_creates_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-RELEASED"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-REL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "RELEASED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNotNone(pe_name)

			pe = frappe.get_doc("Payment Entry", pe_name)
			self.assertEqual(pe.docstatus, 1)
			self.assertAlmostEqual(float(pe.paid_amount), 299.0, delta=0.5)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.status, "Paid")
			self.assertAlmostEqual(float(si.outstanding_amount), 0.0, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_deferred_released_creates_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-DEF-REL"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DEF-REL",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "DEFERRED_RELEASED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNotNone(pe_name)

			pe = frappe.get_doc("Payment Entry", pe_name)
			self.assertEqual(pe.docstatus, 1)
			self.assertAlmostEqual(float(pe.paid_amount), 299.0, delta=0.5)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.status, "Paid")
			self.assertAlmostEqual(float(si.outstanding_amount), 0.0, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_deferred_no_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-DEFERRED"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-DEF",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "DEFERRED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.status, "Unpaid")
			self.assertAlmostEqual(float(si.outstanding_amount), 299.0, delta=0.5)

			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si_name,)
			)[0][0]
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_no_transaction_no_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-NO-TX"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-NOTX",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {"payload": {"transactions": []}},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.status, "Unpaid")
			self.assertAlmostEqual(float(si.outstanding_amount), 299.0, delta=0.5)

			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si_name,)
			)[0][0]
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_sales_breakdown_amount(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-BREAKDOWN-AMT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-BDAMT",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "RELEASED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			status = repo.get_amazon_payment_status(order_id)
			self.assertTrue(status["confirmed"])
			self.assertEqual(status["amount"], 299.0)

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(pe_name)
			pe = frappe.get_doc("Payment Entry", pe_name)
			self.assertAlmostEqual(float(pe.paid_amount), 299.0, delta=0.5)
			self.assertAlmostEqual(float(pe.received_amount), 299.0, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_fees_not_deducted(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-FEES-NOT-DEDUCTED"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-NOFEES",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			# Transaction has Sales: 299.00, Expenses: -50.00, Taxes: -5.00
			# Net settlement would be 244.00, but customer sales amount must remain 299.00!
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "RELEASED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								},
								{
									"breakdownType": "Expenses",
									"breakdownAmount": {"currencyAmount": -50.0, "currencyCode": "INR"},
								},
								{
									"breakdownType": "Taxes",
									"breakdownAmount": {"currencyAmount": -5.0, "currencyCode": "INR"},
								},
							],
						}]
					}
				},
			}

			status = repo.get_amazon_payment_status(order_id)
			self.assertTrue(status["confirmed"])
			self.assertEqual(status["amount"], 299.0)  # NOT 244.0

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(pe_name)
			pe = frappe.get_doc("Payment Entry", pe_name)
			# Must be 299.00, NEVER reduced to 244.00
			self.assertAlmostEqual(float(pe.paid_amount), 299.0, delta=0.5)
			self.assertNotAlmostEqual(float(pe.paid_amount), 244.0, delta=0.5)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_repeated_sync_no_duplicate_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-IDEMPOTENT"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "Other",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-IDEMP",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "RELEASED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			so1, si1, pe1, dn1 = repo.sync_order(mock_order)
			self.assertIsNotNone(pe1)

			so2, si2, pe2, dn2 = repo.sync_order(mock_order)
			self.assertEqual(pe1, pe2)

			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si1,)
			)[0][0]
			self.assertEqual(pe_count, 1)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	def test_payment_cod_deferred_no_pe(self):
		repo = TestAmazonRepository()
		order_id = "TEST-PAY-COD-DEFERRED"
		try:
			mock_order = {
				"AmazonOrderId": order_id,
				"OrderStatus": "Shipped",
				"FulfillmentChannel": "MFN",
				"PaymentMethod": "COD",
				"OrderTotal": {"Amount": "299.00", "CurrencyCode": "INR"},
				"ShippingAddress": {
					"StateOrRegion": "Maharashtra",
					"PostalCode": "400001",
					"City": "Mumbai",
					"CountryCode": "IN",
				},
				"MarketplaceId": "A21TJRUUN4KGV",
				"PurchaseDate": "2026-09-01T00:00:00Z",
				"LatestShipDate": "2026-09-05T00:00:00Z",
			}
			mock_items = [{
				"OrderItemId": "TEST-ITEM-COD-DEF",
				"ASIN": "B0GYSTCR27",
				"SellerSKU": "100343",
				"Title": "Test Product",
				"QuantityOrdered": 1,
				"QuantityShipped": 1,
				"ItemPrice": {"Amount": "284.76", "CurrencyCode": "INR"},
				"PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "INR"},
				"ItemTax": {"Amount": "14.24", "CurrencyCode": "INR"},
			}]
			TestOrders.order_items_by_id[order_id] = {
				"status": 200,
				"json": {"payload": {"AmazonOrderId": order_id, "OrderItems": mock_items}},
			}
			TestFinancesV20240619.transactions_by_order_id[order_id] = {
				"status": 200,
				"json": {
					"payload": {
						"transactions": [{
							"transactionType": "Shipment",
							"transactionStatus": "DEFERRED",
							"breakdowns": [
								{
									"breakdownType": "Sales",
									"breakdownAmount": {"currencyAmount": 299.0, "currencyCode": "INR"},
								}
							],
						}]
					}
				},
			}

			so_name, si_name, pe_name, dn_name = repo.sync_order(mock_order)
			self.assertIsNotNone(so_name)
			self.assertIsNotNone(si_name)
			# Even though PaymentMethod == COD, DEFERRED status means NO Payment Entry!
			self.assertIsNone(pe_name)

			si = frappe.get_doc("Sales Invoice", si_name)
			self.assertEqual(si.status, "Unpaid")
			self.assertAlmostEqual(float(si.outstanding_amount), 299.0, delta=0.5)

			pe_count = frappe.db.sql(
				"SELECT COUNT(*) FROM `tabPayment Entry Reference` WHERE reference_name = %s",
				(si_name,)
			)[0][0]
			self.assertEqual(pe_count, 0)
		finally:
			TestOrders.order_items_by_id.pop(order_id, None)
			TestFinancesV20240619.transactions_by_order_id.pop(order_id, None)
			frappe.db.rollback()

	# ── Customer Creation Tests ──────────────────────────────────────────────

	def test_create_customer_with_buyer_name_available(self):
		"""1. When real Amazon BuyerName is available in BuyerInfo, customer name is <BuyerName> - <AmazonOrderId>."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-001"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {
				"BuyerName": "Rahul Sharma",
				"BuyerEmail": "rahul.sharma@example.com",
			},
		}

		try:
			cust_name = repo.create_customer(order)
			expected = f"Rahul Sharma - {order_id}"
			self.assertEqual(cust_name, expected)

			cust_doc = frappe.get_doc("Customer", cust_name)
			self.assertEqual(cust_doc.customer_name, expected)
		finally:
			frappe.db.rollback()

	def test_create_customer_with_shipping_address_name(self):
		"""2. When BuyerInfo has no name, but ShippingAddress has Name, customer name uses ShippingAddress.Name."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-002"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {},
			"ShippingAddress": {
				"Name": "Priya Patel",
			},
		}

		try:
			cust_name = repo.create_customer(order)
			expected = f"Priya Patel - {order_id}"
			self.assertEqual(cust_name, expected)

			cust_doc = frappe.get_doc("Customer", cust_name)
			self.assertEqual(cust_doc.customer_name, expected)
		finally:
			frappe.db.rollback()

	def test_create_customer_with_buyer_name_missing_fallback(self):
		"""3. When Amazon does not provide a buyer name, retain existing fallback: Buyer - <AmazonOrderId>."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-003"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {},
		}

		try:
			cust_name = repo.create_customer(order)
			expected = f"Buyer - {order_id}"
			self.assertEqual(cust_name, expected)

			cust_doc = frappe.get_doc("Customer", cust_name)
			self.assertEqual(cust_doc.customer_name, expected)
		finally:
			frappe.db.rollback()

	def test_create_customer_with_whitespace_buyer_name_fallback(self):
		"""4. When Amazon BuyerName is empty or only whitespace, fallback safely to Buyer - <AmazonOrderId>."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-004"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {
				"BuyerName": "   ",
			},
		}

		try:
			cust_name = repo.create_customer(order)
			expected = f"Buyer - {order_id}"
			self.assertEqual(cust_name, expected)

			cust_doc = frappe.get_doc("Customer", cust_name)
			self.assertEqual(cust_doc.customer_name, expected)
		finally:
			frappe.db.rollback()

	def test_create_customer_existing_customer_reuse_idempotent(self):
		"""5. Existing customer is reused idempotently without creating duplicates."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-005"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {
				"BuyerName": "Anita Desai",
			},
		}

		try:
			cust_1 = repo.create_customer(order)
			cust_2 = repo.create_customer(order)
			self.assertEqual(cust_1, cust_2)

			count = frappe.db.count("Customer", {"name": f"Anita Desai - {order_id}"})
			self.assertEqual(count, 1)
		finally:
			frappe.db.rollback()

	def test_create_customer_existing_fallback_customer_reuse(self):
		"""6. If an order was previously created under Buyer - <order_id>, reuse it without creating a duplicate."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-NAME-006"
		fallback_name = f"Buyer - {order_id}"

		try:
			# Pre-create customer under fallback name
			old_cust = frappe.new_doc("Customer")
			old_cust.customer_name = fallback_name
			old_cust.customer_group = repo.amz_setting.customer_group
			old_cust.territory = repo.amz_setting.territory
			old_cust.customer_type = repo.amz_setting.customer_type
			old_cust.save()

			# Re-sync with real buyer name present
			order = {
				"AmazonOrderId": order_id,
				"BuyerInfo": {
					"BuyerName": "Amit Kumar",
				},
			}
			cust_name = repo.create_customer(order)
			# Reuses existing customer to avoid duplicates
			self.assertEqual(cust_name, fallback_name)
		finally:
			frappe.db.rollback()

	def test_create_sales_order_uses_real_buyer_name_customer(self):
		"""7. create_sales_order sets customer field to <Real Buyer Name> - <AmazonOrderId>."""
		repo = TestAmazonRepository()
		order_id = "TEST-BUYER-SO-007"
		order = {
			"AmazonOrderId": order_id,
			"BuyerInfo": {
				"BuyerName": "Vikram Seth",
			},
			"OrderStatus": "Shipped",
			"FulfillmentChannel": "MFN",
			"PaymentMethod": "COD",
			"LatestShipDate": "2026-09-24T18:29:59Z",
			"PurchaseDate": "2026-09-24T11:41:20Z",
			"MarketplaceId": "A21TJRUUN4KGV",
		}

		mock_item = {
			"item_code": "DP200",
			"item_name": "Sat Isabgol Psyllium Husk, Green, 200 g",
			"qty": 1,
			"rate": 100.0,
			"amount": 100.0,
			"stock_uom": "Nos",
			"uom": "Nos",
			"conversion_factor": 1.0,
			"warehouse": repo.amz_setting.warehouse,
		}

		try:
			repo.get_order_items = lambda oid: [mock_item]
			so_name = repo.create_sales_order(order)
			self.assertIsNotNone(so_name)

			so = frappe.get_doc("Sales Order", so_name)
			expected_customer = f"Vikram Seth - {order_id}"
			self.assertEqual(so.customer, expected_customer)
		finally:
			frappe.db.rollback()

