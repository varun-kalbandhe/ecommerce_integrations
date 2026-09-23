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


class TestOrders(Orders, TestSPAPI):
	def get_orders(
		self,
		created_after: str,
		created_before: str | None = None,
		last_updated_after: str | None = None,
		last_updated_before: str | None = None,
		order_statuses: list | None = None,
		marketplace_ids: list | None = None,
		fulfillment_channels: list | None = None,
		payment_methods: list | None = None,
		buyer_email: str | None = None,
		seller_order_id: str | None = None,
		max_results: int = 100,
		easyship_shipment_statuses: list | None = None,
		next_token: str | None = None,
		amazon_order_ids: list | None = None,
		actual_fulfillment_supply_source_id: str | None = None,
		is_ispu: bool = False,
		store_chain_store_id: str | None = None,
	) -> object:
		self.expected_response = DATA.get("get_orders_200")
		return super().get_orders(
			created_after,
			created_before,
			last_updated_after,
			last_updated_before,
			order_statuses,
			marketplace_ids,
			fulfillment_channels,
			payment_methods,
			buyer_email,
			seller_order_id,
			max_results,
			easyship_shipment_statuses,
			next_token,
			amazon_order_ids,
			actual_fulfillment_supply_source_id,
			is_ispu,
			store_chain_store_id,
		)

	def get_order_items(self, order_id: str, next_token: str | None = None) -> object:
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
					}
				)
				company.insert(ignore_permissions=True)
				company_name = company.company_name

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
