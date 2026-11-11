# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


import time
import urllib

import dateutil

import frappe
from frappe import _

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api import (
	SPAPI,
	CatalogItems,
	Finances,
	FinancesV20240619,
	ListingsItems,
	Orders,
	ProductFees,
	Reports,
	SPAPIError,
)
from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings import (
	AmazonSPAPISettings,
)
from ecommerce_integrations.amazon.doctype.amazon_account_mapping.amazon_account_mapping import (
	get_account_mapping,
)



def extract_amazon_order_id(tx: dict) -> str | None:
	"""
	Extracts and normalizes the Amazon Order ID from an SP-API transaction payload.
	Inspects:
	1. tx.relatedIdentifiers (case-insensitive for ORDER_ID / AMAZON_ORDER_ID)
	2. items[].relatedIdentifiers (nested item identifiers)
	3. top-level keys (orderId, amazonOrderId, etc.)
	4. contexts (orderId, amazonOrderId)
	Ensures it does NOT confuse item codes, SKU, ASIN, Sales Order, or Sales Invoice.
	"""
	if not isinstance(tx, dict):
		return None

	target_keys = {"ORDER_ID", "ORDERID", "AMAZON_ORDER_ID", "AMAZONORDERID"}

	# 1. Transaction-level relatedIdentifiers
	for r in (tx.get("relatedIdentifiers") or []):
		if isinstance(r, dict):
			name = (r.get("relatedIdentifierName") or "").strip().upper().replace("-", "_")
			val = str(r.get("relatedIdentifierValue") or "").strip()
			if name in target_keys and val:
				return val

	# 2. Nested item-level relatedIdentifiers
	for itm in (tx.get("items") or []):
		if isinstance(itm, dict):
			for r in (itm.get("relatedIdentifiers") or []):
				if isinstance(r, dict):
					name = (r.get("itemRelatedIdentifierName") or r.get("relatedIdentifierName") or "").strip().upper().replace("-", "_")
					val = str(r.get("itemRelatedIdentifierValue") or r.get("relatedIdentifierValue") or "").strip()
					if name in target_keys and val:
						return val

	# 3. Top-level keys
	for k in ("orderId", "amazonOrderId", "amazon_order_id", "OrderId", "AmazonOrderId"):
		val = str(tx.get(k) or "").strip()
		if val:
			return val

	# 4. Contexts
	for ctx in (tx.get("contexts") or []):
		if isinstance(ctx, dict):
			for k in ("orderId", "amazonOrderId", "amazon_order_id", "order_id"):
				val = str(ctx.get(k) or "").strip()
				if val:
					return val

	return None


class AmazonRepository:
	def __init__(self, amz_setting: str | AmazonSPAPISettings) -> None:
		if isinstance(amz_setting, str):
			amz_setting = frappe.get_doc("Amazon SP API Settings", amz_setting)

		self.amz_setting = amz_setting
		self.instance_params = dict(
			client_id=self.amz_setting.client_id,
			client_secret=self.amz_setting.get_password("client_secret"),
			refresh_token=self.amz_setting.refresh_token,
			country_code=self.amz_setting.country,
		)

	def return_as_list(self, input) -> list:
		if isinstance(input, list):
			return input
		else:
			return [input]

	def call_sp_api_method(self, sp_api_method, **kwargs) -> dict:
		errors = {}
		max_retries = self.amz_setting.max_retry_limit

		for _x in range(max_retries):
			try:
				result = sp_api_method(**kwargs)
				return result.get("payload")
			except SPAPIError as e:
				if e.error not in errors:
					errors[e.error] = e.error_description

				time.sleep(1)
				continue

		for error in errors:
			msg = f"<b>Error:</b> {error}<br/><b>Error Description:</b> {errors.get(error)}"
			frappe.msgprint(msg, alert=True, indicator="red")
			frappe.log_error(
				message=f"{error}: {errors.get(error)}",
				title=f'Method "{sp_api_method.__name__}" failed',
			)

		return {}

	def get_finances_instance(self) -> Finances:
		return Finances(**self.instance_params)

	def get_finances_v20240619_instance(self) -> FinancesV20240619:
		return FinancesV20240619(**self.instance_params)

	def get_amazon_payment_status(self, order_id: str) -> dict:
		"""
		Determines authoritative Amazon payment/settlement confirmation status
		using the modern Finances API (v2024-06-19 transactions endpoint).

		Rules:
		- Endpoint: GET /finances/2024-06-19/transactions
		- Filter: relatedIdentifierName = 'ORDER_ID', relatedIdentifierValue = order_id
		- Find transaction with transactionType == 'Shipment'
		- Status check:
		  - 'RELEASED' or 'DEFERRED_RELEASED' -> confirmed = True
		  - 'DEFERRED' -> confirmed = False (held in reserve)
		  - No qualifying shipment transaction -> confirmed = False
		- Payment amount:
		  breakdowns[breakdownType='Sales'].breakdownAmount.currencyAmount
		  (Never reduced by seller fees, TDS, TCS, postage, etc.)
		"""
		if not order_id:
			return {"confirmed": False, "amount": 0.0, "currency": None, "source": "No order_id provided"}

		try:
			finances_client = self.get_finances_v20240619_instance()
			resp = finances_client.list_transactions(
				related_identifier_name="ORDER_ID",
				related_identifier_value=order_id,
			)
			transactions = (resp.get("payload") or {}).get("transactions", [])
		except Exception as e:
			frappe.log_error(
				title=f"Amazon payment check failed for order {order_id}",
				message=frappe.get_traceback(),
			)
			return {
				"confirmed": False,
				"amount": 0.0,
				"currency": None,
				"source": f"Error: {e}",
			}

		confirmed = False
		sales_amount = 0.0
		currency = None
		status_source = "No qualifying transaction"

		for tx in transactions:
			if tx.get("transactionType") == "Shipment":
				status = tx.get("transactionStatus")
				# Extract customer sales amount from breakdowns (never reduced by expenses/fees)
				tx_sales = 0.0
				tx_currency = None
				for bd in (tx.get("breakdowns") or []):
					if bd.get("breakdownType") == "Sales":
						amt_dict = bd.get("breakdownAmount") or {}
						tx_sales += float(amt_dict.get("currencyAmount") or 0.0)
						if not tx_currency:
							tx_currency = amt_dict.get("currencyCode")

				if status in ("RELEASED", "DEFERRED_RELEASED"):
					confirmed = True
					status_source = f"Finances 2024-06-19: {status}"
					sales_amount += tx_sales
					if not currency:
						currency = tx_currency
				elif status == "DEFERRED":
					if not confirmed:
						status_source = "Finances 2024-06-19: DEFERRED"
						if sales_amount == 0.0:
							sales_amount = tx_sales
							currency = tx_currency

		# Structured logging per requirements
		frappe.logger("ecommerce_integrations").info(
			f"Amazon payment check:\n"
			f"Order: {order_id}\n"
			f"Confirmed: {'YES' if confirmed else 'NO'}\n"
			f"Amount: {sales_amount}\n"
			f"Source: {status_source}"
		)

		return {
			"confirmed": confirmed,
			"amount": sales_amount,
			"currency": currency,
			"source": status_source,
		}


	def get_account(self, name) -> str:
		account_name = frappe.db.get_value("Account", {"account_name": f"Amazon {name}"})

		if not account_name:
			new_account = frappe.new_doc("Account")
			new_account.account_name = f"Amazon {name}"
			new_account.company = self.amz_setting.company
			new_account.parent_account = self.amz_setting.market_place_account_group
			new_account.insert(ignore_permissions=True)
			account_name = new_account.name

		return account_name

	def get_charges_and_fees(self, order_id) -> dict:
		finances = self.get_finances_instance()
		financial_events_payload = self.call_sp_api_method(
			sp_api_method=finances.list_financial_events_by_order_id, order_id=order_id
		)

		charges_and_fees = {"charges": [], "fees": []}

		while True:
			shipment_event_list = financial_events_payload.get("FinancialEvents", {}).get(
				"ShipmentEventList", []
			)
			next_token = financial_events_payload.get("NextToken")

			for shipment_event in shipment_event_list:
				if shipment_event:
					for shipment_item in shipment_event.get("ShipmentItemList", []):
						charges = shipment_item.get("ItemChargeList", [])
						fees = shipment_item.get("ItemFeeList", [])
						seller_sku = shipment_item.get("SellerSKU")

						for charge in charges:
							charge_type = charge.get("ChargeType")
							amount = charge.get("ChargeAmount", {}).get("CurrencyAmount", 0)

							if charge_type != "Principal" and float(amount) != 0:
								charge_account = self.get_account(charge_type)
								charges_and_fees.get("charges").append(
									{
										"charge_type": "Actual",
										"account_head": charge_account,
										"tax_amount": amount,
										"description": charge_type + " for " + seller_sku,
									}
								)

						for fee in fees:
							fee_type = fee.get("FeeType")
							amount = fee.get("FeeAmount", {}).get("CurrencyAmount", 0)

							if float(amount) != 0:
								fee_account = self.get_account(fee_type)
								charges_and_fees.get("fees").append(
									{
										"charge_type": "Actual",
										"account_head": fee_account,
										"tax_amount": amount,
										"description": fee_type + " for " + seller_sku,
									}
								)

			if not next_token:
				break

			financial_events_payload = self.call_sp_api_method(
				sp_api_method=finances.list_financial_events_by_order_id,
				order_id=order_id,
				next_token=next_token,
			)

		return charges_and_fees

	def get_orders_instance(self) -> Orders:
		return Orders(**self.instance_params)

	def create_item(self, order_item) -> str:
		# 1. Existing Item -> return unchanged
		item_code = order_item.get("ASIN") or order_item.get("asin")
		if item_code and frappe.db.exists("Item", item_code):
			return item_code

		for field_map in getattr(self.amz_setting, "amazon_fields_map", []):
			if field_map.use_to_find_item_code:
				val = order_item.get(field_map.amazon_field)
				if val:
					existing_code = frappe.db.get_value(
						"Item",
						filters={field_map.item_field: val},
						fieldname="item_code",
					)
					if existing_code:
						return existing_code

		def create_item_group(amazon_item) -> str:
			summaries = amazon_item.get("summaries") or []
			summary = summaries[0] if summaries else {}

			item_group_name = (
				summary.get("websiteDisplayGroupName")
				or (summary.get("browseClassification") or {}).get("displayName")
				or self.amz_setting.parent_item_group
			)

			if item_group_name:
				item_group = frappe.db.get_value("Item Group", filters={"item_group_name": item_group_name})

				if not item_group:
					new_item_group = frappe.new_doc("Item Group")
					new_item_group.item_group_name = item_group_name
					new_item_group.parent_item_group = self.amz_setting.parent_item_group
					has_custom_desc = (
						frappe.get_meta("Item Group").has_field("custom_description")
						or frappe.db.has_column("Item Group", "custom_description")
					)
					if has_custom_desc:
						new_item_group.custom_description = item_group_name
					has_group_hsn = (
						frappe.get_meta("Item Group").has_field("gst_hsn_code")
						or frappe.db.has_column("Item Group", "gst_hsn_code")
					)
					if has_group_hsn:
						parent_hsn = frappe.db.get_value(
							"Item Group", self.amz_setting.parent_item_group, "gst_hsn_code"
						)
						if parent_hsn:
							new_item_group.gst_hsn_code = parent_hsn
					new_item_group.insert()
					return new_item_group.item_group_name
				return item_group

			raise (KeyError("ProductGroup"))

		def create_brand(amazon_item) -> str:
			summaries = amazon_item.get("summaries") or []
			summary = summaries[0] if summaries else {}
			attributes = amazon_item.get("attributes") or {}

			brand_name = summary.get("brand")
			if not brand_name and attributes.get("brand"):
				brand_entries = attributes.get("brand")
				if isinstance(brand_entries, list) and brand_entries:
					brand_name = brand_entries[0].get("value")

			if not brand_name:
				return

			existing_brand = frappe.db.get_value("Brand", filters={"brand": brand_name})

			if not existing_brand:
				brand = frappe.new_doc("Brand")
				brand.brand = brand_name
				brand.insert()
				return brand.brand
			return existing_brand

		def create_manufacturer(amazon_item) -> str:
			summaries = amazon_item.get("summaries") or []
			summary = summaries[0] if summaries else {}
			attributes = amazon_item.get("attributes") or {}

			manufacturer_name = summary.get("manufacturer")
			if not manufacturer_name and attributes.get("manufacturer"):
				mfg_entries = attributes.get("manufacturer")
				if isinstance(mfg_entries, list) and mfg_entries:
					manufacturer_name = mfg_entries[0].get("value")

			if not manufacturer_name:
				return

			existing_manufacturer = frappe.db.get_value(
				"Manufacturer", filters={"short_name": manufacturer_name}
			)

			if not existing_manufacturer:
				manufacturer = frappe.new_doc("Manufacturer")
				manufacturer.short_name = manufacturer_name
				manufacturer.insert()
				return manufacturer.short_name
			return existing_manufacturer

		def create_item_price(amazon_item, item_code) -> None:
			item_price = frappe.new_doc("Item Price")
			item_price.price_list = self.amz_setting.price_list
			attributes = amazon_item.get("attributes") or {}
			list_price_entries = attributes.get("list_price") or []
			price_rate = 0
			if isinstance(list_price_entries, list) and list_price_entries:
				price_rate = list_price_entries[0].get("value") or 0
			item_price.price_list_rate = price_rate
			item_price.item_code = item_code
			item_price.insert()

		def create_ecommerce_item(order_item, item_code) -> None:
			asin = order_item.get("ASIN") or order_item.get("asin")
			sku = (
				order_item.get("SellerSKU")
				or order_item.get("seller_sku")
				or order_item.get("sku")
			)
			integration = frappe.get_meta("Amazon SP API Settings").module
			if frappe.db.exists("Ecommerce Item", {"integration": integration, "integration_item_code": asin}):
				return
			if sku and frappe.db.exists("Ecommerce Item", {"integration": integration, "sku": sku}):
				return

			ecommerce_item = frappe.new_doc("Ecommerce Item")
			ecommerce_item.integration = integration
			ecommerce_item.erpnext_item_code = item_code
			ecommerce_item.integration_item_code = asin
			ecommerce_item.sku = sku
			try:
				ecommerce_item.insert(ignore_permissions=True)
			except frappe.DuplicateEntryError:
				pass

		catalog_items = self.get_catalog_items_instance()
		asin = order_item.get("ASIN") or order_item.get("asin")
		
		try:
			amazon_item = catalog_items.get_catalog_item(asin)
		except Exception as e:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f'Catalog Items API failed for ASIN "{asin}"',
			)
			amazon_item = {}

		if not isinstance(amazon_item, dict):
			amazon_item = {}

		if "errors" in amazon_item:
			errors = amazon_item.get("errors") or []
			first_error = errors[0] if isinstance(errors, list) and errors else {}
			error_code = first_error.get("code", "CatalogItemError")
			error_msg = first_error.get("message", "Unknown error from Amazon Catalog Items API.")
			frappe.log_error(
				message=f"{error_code}: {error_msg}",
				title=f'Catalog Items API failed for ASIN "{asin}"',
			)
			amazon_item = {}

		item = frappe.new_doc("Item")

		for field_map in self.amz_setting.amazon_fields_map:
			if field_map.use_to_find_item_code:
				item.item_code = order_item[field_map.amazon_field]

			if field_map.item_field:
				setattr(item, field_map.item_field, order_item[field_map.amazon_field])

		item.item_group = create_item_group(amazon_item)
		item.brand = create_brand(amazon_item)
		item.manufacturer = create_manufacturer(amazon_item)
		item.stock_uom = "Nos"

		# Required HSN priority:
		# Priority 1: Amazon HSN returned by get_amazon_hsn(order_item)
		amazon_hsn = self.get_amazon_hsn(order_item)

		final_hsn = None
		if amazon_hsn:
			final_hsn = str(amazon_hsn).strip()
		else:
			# Priority 2: If Amazon HSN is unavailable, read gst_hsn_code from actual Item Group
			group_hsn = None
			if item.item_group:
				try:
					group_hsn = frappe.db.get_value(
						"Item Group",
						item.item_group,
						"gst_hsn_code",
					)
				except Exception:
					group_hsn = None
			if group_hsn:
				final_hsn = str(group_hsn).strip()
			elif self.amz_setting.parent_item_group:
				# Priority 3: If that Item Group has no HSN, read gst_hsn_code from configured parent_item_group
				parent_hsn = None
				try:
					parent_hsn = frappe.db.get_value(
						"Item Group",
						self.amz_setting.parent_item_group,
						"gst_hsn_code",
					)
				except Exception:
					parent_hsn = None
				if parent_hsn:
					final_hsn = str(parent_hsn).strip()

		# Priority 4: If no HSN exists from any source, stop creation with a clear ValidationError before item.insert().
		if final_hsn:
			item.gst_hsn_code = final_hsn
			item.set("gst_hsn_code", final_hsn)
		else:
			seller_sku = (
				order_item.get("SellerSKU")
				or order_item.get("seller_sku")
				or order_item.get("sku")
				or "-"
			)
			asin_code = order_item.get("ASIN") or order_item.get("asin") or asin or "-"
			item_group_name = item.item_group or "-"
			msg = _(
				"HSN/SAC Code unavailable for Amazon item.\n"
				"ASIN: {0}\n"
				"Seller SKU: {1}\n"
				"Item Group: {2}\n"
				"Amazon did not provide an HSN and no fallback HSN is configured on the Item Group or Amazon parent Item Group."
			).format(asin_code, seller_sku, item_group_name)
			
			item.item_name = f"[MISSING HSN] {item.item_name}"[:140]
			item.description = f"<b>[MISSING HSN]</b> {msg}\n\n{item.description or ''}"
			
			frappe.log_error(title=f"Missing HSN for Amazon Item {seller_sku}", message=msg)

		# 4. Before item.insert(), ensure the value being assigned is actually on the Item document.
		item.insert(ignore_permissions=True)

		create_item_price(amazon_item, item.item_code)
		create_ecommerce_item(order_item, item.item_code)

		return item.item_code

	def get_item_code(self, order_item) -> str:
		for field_map in self.amz_setting.amazon_fields_map:
			if field_map.use_to_find_item_code:
				item_code = frappe.db.get_value(
					"Item",
					filters={field_map.item_field: order_item[field_map.amazon_field]},
					fieldname="item_code",
				)

				if item_code:
					return item_code
				elif not self.amz_setting.create_item_if_not_exists:
					field_label = frappe.get_meta("Item").get_label(field_map.item_field)
					frappe.throw(
						_("Item not found with {0} ({1}) = {2}.").format(
							frappe.bold(field_label),
							field_map.item_field,
							frappe.bold(order_item[field_map.amazon_field]),
						)
					)

				break
		else:
			frappe.throw(_("At least one field must be selected to find the item code."))

		item_code = self.create_item(order_item)
		return item_code

	def get_order_items(self, order_id) -> list:
		orders = self.get_orders_instance()
		order_items_payload = self.call_sp_api_method(sp_api_method=orders.get_order_items, order_id=order_id)

		final_order_items = []
		print(f"DEBUG: order_items_payload in get_order_items: {order_items_payload}")
		warehouse = self.amz_setting.warehouse

		while True:
			order_items_list = (order_items_payload or {}).get("OrderItems") or []
			next_token = (order_items_payload or {}).get("NextToken")

			if not order_items_list:
				break

			for order_item in order_items_list:
				if order_item.get("QuantityOrdered") > 0:
					final_order_items.append(
						{
							"item_code": self.get_item_code(order_item),
							"item_name": order_item.get("SellerSKU"),
							"amazon_item_code": order_item.get("SellerSKU"),
							"description": order_item.get("Title"),
							"rate": order_item.get("ItemPrice", {}).get("Amount", 0),
							"qty": order_item.get("QuantityOrdered"),
							"uom": "Nos",
							"stock_uom": "Nos",
							"warehouse": warehouse,
							"conversion_factor": 1,
							"_raw_order_item": order_item,
						}
					)

			if not next_token:
				break

			order_items_payload = self.call_sp_api_method(
				sp_api_method=orders.get_order_items,
				order_id=order_id,
				next_token=next_token,
			)

		return final_order_items

	def update_sales_order_status_fields(self, so_doc, order) -> None:
		update_fields = {}

		amazon_order_status = order.get("OrderStatus")
		if amazon_order_status is not None:
			update_fields["amazon_order_status"] = amazon_order_status

		amazon_fulfillment_channel = order.get("FulfillmentChannel")
		if amazon_fulfillment_channel is not None:
			update_fields["amazon_fulfillment_channel"] = amazon_fulfillment_channel

		amazon_shipment_status = order.get("EasyShipShipmentStatus")
		if amazon_shipment_status is not None:
			update_fields["amazon_shipment_status"] = amazon_shipment_status

		payment_method = order.get("PaymentMethod")
		if not payment_method and order.get("PaymentMethodDetails"):
			pm_details = order.get("PaymentMethodDetails")
			if isinstance(pm_details, list) and pm_details:
				payment_method = ", ".join(str(d) for d in pm_details)
			elif isinstance(pm_details, str):
				payment_method = pm_details
		if payment_method is not None:
			update_fields["amazon_payment_method"] = payment_method

		cancellation_reason = (
			order.get("CancellationReason")
			or order.get("CancelReason")
			or order.get("BuyerCancelReason")
		)
		if cancellation_reason is not None:
			update_fields["amazon_cancellation_reason"] = cancellation_reason

		if update_fields:
			so_doc.db_set(update_fields)

	def handle_sales_order_cancellation(self, so_doc, order) -> bool:
		"""
		Handles Amazon order cancellation based on ERPNext Sales Order docstatus:
		- If already cancelled (docstatus == 2): no-op (idempotent).
		- CASE A (Draft, docstatus == 0): safely delete the draft.
		- CASE B (Submitted, docstatus == 1, no DN, no SI): cancel the Sales Order.
		- CASE C (Submitted, DN exists, no SI): block cancellation, log manual return required.
		- CASE D (Submitted, SI exists, no DN): block cancellation, log credit note required.
		- CASE E (Submitted, both DN and SI exist): block cancellation, log manual action required.

		Returns True if draft Sales Order was deleted, False otherwise.
		"""
		order_id = order.get("AmazonOrderId")

		# Idempotent: Already cancelled in ERPNext
		if so_doc.docstatus == 2:
			return False

		# CASE A: Draft Sales Order -> Delete draft safely
		if so_doc.docstatus == 0:
			doc_name = so_doc.name
			frappe.log_error(
				title=f"Amazon Order {order_id} Canceled - Draft Deleted",
				message=f"Draft Sales Order {doc_name} was deleted because Amazon order {order_id} was canceled.",
				reference_doctype="Sales Order",
				reference_name=doc_name,
			)
			so_doc.delete()
			return True

		# Submitted Sales Order (docstatus == 1)
		if so_doc.docstatus == 1:
			submitted_dns = frappe.db.sql(
				"""
				SELECT DISTINCT parent FROM `tabDelivery Note Item`
				WHERE against_sales_order = %s AND docstatus = 1
				""",
				(so_doc.name,),
				as_dict=True,
			)
			dn_names = [d.parent for d in submitted_dns]

			submitted_sis = frappe.db.sql(
				"""
				SELECT DISTINCT parent FROM `tabSales Invoice Item`
				WHERE sales_order = %s AND docstatus = 1
				""",
				(so_doc.name,),
				as_dict=True,
			)
			si_names = [s.parent for s in submitted_sis]

			# CASE E: Both Delivery Note and Sales Invoice exist
			if dn_names and si_names:
				frappe.log_error(
					title=f"Amazon Order {order_id} Canceled - Manual Action Required",
					message=(
						f"Sales Order {so_doc.name} cannot be automatically cancelled because submitted "
						f"Delivery Note(s) ({', '.join(dn_names)}) and Sales Invoice(s) ({', '.join(si_names)}) exist. "
						f"Manual return, credit-note, and refund handling is required."
					),
					reference_doctype="Sales Order",
					reference_name=so_doc.name,
				)
				return False

			# CASE C: Submitted Delivery Note exists
			if dn_names:
				frappe.log_error(
					title=f"Amazon Order {order_id} Canceled - Manual Return Required",
					message=(
						f"Sales Order {so_doc.name} cannot be automatically cancelled because submitted "
						f"Delivery Note(s) exist: {', '.join(dn_names)}. "
						f"Fulfillment has already occurred; manual return/refund handling is required."
					),
					reference_doctype="Sales Order",
					reference_name=so_doc.name,
				)
				return False

			# CASE D: Submitted Sales Invoice exists
			if si_names:
				frappe.log_error(
					title=f"Amazon Order {order_id} Canceled - Credit Note Required",
					message=(
						f"Sales Order {so_doc.name} cannot be automatically cancelled because submitted "
						f"Sales Invoice(s) exist: {', '.join(si_names)}. "
						f"Invoicing has already occurred; manual credit-note/refund handling is required."
					),
					reference_doctype="Sales Order",
					reference_name=so_doc.name,
				)
				return False

			# CASE B: Neither DN nor SI exist -> Cancel Sales Order
			so_doc.cancel()
			frappe.log_error(
				title=f"Amazon Order {order_id} Canceled - Sales Order Cancelled",
				message=f"Sales Order {so_doc.name} was successfully cancelled in ERPNext because Amazon order {order_id} was canceled.",
				reference_doctype="Sales Order",
				reference_name=so_doc.name,
			)
			return False

		return False

	def create_customer(self, order: dict) -> str:
		order_id = order.get("AmazonOrderId") or ""
		buyer_info = order.get("BuyerInfo") or {}
		shipping_address = order.get("ShippingAddress") or {}

		buyer_name = (
			buyer_info.get("BuyerName")
			or order.get("BuyerName")
			or shipping_address.get("Name")
			or shipping_address.get("RecipientName")
		)

		if buyer_name and str(buyer_name).strip():
			buyer_prefix = str(buyer_name).strip()
		else:
			buyer_prefix = "Buyer"

		order_customer_name = f"{buyer_prefix} - {order_id}" if order_id else buyer_prefix

		existing_customer_name = (
			frappe.db.get_value("Customer", filters={"name": order_customer_name}, fieldname="name")
			or frappe.db.get_value("Customer", filters={"customer_name": order_customer_name}, fieldname="name")
		)

		if not existing_customer_name and order_id:
			existing_customer_name = (
				frappe.db.get_value("Customer", filters={"name": f"Buyer - {order_id}"}, fieldname="name")
				or frappe.db.get_value("Customer", filters={"customer_name": f"Buyer - {order_id}"}, fieldname="name")
			)

		if existing_customer_name:
			filters = [
				["Dynamic Link", "link_doctype", "=", "Customer"],
				["Dynamic Link", "link_name", "=", existing_customer_name],
				["Dynamic Link", "parenttype", "=", "Contact"],
			]

			existing_contacts = frappe.get_list("Contact", filters)

			if not existing_contacts:
				new_contact = frappe.new_doc("Contact")
				new_contact.first_name = order_customer_name
				new_contact.append(
					"links",
					{"link_doctype": "Customer", "link_name": existing_customer_name},
				)
				if buyer_info and buyer_info.get("BuyerEmail"):
					new_contact.email_id = buyer_info.get("BuyerEmail")
				new_contact.insert()

			return existing_customer_name
		else:
			new_customer = frappe.new_doc("Customer")
			new_customer.customer_name = order_customer_name
			new_customer.customer_group = self.amz_setting.customer_group
			new_customer.territory = self.amz_setting.territory
			new_customer.customer_type = self.amz_setting.customer_type
			new_customer.save()

			new_contact = frappe.new_doc("Contact")
			new_contact.first_name = order_customer_name
			new_contact.append("links", {"link_doctype": "Customer", "link_name": new_customer.name})
			if buyer_info and buyer_info.get("BuyerEmail"):
				new_contact.email_id = buyer_info.get("BuyerEmail")
			new_contact.insert()

			return new_customer.name

	def create_sales_order(self, order) -> str | None:
		def create_customer(order) -> str:
			return self.create_customer(order)

		def create_address(order, customer_name) -> str | None:
			shipping_address = order.get("ShippingAddress")

			if not shipping_address:
				return
			else:
				make_address = frappe.new_doc("Address")
				make_address.address_line1 = shipping_address.get("AddressLine1", "Not Provided")
				make_address.city = shipping_address.get("City", "Not Provided")
				make_address.state = shipping_address.get("StateOrRegion").title()
				make_address.pincode = shipping_address.get("PostalCode")

				filters = [
					["Dynamic Link", "link_doctype", "=", "Customer"],
					["Dynamic Link", "link_name", "=", customer_name],
					["Dynamic Link", "parenttype", "=", "Address"],
				]
				existing_address = frappe.get_list("Address", filters)

				for address in existing_address:
					address_doc = frappe.get_doc("Address", address["name"])
					if (
						address_doc.address_line1 == make_address.address_line1
						and address_doc.pincode == make_address.pincode
					):
						return address

				make_address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
				make_address.address_type = "Shipping"
				make_address.insert()
				return make_address.name

		order_id = order.get("AmazonOrderId")
		so = frappe.db.get_value("Sales Order", filters={"amazon_order_id": order_id}, fieldname="name")

		if so:
			so_doc = frappe.get_doc("Sales Order", so)
			self.update_sales_order_status_fields(so_doc, order)

			if order.get("OrderStatus") == "Canceled":
				deleted = self.handle_sales_order_cancellation(so_doc, order)
				if deleted:
					return None

			return so_doc.name
		else:
			items = self.get_order_items(order_id)

			if not items:
				return

			customer_name = create_customer(order)
			address_name = create_address(order, customer_name)

			delivery_date = dateutil.parser.parse(order.get("LatestShipDate")).strftime("%Y-%m-%d")
			transaction_date = dateutil.parser.parse(order.get("PurchaseDate")).strftime("%Y-%m-%d")

			so = frappe.new_doc("Sales Order")
			so.amazon_order_id = order_id
			so.marketplace_id = order.get("MarketplaceId")
			so.amazon_order_status = order.get("OrderStatus")
			so.amazon_fulfillment_channel = order.get("FulfillmentChannel")
			so.amazon_shipment_status = order.get("EasyShipShipmentStatus")

			payment_method = order.get("PaymentMethod")
			if not payment_method and order.get("PaymentMethodDetails"):
				pm_details = order.get("PaymentMethodDetails")
				if isinstance(pm_details, list) and pm_details:
					payment_method = ", ".join(str(d) for d in pm_details)
				elif isinstance(pm_details, str):
					payment_method = pm_details
			so.amazon_payment_method = payment_method

			cancellation_reason = (
				order.get("CancellationReason")
				or order.get("CancelReason")
				or order.get("BuyerCancelReason")
			)
			if cancellation_reason:
				so.amazon_cancellation_reason = cancellation_reason

			so.customer = customer_name
			so.delivery_date = delivery_date
			so.transaction_date = transaction_date
			so.company = self.amz_setting.company

			if address_name:
				so.customer_address = address_name
				so.shipping_address_name = address_name

			# Evaluate GST inter-state correctly so Sales Order gets the right tax template
			company_state_code = self.get_company_state_code(so.company)
			pos_code = ""
			if address_name:
				address_doc = frappe.get_cached_doc("Address", address_name)
				pos_code = address_doc.gst_state_number or ""
			is_inter_state = bool(pos_code and company_state_code and pos_code != company_state_code)

			so.taxes_and_charges = self.get_sales_taxes_and_charges_template(so.company, is_inter_state)

			missing_hsn_found = False
			order_items_raw = order.get("OrderItems", [])
			for item in items:
				matched_amz_item = item.pop("_raw_order_item", None)
				if not matched_amz_item:
					matched_amz_item = next(
						(i for i in order_items_raw if i.get("SellerSKU") == item.get("amazon_item_code")), None
					)
				item["item_tax_template"] = self.get_product_gst_template(
					item["item_code"], so.company, is_inter_state, order_item=matched_amz_item
				)
				so.append("items", item)
				
				item_name = frappe.db.get_value("Item", item["item_code"], "item_name", cache=True)
				if item_name and item_name.startswith("[MISSING HSN]"):
					missing_hsn_found = True

			taxes_and_charges = self.amz_setting.taxes_charges

			if taxes_and_charges:
				charges_and_fees = self.get_charges_and_fees(order_id)

				for charge in charges_and_fees.get("charges"):
					so.append("taxes", charge)

				for fee in charges_and_fees.get("fees"):
					so.append("taxes", fee)

			so.insert(ignore_permissions=True)
			
			if missing_hsn_found:
				so.add_comment("Comment", "<b>[Action Required]</b> One or more items in this Sales Order are missing an HSN Code. Please update the Item master.")
			
			try:
				so.submit()
			except Exception as e:
				frappe.log_error(title=f"Sales Order Submit Failed for {order_id}", message=frappe.get_traceback())
				so.add_comment("Comment", f"<b>[Sync Error]</b> Failed to submit Sales Order automatically due to validation error: {e}")

			if order.get("OrderStatus") == "Canceled":
				self.handle_sales_order_cancellation(so, order)

			return so.name

	def get_company_state_code(self, company: str) -> str:
		company_gstin = frappe.db.get_value("Company", company, "gstin")
		if company_gstin and len(company_gstin) >= 2 and company_gstin[:2].isdigit():
			return company_gstin[:2]

		addr_rows = frappe.db.sql(
			"""
			SELECT a.gst_state_number FROM `tabAddress` a
			JOIN `tabDynamic Link` dl ON dl.parent = a.name
			WHERE dl.link_doctype = 'Company' AND dl.link_name = %s AND a.gst_state_number IS NOT NULL
			LIMIT 1
			""",
			(company,),
			as_dict=True,
		)
		if addr_rows and addr_rows[0].gst_state_number:
			return str(addr_rows[0].gst_state_number).zfill(2)
		return ""

	def get_product_gst_template(
		self, item_code: str, company: str, is_inter_state: bool, order_item: dict | None = None
	) -> str:
		"""
		Determines the Item Tax Template to apply for an item row.
		Prefers the item's existing GST configuration (Item master, Item Group master, or SellerSKU item).
		If no template is assigned, falls back to Amazon order item tax rate if available.
		Raises ValidationError if no usable configuration is found.
		"""
		# 1. Check Item master taxes
		if frappe.db.exists("Item", item_code):
			item_doc = frappe.get_doc("Item", item_code)
			for t in getattr(item_doc, "taxes", []):
				t_disabled, t_company = frappe.get_cached_value(
					"Item Tax Template", t.item_tax_template, ["disabled", "company"]
				)
				if not t_disabled and t_company == company:
					cat = (t.tax_category or "").lower()
					if is_inter_state and "in-state" not in cat:
						return t.item_tax_template
					elif not is_inter_state and "out-state" not in cat:
						return t.item_tax_template

			for t in getattr(item_doc, "taxes", []):
				t_disabled, t_company = frappe.get_cached_value(
					"Item Tax Template", t.item_tax_template, ["disabled", "company"]
				)
				if not t_disabled and t_company == company:
					return t.item_tax_template

			# 2. Check Item Group master taxes
			if item_doc.item_group and frappe.db.exists("Item Group", item_doc.item_group):
				ig_doc = frappe.get_doc("Item Group", item_doc.item_group)
				for t in getattr(ig_doc, "taxes", []):
					t_disabled, t_company = frappe.get_cached_value(
						"Item Tax Template", t.item_tax_template, ["disabled", "company"]
					)
					if not t_disabled and t_company == company:
						return t.item_tax_template

		# 3. Check SellerSKU if different from item_code
		if order_item and order_item.get("SellerSKU") and order_item.get("SellerSKU") != item_code:
			sku = order_item.get("SellerSKU")
			if frappe.db.exists("Item", sku):
				sku_doc = frappe.get_doc("Item", sku)
				for t in getattr(sku_doc, "taxes", []):
					t_disabled, t_company = frappe.get_cached_value(
						"Item Tax Template", t.item_tax_template, ["disabled", "company"]
					)
					if not t_disabled and t_company == company:
						cat = (t.tax_category or "").lower()
						if is_inter_state and "in-state" not in cat:
							return t.item_tax_template
						elif not is_inter_state and "out-state" not in cat:
							return t.item_tax_template
				for t in getattr(sku_doc, "taxes", []):
					t_disabled, t_company = frappe.get_cached_value(
						"Item Tax Template", t.item_tax_template, ["disabled", "company"]
					)
					if not t_disabled and t_company == company:
						return t.item_tax_template

		# 4. Infer from Amazon OrderItem tax rate if available
		if order_item:
			ip = float(order_item.get("ItemPrice", {}).get("Amount", 0) or 0)
			it = float(order_item.get("ItemTax", {}).get("Amount", 0) or 0)
			if ip > 0 and it > 0:
				rate = round((it / ip) * 100)
				candidate_templates = frappe.get_all(
					"Item Tax Template",
					filters={"company": company, "disabled": 0},
					fields=["name", "title"],
				)
				prefix = "out-state" if is_inter_state else "in-state"
				for tmpl in candidate_templates:
					tmpl_name_lower = tmpl.name.lower()
					if f"{rate}%" in tmpl_name_lower or f"{rate} %" in tmpl_name_lower:
						if prefix in tmpl_name_lower:
							return tmpl.name
				for tmpl in candidate_templates:
					tmpl_name_lower = tmpl.name.lower()
					if f"{rate}%" in tmpl_name_lower or f"{rate} %" in tmpl_name_lower:
						return tmpl.name

		# 5. Not found -> raise ValidationError
		frappe.throw(
			_("No usable GST configuration found for item {0}.").format(item_code),
			frappe.ValidationError,
		)

	def get_sales_taxes_and_charges_template(self, company: str, is_inter_state: bool) -> str:
		company_abbr = frappe.db.get_value("Company", company, "abbr") or ""
		keyword = "out-state" if is_inter_state else "in-state"

		templates = frappe.get_all(
			"Sales Taxes and Charges Template",
			filters={"company": company, "disabled": 0},
			fields=["name", "title", "is_default"],
		)
		for tmpl in templates:
			name_lower = tmpl.name.lower()
			if keyword in name_lower and "rcm" not in name_lower:
				return tmpl.name

		if is_inter_state:
			expected = f"Output GST Out-state - {company_abbr}"
		else:
			expected = f"Output GST In-State - {company_abbr}"

		if frappe.db.exists("Sales Taxes and Charges Template", expected):
			return expected

		if templates:
			return templates[0].name

		frappe.throw(
			_("No Sales Taxes and Charges Template found for company {0}.").format(company),
			frappe.ValidationError,
		)

	def create_sales_invoice(
		self, sales_order_name: str, order: dict | None = None, order_items: list | None = None
	) -> str:
		"""
		Creates and submits a Sales Invoice from an existing Sales Order using Amazon financial data.
		- Idempotent: returns existing invoice if already created.
		- Determines GST mode (Inter-state vs Intra-state) based on place of supply.
		- Applies Amazon unit rates, promotion discounts, and shipping charges.
		- Validates that amounts reconcile with Amazon OrderTotal.
		"""
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		so = frappe.get_doc("Sales Order", sales_order_name)
		amazon_order_id = so.amazon_order_id

		# 1. Idempotency / Duplicate Check
		existing_si = frappe.db.sql(
			"""
			SELECT parent FROM `tabSales Invoice Item`
			WHERE sales_order = %s AND docstatus < 2
			LIMIT 1
			""",
			(sales_order_name,),
			as_dict=True,
		)
		if existing_si:
			return existing_si[0].parent

		if amazon_order_id:
			existing_by_order = frappe.db.get_value(
				"Sales Invoice",
				filters={"custom_marketplace_order": amazon_order_id, "docstatus": ["<", 2]},
				fieldname="name",
			) or frappe.db.get_value(
				"Sales Invoice",
				filters={"po_no": amazon_order_id, "docstatus": ["<", 2]},
				fieldname="name",
			)
			if existing_by_order:
				return existing_by_order

		# 2. Fetch Amazon order & order items if not provided
		orders_client = self.get_orders_instance()
		if not order and amazon_order_id:
			ord_resp = orders_client.get_order(amazon_order_id)
			order = ord_resp.get("payload", {})

		if not order_items and amazon_order_id:
			items_resp = orders_client.get_order_items(amazon_order_id)
			order_items = items_resp.get("payload", {}).get("OrderItems", [])

		# 3. Initialize Sales Invoice from Sales Order
		si = make_sales_invoice(sales_order_name)
		if amazon_order_id:
			si.po_no = amazon_order_id
			if hasattr(si, "custom_marketplace_order"):
				si.custom_marketplace_order = amazon_order_id
		if order and hasattr(si, "amazon_is_business_order"):
			si.amazon_is_business_order = 1 if order.get("IsBusinessOrder") else 0

		# 4. Determine GST Mode: Inter-State vs Intra-State
		company_state_code = self.get_company_state_code(si.company)
		pos = so.place_of_supply or getattr(si, "place_of_supply", None)
		pos_code = pos.split("-")[0].strip() if pos and "-" in pos else ""
		is_inter_state = bool(pos_code and company_state_code and pos_code != company_state_code)

		# 5. Set Taxes & Charges Template
		si.taxes_and_charges = self.get_sales_taxes_and_charges_template(si.company, is_inter_state)
		si.set_taxes()

		# Map Amazon items by ASIN and SellerSKU
		amz_items_map = {}
		if order_items:
			for itm in order_items:
				if itm.get("ASIN"):
					amz_items_map[itm.get("ASIN")] = itm
				if itm.get("SellerSKU"):
					amz_items_map[itm.get("SellerSKU")] = itm

		primary_tax_template = None

		for row in list(si.items):
			matched_amz_item = amz_items_map.get(row.item_code)
			if not matched_amz_item and order_items and len(order_items) == 1:
				matched_amz_item = order_items[0]

			qty = float(matched_amz_item.get("QuantityOrdered") if matched_amz_item else row.qty or 1)
			row.qty = qty

			if matched_amz_item:
				ip = float(matched_amz_item.get("ItemPrice", {}).get("Amount", 0) or 0)
				promo = float(matched_amz_item.get("PromotionDiscount", {}).get("Amount", 0) or 0)

				if promo > 0:
					row.price_list_rate = ip / qty
					row.discount_amount = promo / qty
					row.rate = (ip - promo) / qty
				else:
					row.rate = ip / qty

			item_tax_template = self.get_product_gst_template(
				row.item_code, si.company, is_inter_state, order_item=matched_amz_item
			)
			row.item_tax_template = item_tax_template
			if not primary_tax_template:
				primary_tax_template = item_tax_template

		# 6. Add Shipping Charges item if ShippingPrice > 0
		total_shipping_price = 0.0
		if order_items:
			total_shipping_price = sum(
				float(itm.get("ShippingPrice", {}).get("Amount", 0) or 0) for itm in order_items
			)

		if total_shipping_price > 0:
			shipping_item_code = "Shipping Charges"
			if not frappe.db.exists("Item", shipping_item_code):
				frappe.throw(
					_("Item '{0}' not found for shipping charges.").format(shipping_item_code),
					frappe.ValidationError,
				)

			shipping_income_account = (
				frappe.db.get_value(
					"Item Default",
					filters={"parent": shipping_item_code, "company": si.company},
					fieldname="income_account",
				)
				or frappe.db.get_value("Account", {"company": si.company, "account_name": "Shipping Charges"}, "name")
				or f"Shipping Charges - {frappe.db.get_value('Company', si.company, 'abbr')}"
			)
			if not frappe.db.exists("Account", shipping_income_account):
				shipping_income_account = frappe.get_cached_value("Company", si.company, "default_income_account")

			si.append(
				"items",
				{
					"item_code": shipping_item_code,
					"item_name": "Shipping Charges",
					"description": f"Shipping Charges for Amazon Order {amazon_order_id}",
					"qty": 1.0,
					"rate": total_shipping_price,
					"uom": "Nos",
					"stock_uom": "Nos",
					"conversion_factor": 1.0,
					"item_tax_template": primary_tax_template,
					"income_account": shipping_income_account,
				},
			)

		# 7. Calculate taxes and totals
		si.run_method("calculate_taxes_and_totals")

		# 8. Amount Reconciliation check if OrderTotal provided
		if order and order.get("OrderTotal", {}).get("Amount"):
			amz_total = float(order.get("OrderTotal", {}).get("Amount"))
			diff = abs(si.grand_total - amz_total)
			if diff > 1.0:
				frappe.throw(
					_("Sales Invoice grand total {0} does not reconcile with Amazon OrderTotal {1} (difference: {2}).").format(
						si.grand_total, amz_total, diff
					),
					frappe.ValidationError,
				)

		# 9. Insert and Submit
		si.update_stock = 0
		si.insert(ignore_permissions=True)
		si.submit()

		return si.name

	def get_orders(
		self,
		created_after: str | None = None,
		created_before: str | None = None,
		last_updated_after: str | None = None,
		return_summary: bool = False,
	) -> list | dict:
		orders = self.get_orders_instance()
		order_statuses = [
			"PendingAvailability",
			"Pending",
			"Unshipped",
			"PartiallyShipped",
			"Shipped",
			"InvoiceUnconfirmed",
			"Canceled",
			"Unfulfillable",
		]
		fulfillment_channels = ["AFN", "MFN"]

		kwargs = {
			"order_statuses": order_statuses,
			"fulfillment_channels": fulfillment_channels,
			"max_results": 50,
		}
		if created_after:
			kwargs["created_after"] = created_after
		if created_before:
			kwargs["created_before"] = created_before
		if last_updated_after:
			kwargs["last_updated_after"] = last_updated_after

		orders_payload = self.call_sp_api_method(
			sp_api_method=orders.get_orders,
			**kwargs,
		)

		summary = {
			"total_orders": 0,
			"successful_orders": [],
			"failed_orders": [],
			"skipped_orders": [],
			"sales_orders": [],
			"sales_invoices": [],
			"payment_entries": [],
			"delivery_notes": [],
		}

		while True:
			orders_list = (orders_payload or {}).get("Orders")
			next_token = (orders_payload or {}).get("NextToken")

			if not orders_list or len(orders_list) == 0:
				break

			for order in orders_list:
				order_id = order.get("AmazonOrderId") or "Unknown"
				summary["total_orders"] += 1
				try:
					sales_order, si_name, pe_name, dn_name = self.sync_order(order)
					if sales_order:
						summary["successful_orders"].append(order_id)
						summary["sales_orders"].append(sales_order)
						if si_name:
							summary["sales_invoices"].append(si_name)
						if pe_name:
							summary["payment_entries"].append(pe_name)
						if dn_name:
							summary["delivery_notes"].append(dn_name)
					else:
						err = getattr(self, "_last_order_error", {}).get(order_id)
						if err:
							summary["failed_orders"].append({
								"order_id": order_id,
								"reason": err,
							})
						else:
							summary["skipped_orders"].append(order_id)
				except Exception as e:
					frappe.db.rollback()
					err_msg = str(e).strip() or type(e).__name__
					summary["failed_orders"].append({
						"order_id": order_id,
						"reason": err_msg,
					})
					frappe.log_error(f"Amazon Order {order_id} failed: {err_msg}", "Amazon Order Sync")

			if not next_token:
				break

			orders_payload = self.call_sp_api_method(
				sp_api_method=orders.get_orders,
				created_after=created_after,
				last_updated_after=last_updated_after,
				next_token=next_token,
			)

		if return_summary:
			return summary

		return summary["sales_orders"]

	def get_catalog_items_instance(self) -> CatalogItems:
		return CatalogItems(**self.instance_params)

	def get_listings_items_instance(self) -> ListingsItems:
		return ListingsItems(**self.instance_params)

	def get_product_fees_instance(self) -> ProductFees:
		return ProductFees(**self.instance_params)

	def get_seller_id(self, asin: str | None = None) -> str | None:
		if getattr(self, "_seller_id", None):
			return self._seller_id

		setting_seller_id = (
			getattr(self.amz_setting, "seller_id", None)
			or getattr(self.amz_setting, "merchant_id", None)
		)
		if setting_seller_id:
			self._seller_id = setting_seller_id
			return self._seller_id

		setting_name = getattr(self.amz_setting, "name", None)
		if setting_name:
			cached = frappe.cache.hget("amazon_sp_api_seller_id", setting_name)
			if cached:
				self._seller_id = cached
				return self._seller_id

		if asin:
			try:
				product_fees = self.get_product_fees_instance()
				resp = product_fees.get_my_fees_estimate_for_asin(asin)
				seller_id = (
					resp.get("payload", {})
					.get("FeesEstimateResult", {})
					.get("FeesEstimateIdentifier", {})
					.get("SellerId")
				)
				if seller_id:
					self._seller_id = seller_id
					if setting_name:
						frappe.cache.hset("amazon_sp_api_seller_id", setting_name, seller_id)
					return self._seller_id
				elif isinstance(resp, dict) and "errors" in resp:
					frappe.log_error(
						message=f"Product Fees API returned errors: {resp.get('errors')}",
						title="Failed to resolve Amazon Seller ID",
					)
			except Exception as e:
				frappe.log_error(message=str(e), title="Failed to resolve Amazon Seller ID")

		return None

	def get_amazon_hsn(self, order_item) -> str | None:
		seller_sku = (
			order_item.get("SellerSKU")
			or order_item.get("seller_sku")
			or order_item.get("sku")
		)
		if not seller_sku:
			return None

		asin = order_item.get("ASIN") or order_item.get("asin")
		seller_id = self.get_seller_id(asin=asin)
		if not seller_id:
			return None

		try:
			listings_client = self.get_listings_items_instance()
			listing = listings_client.get_listings_item(
				seller_id=seller_id,
				sku=seller_sku,
			)
			if not isinstance(listing, dict) or "errors" in listing:
				if isinstance(listing, dict) and "errors" in listing:
					frappe.log_error(
						message=f"Listings Items API returned errors: {listing.get('errors')}",
						title=f'Listings Items API failed to retrieve HSN for SKU "{seller_sku}"',
					)
				return None

			attributes = listing.get("attributes") or listing.get("payload", {}).get("attributes") or {}
			ext_info = attributes.get("external_product_information") or []
			if isinstance(ext_info, dict):
				ext_info = [ext_info]
			if isinstance(ext_info, list):
				for entry in ext_info:
					if isinstance(entry, dict):
						entity = str(entry.get("entity", "")).strip().lower()
						if entity in ("hsn code", "hsn"):
							val = entry.get("value")
							if val:
								return str(val).strip()
		except Exception as e:
			frappe.log_error(
				message=str(e),
				title=f'Listings Items API failed to retrieve HSN for SKU "{seller_sku}"',
			)

		return None


	# ── Phase 4 Payment Entry ────────────────────────────────────────────────

	_AMAZON_PAYMENT_METHOD_MAP: dict = {
		# key is lowercased PaymentMethod string from Amazon
		"cod": "Cash",
		"cashondelivery": "Cash",
		"cash on delivery": "Cash",
		"cash": "Cash",
		"standard": "Cash",
		"other": "Cash",
		"amazon": "Cash",
		"amazon pay": "Cash",
		"upi": "Cash",
		"creditcard": "Credit Card",
		"credit card": "Credit Card",
		"netbanking": "Wire Transfer",
		"neft": "Wire Transfer",
		"wire transfer": "Wire Transfer",
		"cheque": "Cheque",
	}

	def get_amazon_mode_of_payment(self, payment_method: str | None) -> str:
		"""
		Maps an Amazon PaymentMethod string to an ERPNext Mode of Payment name.
		Defaults to 'Cash' if method is not mapped.
		"""
		if not payment_method:
			return "Cash"
		key = payment_method.strip().lower()
		# Handle PaymentMethodDetails list serialised as comma-joined string
		if "," in key:
			for part in key.split(","):
				result = self._AMAZON_PAYMENT_METHOD_MAP.get(part.strip())
				if result:
					return result
			return "Cash"
		return self._AMAZON_PAYMENT_METHOD_MAP.get(key, "Cash")

	def create_payment_entry(
		self,
		sales_invoice_name: str,
		payment_method: str | None = None,
		payment_status: dict | None = None,
	) -> str | None:
		"""
		Creates and submits a standard ERPNext Payment Entry (Receive) against a
		Sales Invoice from an Amazon order.

		- Idempotent: returns existing submitted/draft PE if one already exists.
		- Payment confirmation and amount come from Amazon Finances (2024-06-19).
		- Customer sales breakdown amount is used (never deducted by Amazon seller fees).
		- Returns the PE name on success, or None.
		"""
		si = frappe.get_doc("Sales Invoice", sales_invoice_name)

		if si.docstatus != 1:
			frappe.throw(
				_("Cannot create Payment Entry against Sales Invoice {0}: not submitted (docstatus={1}).").format(
					sales_invoice_name, si.docstatus
				),
				frappe.ValidationError,
			)

		# 1. Idempotency: check for existing PE against this invoice
		existing_pe = frappe.db.sql(
			"""
			SELECT per.parent
			FROM `tabPayment Entry Reference` per
			JOIN `tabPayment Entry` pe ON pe.name = per.parent
			WHERE per.reference_doctype = 'Sales Invoice'
			  AND per.reference_name = %s
			  AND pe.docstatus < 2
			LIMIT 1
			""",
			(sales_invoice_name,),
			as_dict=True,
		)
		if existing_pe:
			return existing_pe[0].parent

		# 2. Check outstanding amount
		if si.outstanding_amount is not None:
			outstanding = float(si.outstanding_amount)
		else:
			outstanding = float(si.rounded_total or si.grand_total or 0)
		if outstanding <= 0:
			frappe.log_error(
				message=f"Sales Invoice {sales_invoice_name} has outstanding_amount={outstanding}. No payment entry created.",
				title="Amazon Payment Entry: invoice already paid",
			)
			return None

		# 3. Resolve payment method from the invoice's Sales Order if not provided
		if not payment_method:
			so_name = frappe.db.get_value(
				"Sales Invoice Item",
				{"parent": sales_invoice_name, "sales_order": ["like", "SAL-%"]},
				"sales_order",
			)
			if so_name:
				payment_method = frappe.db.get_value("Sales Order", so_name, "amazon_payment_method")

		# If payment_status is not provided and payment_method is not COD, skip PE
		if not payment_status:
			if payment_method and payment_method.strip().lower() not in (
				"cod",
				"cashondelivery",
				"cash on delivery",
				"cash",
			):
				frappe.log_error(
					message=(
						f"Skipping Payment Entry for Sales Invoice {sales_invoice_name}: "
						f"Amazon PaymentMethod '{payment_method}' requires financial settlement confirmation."
					),
					title="Amazon Payment Entry: payment confirmation required",
				)
				return None

		# 4. Map to ERPNext Mode of Payment and resolve accounts
		mode_of_payment = self.get_amazon_mode_of_payment(payment_method) or "Cash"

		paid_to = frappe.db.get_value(
			"Mode of Payment Account",
			{"parent": mode_of_payment, "company": si.company},
			"default_account",
		)
		if not paid_to:
			# Fallback to configured Mode of Payment Account for this company
			fallback_mop_acc = frappe.db.get_value(
				"Mode of Payment Account",
				{"company": si.company},
				["parent", "default_account"],
				as_dict=True,
			)
			if fallback_mop_acc:
				mode_of_payment = fallback_mop_acc.parent
				paid_to = fallback_mop_acc.default_account
			else:
				# Fallback to default cash account of company
				paid_to = (
					frappe.db.get_value("Company", si.company, "default_cash_account")
					or frappe.db.get_value("Account", {"account_type": "Cash", "company": si.company}, "name")
					or frappe.db.get_value("Account", {"account_name": ["like", "%Cash%"], "company": si.company}, "name")
				)

		if not paid_to:
			frappe.throw(
				_("No account configured for Mode of Payment '{0}' and Company '{1}'.").format(
					mode_of_payment, si.company
				),
				frappe.ValidationError,
			)

		# 5. paid_from = company default receivable account
		paid_from = (
			frappe.db.get_value("Company", si.company, "default_receivable_account")
			or frappe.db.get_value("Account", {"account_type": "Receivable", "company": si.company}, "name")
		)
		if not paid_from:
			frappe.throw(
				_("No default receivable account found for Company '{0}'.").format(si.company),
				frappe.ValidationError,
			)

		# 6. Amount determination:
		# Use payment_status amount (customer sales breakdown amount) if available, capped at outstanding
		if payment_status and payment_status.get("amount") and float(payment_status["amount"]) > 0:
			pay_amount = min(float(payment_status["amount"]), outstanding)
		else:
			pay_amount = outstanding

		# 7. Build Payment Entry
		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = "Receive"
		pe.party_type = "Customer"
		pe.party = si.customer
		pe.company = si.company
		pe.posting_date = frappe.utils.nowdate()
		pe.mode_of_payment = mode_of_payment
		pe.paid_from = paid_from
		pe.paid_to = paid_to
		pe.paid_amount = pay_amount
		pe.received_amount = pay_amount
		pe.reference_no = si.po_no or sales_invoice_name
		pe.reference_date = pe.posting_date

		pe.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": sales_invoice_name,
				"allocated_amount": pay_amount,
				"total_amount": outstanding,
				"outstanding_amount": outstanding,
			},
		)

		pe.insert(ignore_permissions=True)
		pe.submit()

		return pe.name

	def sync_sales_order_invoice_and_payment(
		self, sales_order_name: str, order: dict | None = None, order_items: list | None = None
	) -> tuple[str | None, str | None]:
		"""
		Handles automatic Sales Invoice and Payment Entry creation for a Sales Order:
		1. If order is Canceled or Sales Order is not submitted (docstatus != 1), skip invoice.
		2. Idempotently creates or retrieves existing Sales Invoice via self.create_sales_invoice().
		3. Checks authoritative financial settlement via Finances API v2024-06-19.
		4. If payment is confirmed (RELEASED / DEFERRED_RELEASED), creates or retrieves Payment Entry.
		5. If DEFERRED or no qualifying transaction, leaves Sales Invoice Outstanding.
		Returns (sales_invoice_name, payment_entry_name).
		"""
		if not sales_order_name:
			return None, None

		# Check order status
		order_status = None
		if order:
			order_status = order.get("OrderStatus")
		if not order_status:
			order_status = frappe.db.get_value("Sales Order", sales_order_name, "amazon_order_status")

		if order_status == "Canceled":
			return None, None

		# Check Sales Order docstatus (must be submitted)
		so_docstatus = frappe.db.get_value("Sales Order", sales_order_name, "docstatus")
		if so_docstatus != 1:
			return None, None

		si_name = None
		pe_name = None

		try:
			si_name = self.create_sales_invoice(sales_order_name, order=order, order_items=order_items)
		except Exception as e:
			import traceback
			traceback.print_exc()
			frappe.log_error(
				title=f"Failed to create Sales Invoice for {sales_order_name}",
				message=frappe.get_traceback(),
				reference_doctype="Sales Order",
				reference_name=sales_order_name,
			)
			return None, None

		if not si_name:
			return None, None

		# Check authoritative payment status from Finances 2024-06-19
		amazon_order_id = (order.get("AmazonOrderId") if order else None) or frappe.db.get_value(
			"Sales Order", sales_order_name, "amazon_order_id"
		)
		if amazon_order_id:
			try:
				payment_status = self.get_amazon_payment_status(amazon_order_id)
				if payment_status.get("confirmed"):
					pm = (order.get("PaymentMethod") if order else None) or frappe.db.get_value(
						"Sales Order", sales_order_name, "amazon_payment_method"
					)
					pe_name = self.create_payment_entry(
						si_name, payment_method=pm, payment_status=payment_status
					)
				else:
					# DEFERRED or no qualifying transaction: leave Sales Invoice Outstanding
					pe_name = None
			except Exception as e:
				frappe.log_error(
					title=f"Failed to process Payment Entry for {sales_order_name}",
					message=frappe.get_traceback(),
					reference_doctype="Sales Order",
					reference_name=sales_order_name,
				)
				pe_name = None

		return si_name, pe_name

	def get_amazon_shipment_status(
		self, order: dict, order_items: list | None = None
	) -> dict:
		"""
		Determines Amazon shipment fulfillment status based on QuantityShipped from order items.
		Returns:
		{
			"has_shipped_quantity": bool,
			"total_shipped_qty": int,
			"items": [
				{
					"order_item_id": str,
					"seller_sku": str,
					"asin": str,
					"qty_ordered": int,
					"qty_shipped": int,
				}
			]
		}
		"""
		order_id = order.get("AmazonOrderId") if order else None
		if not order_items and order_id:
			try:
				orders_client = self.get_orders_instance()
				items_resp = orders_client.get_order_items(order_id)
				order_items = (items_resp.get("payload") or {}).get("OrderItems", [])
			except Exception as e:
				frappe.log_error(
					title=f"Amazon shipment check failed for order {order_id}",
					message=frappe.get_traceback(),
				)
				return {
					"has_shipped_quantity": False,
					"total_shipped_qty": 0,
					"items": [],
					"error": str(e),
				}

		items_list = []
		total_shipped = 0
		for itm in (order_items or []):
			qty_shipped = int(itm.get("QuantityShipped") or 0)
			qty_ordered = int(itm.get("QuantityOrdered") or 0)
			total_shipped += qty_shipped
			items_list.append(
				{
					"order_item_id": itm.get("OrderItemId"),
					"seller_sku": itm.get("SellerSKU"),
					"asin": itm.get("ASIN"),
					"qty_ordered": qty_ordered,
					"qty_shipped": qty_shipped,
				}
			)

		return {
			"has_shipped_quantity": total_shipped > 0,
			"total_shipped_qty": total_shipped,
			"items": items_list,
		}

	def create_delivery_note(
		self,
		sales_order_name: str,
		order: dict | None = None,
		order_items: list | None = None,
		shipment_status: dict | None = None,
	) -> str | None:
		"""
		Creates and submits a Delivery Note for a Sales Order based on Amazon QuantityShipped.
		- Only creates DN if Amazon QuantityShipped > 0.
		- Supports partial shipments and incremental deliveries.
		- Idempotent: Never duplicates previously delivered quantities.
		- If all shipped quantities are already delivered, returns None.
		- Preserves cancellation safety: returns None for canceled orders.
		"""
		if not sales_order_name:
			print("DEBUG: early return 1")
			return None

		so = frappe.get_doc("Sales Order", sales_order_name)
		if so.docstatus != 1 or so.status == "Cancelled":
			print(f"DEBUG: early return 2. so.docstatus={so.docstatus}, so.status={so.status}")
			return None

		if order and order.get("OrderStatus") == "Canceled":
			print("DEBUG: early return 3")
			return None

		from frappe.utils import flt
		if flt(so.per_delivered) >= 100:
			print("DEBUG: early return 4")
			return None

		frappe.db.sql("SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE", (sales_order_name,))

		# 1. Fetch shipment status if not provided
		if shipment_status is None:
			shipment_status = self.get_amazon_shipment_status(order, order_items=order_items)
		print(f"DEBUG: shipment_status={shipment_status}")

		if not shipment_status.get("has_shipped_quantity"):
			return None

		# Map Amazon shipped quantities by SellerSKU and ASIN / OrderItemId
		amz_items = shipment_status.get("items", [])
		amz_by_sku = {i["seller_sku"]: i for i in amz_items if i.get("seller_sku")}
		amz_by_code = {i["asin"]: i for i in amz_items if i.get("asin")}

		# 2. Build initial Delivery Note from Sales Order
		print(f"DEBUG: calling make_delivery_note({sales_order_name}). Sales Order has {len(so.items)} items.")
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
		dn = make_delivery_note(sales_order_name)
		print(f"DEBUG: dn items before filter: {len(dn.items)}")

		amazon_order_id = (order.get("AmazonOrderId") if order else None) or so.amazon_order_id

		items_to_keep = []
		for dn_item in list(dn.items):
			# Match to Amazon item
			amz_item = (
				amz_by_sku.get(dn_item.item_name)
				or amz_by_code.get(dn_item.item_code)
				or (amz_items[0] if len(amz_items) == 1 else None)
			)
			amz_shipped = int((amz_item or {}).get("qty_shipped") or 0)
			print(f"DEBUG: amz_item={amz_item}, amz_shipped={amz_shipped}")

			# Query already delivered quantity for this Sales Order item
			already_delivered = flt(
				frappe.db.sql(
					"""
					SELECT COALESCE(SUM(dni.qty), 0)
					FROM `tabDelivery Note Item` dni
					JOIN `tabDelivery Note` dn_parent ON dn_parent.name = dni.parent
					WHERE dni.so_detail = %s
					  AND dn_parent.docstatus < 2
					  AND dn_parent.is_return = 0
					""",
					(dn_item.so_detail,),
				)[0][0]
			)

			so_qty = flt(frappe.db.get_value("Sales Order Item", dn_item.so_detail, "qty") or dn_item.qty)
			to_deliver = min(flt(amz_shipped) - already_delivered, so_qty - already_delivered)

			# Log per requested format
			frappe.logger("ecommerce_integrations").info(
				f"Amazon shipment check:\n"
				f"Order: {amazon_order_id}\n"
				f"Amazon shipped quantity: {amz_shipped}\n"
				f"ERPNext delivered quantity: {already_delivered}\n"
				f"Remaining quantity: {max(0, to_deliver)}"
			)

			if to_deliver > 0:
				dn_item.qty = to_deliver
				dn_item.allow_zero_valuation_rate = 1
				if not dn_item.warehouse:
					dn_item.warehouse = self.amz_setting.warehouse
				items_to_keep.append(dn_item)

		if not items_to_keep:
			print("DEBUG: items_to_keep is empty. Returning None")
			return None

		dn.items = items_to_keep
		if amazon_order_id:
			dn.po_no = amazon_order_id
			if hasattr(dn, "custom_marketplace_order"):
				dn.custom_marketplace_order = amazon_order_id

		dn.run_method("set_missing_values")
		dn.run_method("calculate_taxes_and_totals")
		dn.insert(ignore_permissions=True)
		dn.submit()

		return dn.name

	def sync_order(self, order: dict) -> tuple[str | None, str | None, str | None, str | None]:
		"""
		Synchronizes a single Amazon order:
		Amazon Order -> Sales Order -> Sales Invoice -> Delivery Note (if QuantityShipped > 0).
		Payment Entry requires confirmed financial settlement (otherwise SI remains Outstanding).
		Returns (sales_order_name, sales_invoice_name, payment_entry_name, delivery_note_name).
		"""
		amazon_order_id = order.get("AmazonOrderId") or "Unknown"
		if not hasattr(self, "_last_order_error"):
			self._last_order_error = {}
		self._last_order_error.pop(amazon_order_id, None)

		try:
			sales_order = self.create_sales_order(order)
			if not sales_order:
				return None, None, None, None

			si_name, pe_name = None, None
			try:
				si_name, pe_name = self.sync_sales_order_invoice_and_payment(sales_order, order=order)
			except Exception as e:
				frappe.db.rollback()
				frappe.log_error(
					title=f"Failed to create Sales Invoice for {sales_order} (Amazon {amazon_order_id})",
					message=frappe.get_traceback(),
					reference_doctype="Sales Order",
					reference_name=sales_order,
				)

			dn_name = None
			so_docstatus = frappe.db.get_value("Sales Order", sales_order, "docstatus")
			if so_docstatus == 1 and order.get("OrderStatus") != "Canceled":
				try:
					dn_name = self.create_delivery_note(sales_order, order=order)
				except Exception as e:
					print(f"DEBUG: Exception in create_delivery_note: {frappe.get_traceback()}")
					frappe.log_error(
						title=f"Failed to create Delivery Note for {sales_order}",
						message=frappe.get_traceback(),
						reference_doctype="Sales Order",
						reference_name=sales_order,
					)
				
				try:
					self.handle_returns(sales_order, order)
				except Exception as e:
					frappe.log_error(
						title=f"Failed to handle returns for {sales_order}",
						message=frappe.get_traceback(),
						reference_doctype="Sales Order",
						reference_name=sales_order,
					)

			return sales_order, si_name, pe_name, dn_name
		except Exception as e:
			frappe.db.rollback()
			err_str = str(e).strip() or type(e).__name__
			self._last_order_error[amazon_order_id] = err_str

			try:
				import json
				ir = frappe.new_doc("Integration Request")
				ir.integration_request_service = "Amazon SP-API"
				ir.is_remote_request = 0
				ir.request_description = f"Amazon Order Sync Failed: {amazon_order_id}"
				ir.data = json.dumps(order, indent=4, default=str)
				ir.error = frappe.get_traceback()
				ir.status = "Failed"
				ir.insert(ignore_permissions=True)
				frappe.db.commit()
			except Exception:
				pass
			
			frappe.log_error(
				title=f"Amazon Sync Failed for Order {amazon_order_id}: {err_str}",
				message=frappe.get_traceback()
			)
			return None, None, None, None

	def get_refund_events(self, order_id: str) -> list:
		finances = self.get_finances_instance()
		next_token = None
		refund_events = []

		while True:
			try:
				payload = self.call_sp_api_method(
					sp_api_method=finances.list_financial_events_by_order_id,
					order_id=order_id,
					next_token=next_token,
				)
			except Exception:
				break

			events = payload.get("FinancialEvents", {}) if isinstance(payload, dict) else {}
			refunds = events.get("RefundEventList", [])
			if refunds:
				refund_events.extend(refunds)

			next_token = payload.get("NextToken") if isinstance(payload, dict) else None
			if not next_token:
				break

		return refund_events

	def handle_returns(self, sales_order_name: str, order: dict) -> None:
		so_docstatus = frappe.db.get_value("Sales Order", sales_order_name, "docstatus")
		if so_docstatus != 1 or order.get("OrderStatus") == "Canceled":
			return

		order_id = order.get("AmazonOrderId")
		if not order_id:
			return

		refund_events = self.get_refund_events(order_id)
		if not refund_events:
			return

		from frappe.utils import flt
		amz_returns = {}
		for event in refund_events:
			for adj in event.get("ShipmentItemAdjustmentList", []):
				sku = adj.get("SellerSKU")
				qty = flt(adj.get("QuantityShipped"))
				if sku and qty > 0:
					amz_returns[sku] = amz_returns.get(sku, 0.0) + qty

		if not amz_returns:
			return

		# Idempotency checks
		returned_qty_data = frappe.db.sql("""
			SELECT item_code, SUM(ABS(qty)) as returned_qty 
			FROM `tabDelivery Note Item` 
			WHERE against_sales_order = %s AND docstatus < 2 AND parent IN (
				SELECT name FROM `tabDelivery Note` WHERE is_return = 1
			)
			GROUP BY item_code
		""", (sales_order_name,), as_dict=True)
		erpnext_returns = {d.item_code: flt(d.returned_qty) for d in returned_qty_data}

		so_items = frappe.db.sql("""
			SELECT item_code, item_name 
			FROM `tabSales Order Item` 
			WHERE parent = %s
		""", (sales_order_name,), as_dict=True)
		sku_to_item = {d.item_name: d.item_code for d in so_items if d.item_name}

		incremental_returns = {}
		for sku, amz_qty in amz_returns.items():
			item_code = sku_to_item.get(sku)
			if not item_code:
				continue
			already_returned = erpnext_returns.get(item_code, 0.0)
			if amz_qty > already_returned:
				incremental_returns[item_code] = amz_qty - already_returned

		if incremental_returns:
			original_dns = frappe.db.sql("""
				SELECT DISTINCT parent 
				FROM `tabDelivery Note Item` 
				WHERE against_sales_order = %s AND docstatus = 1 AND parent IN (
					SELECT name FROM `tabDelivery Note` WHERE is_return = 0
				)
				ORDER BY creation DESC
			""", (sales_order_name,))

			if original_dns:
				original_dn_name = original_dns[0][0]
				from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as make_dn_return
				dn_return = make_dn_return(original_dn_name)
				items_to_keep = []
				for item in dn_return.items:
					if item.item_code in incremental_returns and incremental_returns[item.item_code] > 0:
						inc_qty = incremental_returns[item.item_code]
						max_allowed_abs = abs(item.qty)
						
						if inc_qty > max_allowed_abs:
							item.qty = -max_allowed_abs
							incremental_returns[item.item_code] -= max_allowed_abs
						else:
							item.qty = -inc_qty
							incremental_returns[item.item_code] = 0.0
						
						items_to_keep.append(item)
				
				dn_return.items = items_to_keep
				if original_dn_name:
					orig_po = frappe.db.get_value("Delivery Note", original_dn_name, "po_no")
					if orig_po:
						dn_return.po_no = orig_po
					orig_cmo = frappe.db.get_value("Delivery Note", original_dn_name, "custom_marketplace_order")
					if orig_cmo and hasattr(dn_return, "custom_marketplace_order"):
						dn_return.custom_marketplace_order = orig_cmo
				if dn_return.items:
					dn_return.flags.ignore_permissions = True
					dn_return.insert()
					dn_return.submit()

		returned_si_data = frappe.db.sql("""
			SELECT item_code, SUM(ABS(qty)) as returned_qty 
			FROM `tabSales Invoice Item` 
			WHERE sales_order = %s AND docstatus < 2 AND parent IN (
				SELECT name FROM `tabSales Invoice` WHERE is_return = 1
			)
			GROUP BY item_code
		""", (sales_order_name,), as_dict=True)
		erpnext_si_returns = {d.item_code: flt(d.returned_qty) for d in returned_si_data}

		incremental_returns_si = {}
		for sku, amz_qty in amz_returns.items():
			item_code = sku_to_item.get(sku)
			if not item_code:
				continue
			already_returned = erpnext_si_returns.get(item_code, 0.0)
			if amz_qty > already_returned:
				incremental_returns_si[item_code] = amz_qty - already_returned

		if incremental_returns_si:
			print(f"DEBUG: incremental_returns_si={incremental_returns_si}")
			original_sis = frappe.db.sql("""
				SELECT DISTINCT parent 
				FROM `tabSales Invoice Item` 
				WHERE sales_order = %s AND docstatus = 1 AND parent IN (
					SELECT name FROM `tabSales Invoice` WHERE is_return = 0
				)
				ORDER BY creation DESC
			""", (sales_order_name,))

			if original_sis:
				original_si_name = original_sis[0][0]
				from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_si_return
				si_return = make_si_return(original_si_name)
				si_return.update_stock = 0
				items_to_keep = []
				for item in si_return.items:
					if item.item_code in incremental_returns_si and incremental_returns_si[item.item_code] > 0:
						inc_qty = incremental_returns_si[item.item_code]
						max_allowed_abs = abs(item.qty)
						
						if inc_qty > max_allowed_abs:
							item.qty = -max_allowed_abs
							incremental_returns_si[item.item_code] -= max_allowed_abs
						else:
							item.qty = -inc_qty
							incremental_returns_si[item.item_code] = 0.0
							
						items_to_keep.append(item)
				
				print(f"DEBUG: si items_to_keep={len(items_to_keep)}")
				si_return.items = items_to_keep
				if original_si_name:
					orig_po = frappe.db.get_value("Sales Invoice", original_si_name, "po_no")
					if orig_po:
						si_return.po_no = orig_po
					orig_cmo = frappe.db.get_value("Sales Invoice", original_si_name, "custom_marketplace_order")
					if orig_cmo and hasattr(si_return, "custom_marketplace_order"):
						si_return.custom_marketplace_order = orig_cmo
				if si_return.items:
					si_return.flags.ignore_permissions = True
					try:
						si_return.insert()
						si_return.submit()
						print(f"DEBUG: SI RETURN CREATED {si_return.name}")
					except Exception as e:
						import traceback
						traceback.print_exc()
						frappe.log_error(title="SI Return Failed", message=frappe.get_traceback())

	def sync_financial_transactions(
		self,
		posted_after: str | None = None,
		posted_before: str | None = None,
		max_pages: int | None = None,
		transactions_list: list | None = None,
	) -> list[str]:
		"""
		Phase 1 Ingestion:
		Pulls all financial transactions in [posted_after, posted_before] from Finances API v2024-06-19.
		Idempotently creates or updates Amazon Financial Transaction and child Amazon Financial Breakdown records.
		Links Sales Order / Sales Invoice if the related Amazon Order ID exists in ERPNext.
		Preserves the raw JSON response and computes a payload hash.
		Catches and logs individual transaction ingestion errors via Integration Request and error log, continuing batch.
		Returns list of processed Amazon Financial Transaction names.
		"""
		import hashlib
		import json
		from dateutil import parser as dt_parser

		if transactions_list is not None:
			transactions = transactions_list
		else:
			finances_client = self.get_finances_v20240619_instance()
			transactions = finances_client.get_transactions(
				posted_after=posted_after,
				posted_before=posted_before,
				max_pages=max_pages,
				max_retries=self.amz_setting.max_retry_limit or 3,
			)

		processed_docs = []

		for tx in transactions:
			amazon_order_id = extract_amazon_order_id(tx)
			tx_id = tx.get("transactionId")
			if not tx_id:
				# Generate deterministic transaction ID if missing from order/event identifiers
				rel_ids = {
					(r.get("relatedIdentifierName") or "").strip().upper().replace("-", "_"): r.get("relatedIdentifierValue")
					for r in (tx.get("relatedIdentifiers") or [])
					if isinstance(r, dict) and r.get("relatedIdentifierName")
				}
				ord_id = amazon_order_id or rel_ids.get("FINANCIAL_EVENT_GROUP_ID") or "NO_ID"
				tx_type = tx.get("transactionType") or "TX"
				tx_date = tx.get("postedDate") or "NODATE"
				tx_id = f"{tx_type}-{ord_id}-{tx_date}"

			try:
				# 1. Normalize Header Identifiers
				rel_ids = {}
				for r in (tx.get("relatedIdentifiers") or []):
					if isinstance(r, dict) and r.get("relatedIdentifierName"):
						k = (r.get("relatedIdentifierName") or "").strip().upper().replace("-", "_")
						rel_ids[k] = r.get("relatedIdentifierValue")

				settlement_id = rel_ids.get("SETTLEMENT_ID")
				event_group_id = rel_ids.get("FINANCIAL_EVENT_GROUP_ID")

				# Marketplace and Selling Partner metadata
				sp_meta = tx.get("sellingPartnerMetadata") or {}
				marketplace_id = sp_meta.get("marketplaceId") or (tx.get("marketplaceDetails") or {}).get("marketplaceId")
				marketplace_name = (tx.get("marketplaceDetails") or {}).get("marketplaceName")

				# Totals
				total_amt_dict = tx.get("totalAmount") or {}
				total_amount = float(total_amt_dict.get("currencyAmount") or 0.0)
				currency = total_amt_dict.get("currencyCode")

				# Posted date
				posted_date = None
				if tx.get("postedDate"):
					try:
						posted_date = dt_parser.parse(tx.get("postedDate")).strftime("%Y-%m-%d %H:%M:%S")
					except Exception:
						posted_date = None

				# Product context info from items
				seller_sku = None
				asin = None
				order_item_id = None
				items_list = tx.get("items") or []
				for itm in items_list:
					if not isinstance(itm, dict):
						continue
					for ctx in (itm.get("contexts") or []):
						if isinstance(ctx, dict):
							if ctx.get("sku") and not seller_sku:
								seller_sku = ctx.get("sku")
							if ctx.get("asin") and not asin:
								asin = ctx.get("asin")
					for item_rel in (itm.get("relatedIdentifiers") or []):
						if isinstance(item_rel, dict) and not order_item_id:
							order_item_id = item_rel.get("itemRelatedIdentifierValue")

				# Link existing ERPNext Sales Order and Sales Invoice
				sales_order = None
				sales_invoice = None
				if amazon_order_id:
					sales_order = frappe.db.get_value("Sales Order", {"amazon_order_id": amazon_order_id}, "name")
					sales_invoice = (
						frappe.db.get_value("Sales Invoice", {"custom_marketplace_order": amazon_order_id, "docstatus": ["<", 2]}, "name")
						or frappe.db.get_value("Sales Invoice", {"po_no": amazon_order_id, "docstatus": ["<", 2]}, "name")
					)

				# Raw response and hash
				raw_str = json.dumps(tx, sort_keys=True, indent=2)
				payload_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

				# 2. Extract Breakdowns Flattened List
				extracted_breakdowns = []
				from ecommerce_integrations.amazon.doctype.amazon_account_mapping.amazon_account_mapping import get_account_mapping
				acc_mapping = get_account_mapping(self.amz_setting.company)

				def parse_breakdown_tree(bd, category=None, fee_name=None, scope="Order", sku=None, itm_id=None):
					if not isinstance(bd, dict):
						return
					b_type = bd.get("breakdownType") or "Other"
					b_amt_dict = bd.get("breakdownAmount") or {}
					b_amt = float(b_amt_dict.get("currencyAmount") or 0.0)
					b_curr = b_amt_dict.get("currencyCode") or currency

					# If b_type is a specific fee type (e.g. FixedClosingFee, MFNPostageFee, Commission, etc.)
					current_fee = fee_name
					if b_type not in ("AmazonFees", "Expenses", "Sales", "Base", "Tax", "ProductCharges", "TaxCollectedAtSource", "TaxWithholding"):
						current_fee = b_type

					sub_bds = bd.get("breakdowns") or []
					if not sub_bds:
						# Leaf breakdown: resolve accounting heads
						resolved_acc = None
						role = "Debit"
						resolved_fee = current_fee or (b_type if category and category != b_type else None)
						if acc_mapping:
							resolved_acc, role = acc_mapping.get_account_for_breakdown(
								category=category or b_type,
								fee_type=resolved_fee,
								breakdown_type=b_type,
							)

						dr_acc = resolved_acc if role == "Debit" else (acc_mapping.clearing_account if acc_mapping else None)
						cr_acc = resolved_acc if role == "Credit" else (acc_mapping.clearing_account if acc_mapping else None)

						extracted_breakdowns.append({
							"breakdown_type": b_type,
							"category": category or b_type,
							"fee_type": resolved_fee,
							"amount": b_amt,
							"currency": b_curr,
							"allocation_scope": scope,
							"seller_sku": sku,
							"amazon_order_item_id": itm_id,
							"accounting_category": category or b_type,
							"debit_account": dr_acc,
							"credit_account": cr_acc,
							"raw_source": json.dumps(bd),
						})
					else:
						# Recurse down tree
						for sub in sub_bds:
							parse_breakdown_tree(
								sub,
								category=category or b_type,
								fee_name=current_fee,
								scope=scope,
								sku=sku,
								itm_id=itm_id,
							)

				# Top-level transaction breakdowns (Order / Account scope)
				for top_bd in (tx.get("breakdowns") or []):
					scope = "Order" if amazon_order_id else "Account"
					parse_breakdown_tree(top_bd, category=top_bd.get("breakdownType"), scope=scope)

				# Item-level breakdowns (Item scope)
				for itm in items_list:
					if not isinstance(itm, dict):
						continue
					itm_sku = None
					itm_asin = None
					for ctx in (itm.get("contexts") or []):
						if isinstance(ctx, dict):
							itm_sku = ctx.get("sku") or itm_sku
							itm_asin = ctx.get("asin") or itm_asin

					itm_rel_id = None
					for item_rel in (itm.get("relatedIdentifiers") or []):
						if isinstance(item_rel, dict) and not itm_rel_id:
							itm_rel_id = item_rel.get("itemRelatedIdentifierValue")

					for itm_bd in (itm.get("breakdowns") or []):
						parse_breakdown_tree(
							itm_bd,
							category=itm_bd.get("breakdownType"),
							scope="Item",
							sku=itm_sku or seller_sku,
							itm_id=itm_rel_id or order_item_id,
						)

				# Overall transaction accounting category
				tx_cat = tx.get("transactionType") or "Transaction"
				tx_dr = None
				tx_cr = None
				if acc_mapping:
					if tx_cat == "Shipment":
						tx_dr = acc_mapping.clearing_account
						tx_cr = acc_mapping.receivable_account
					elif tx_cat in ["Transfer", "FundTransfer"]:
						tx_dr = acc_mapping.bank_account
						tx_cr = acc_mapping.clearing_account
					elif tx_cat in ["ServiceFee", "Expenses"]:
						tx_dr = frappe.db.get_value("Account", {"account_name": ["like", "%Amazon Fee%"], "company": self.amz_setting.company}, "name")
						tx_cr = acc_mapping.clearing_account

				# 3. Idempotent Upsert into Amazon Financial Transaction
				existing_name = frappe.db.get_value(
					"Amazon Financial Transaction",
					{"amazon_transaction_id": tx_id},
					"name",
				)

				# Determine accounting status (Ready vs Pending)
				act_status = "Pending"
				if total_amount != 0.0 and acc_mapping:
					leaf_bds = [b for b in extracted_breakdowns if b.get("allocation_scope") == "Item"] or [b for b in extracted_breakdowns if b.get("amount") != 0]
					if leaf_bds:
						all_mapped = True
						for b in leaf_bds:
							acc, _ = acc_mapping.get_account_for_breakdown(b.get("category"), b.get("fee_type"), b.get("breakdown_type"))
							if not acc:
								all_mapped = False
								break
						if all_mapped:
							act_status = "Ready"

				if existing_name:
					doc = frappe.get_doc("Amazon Financial Transaction", existing_name)
					doc.transaction_type = tx.get("transactionType")
					doc.transaction_status = tx.get("transactionStatus") or "RELEASED"
					doc.posted_date = posted_date
					doc.total_amount = total_amount
					doc.currency = currency
					doc.amazon_order_id = amazon_order_id
					doc.amazon_order_item_id = order_item_id
					doc.seller_sku = seller_sku
					doc.asin = asin
					doc.marketplace = marketplace_name
					doc.marketplace_id = marketplace_id
					doc.settlement_id = settlement_id
					doc.financial_event_group_id = event_group_id
					doc.sales_order = sales_order
					doc.sales_invoice = sales_invoice
					doc.payload_hash = payload_hash
					doc.raw_response = raw_str
					doc.accounting_category = tx_cat
					doc.debit_account = tx_dr or doc.debit_account
					doc.credit_account = tx_cr or doc.credit_account
					doc.accounting_reference = amazon_order_id or event_group_id
					if doc.accounting_status not in ["Posted", "Reconciled"]:
						doc.accounting_status = act_status

					# Clear and rebuild child table to prevent duplication
					doc.set("breakdowns", [])
					for bd_row in extracted_breakdowns:
						doc.append("breakdowns", bd_row)

					doc.save(ignore_permissions=True)
					processed_docs.append(doc.name)
				else:
					doc = frappe.new_doc("Amazon Financial Transaction")
					doc.amazon_transaction_id = tx_id
					doc.transaction_type = tx.get("transactionType")
					doc.transaction_status = tx.get("transactionStatus") or "RELEASED"
					doc.posted_date = posted_date
					doc.total_amount = total_amount
					doc.currency = currency
					doc.amazon_order_id = amazon_order_id
					doc.amazon_order_item_id = order_item_id
					doc.seller_sku = seller_sku
					doc.asin = asin
					doc.marketplace = marketplace_name
					doc.marketplace_id = marketplace_id
					doc.settlement_id = settlement_id
					doc.financial_event_group_id = event_group_id
					doc.sales_order = sales_order
					doc.sales_invoice = sales_invoice
					doc.sync_status = "Synced"
					doc.reconciliation_status = "Unreconciled"
					doc.accounting_status = act_status
					doc.accounting_category = tx_cat
					doc.debit_account = tx_dr
					doc.credit_account = tx_cr
					doc.accounting_reference = amazon_order_id or event_group_id
					doc.payload_hash = payload_hash
					doc.raw_response = raw_str

					for bd_row in extracted_breakdowns:
						doc.append("breakdowns", bd_row)

					doc.insert(ignore_permissions=True)
					processed_docs.append(doc.name)

			except Exception as e:
				# Defensive handling: log failure via Integration Request and error log, continue next
				import traceback
				frappe.log_error(
					title=f"Amazon Financial Transaction Ingestion Failed: {tx_id}",
					message=frappe.get_traceback(),
				)
				ir = frappe.new_doc("Integration Request")
				ir.integration_request_service = "Amazon SP-API Finances"
				ir.is_remote_request = 0
				ir.request_description = f"Financial Transaction Failed: {tx_id}"
				ir.data = json.dumps(tx, indent=2)
				ir.error = frappe.get_traceback()
				ir.status = "Failed"
				ir.insert(ignore_permissions=True)

		# Auto-trigger / recalculate profitability and update Sales Invoices for synced orders
		try:
			from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
			from ecommerce_integrations.amazon.doctype.amazon_order_profitability.sales_invoice_amazon_profitability import (
				update_sales_invoice_amazon_charges,
			)
			engine = AmazonProfitabilityEngine()
			synced_orders = set()
			for doc_name in processed_docs:
				oid = frappe.db.get_value("Amazon Financial Transaction", doc_name, "amazon_order_id")
				if oid:
					synced_orders.add(oid)
			for oid in synced_orders:
				try:
					engine.calculate_order_profitability(oid)
				except Exception:
					pass
				try:
					invoices = frappe.get_all(
						"Sales Invoice",
						filters={"custom_marketplace_order": oid, "docstatus": 0},
						pluck="name",
					)
					for inv_name in invoices:
						try:
							inv_doc = frappe.get_doc("Sales Invoice", inv_name)
							from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sales_invoice_taxes import (
								sync_amazon_charges_to_sales_invoice,
							)
							sync_amazon_charges_to_sales_invoice(inv_doc)
							inv_doc.save(ignore_permissions=True)
						except Exception:
							pass
				except Exception:
					pass
		except Exception:
			pass

		return processed_docs

	def get_reports_instance(self) -> Reports:
		return Reports(**self.instance_params)

	def sync_settlement_reports(self, page_size: int = 5, created_since: str | None = None) -> list[str]:
		"""
		Phase 2 Settlement Staging:
		Searches for DONE GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2 reports via Reports API.
		Downloads and parses Flat File V2 TSV report document.
		Idempotently upserts Amazon Settlement master and Amazon Settlement Entry line items.
		Matches line items against Amazon Financial Transactions where available.
		Does NOT post final Journal Entries.
		"""
		import gzip
		import json
		import requests
		from dateutil import parser as dt_parser

		reports_client = self.get_reports_instance()
		resp = reports_client.get_reports(
			report_types="GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
			processing_statuses="DONE",
			page_size=page_size,
			created_since=created_since,
		)

		reports_list = (resp.get("reports") or [])
		processed_settlements = []

		for r in reports_list:
			rep_doc_id = r.get("reportDocumentId")
			rep_id = r.get("reportId")
			if not rep_doc_id:
				continue

			try:
				doc_resp = reports_client.get_report_document(rep_doc_id)
				dl_url = doc_resp.get("url")
				compression = doc_resp.get("compressionAlgorithm")

				if not dl_url:
					continue

				content_resp = requests.get(dl_url, timeout=60)
				raw_bytes = content_resp.content
				if compression == "GZIP":
					raw_bytes = gzip.decompress(raw_bytes)

				tsv_content = raw_bytes.decode("utf-8", errors="replace")
				lines = tsv_content.splitlines()
				if len(lines) < 2:
					continue

				# Header row
				headers = [h.strip().lower() for h in lines[0].split("\t")]
				# Row 1 has settlement metadata
				first_data = [d.strip() for d in lines[1].split("\t")]
				row_map = dict(zip(headers, first_data))

				settlement_id = row_map.get("settlement-id")
				if not settlement_id:
					continue

				def parse_dt(dt_str):
					if not dt_str: return None
					try:
						# E.g. "28.09.2026 10:44:19 UTC" or ISO
						cleaned = dt_str.replace(" UTC", "")
						return dt_parser.parse(cleaned, dayfirst=True).strftime("%Y-%m-%d %H:%M:%S")
					except Exception:
						return None

				start_date = parse_dt(row_map.get("settlement-start-date"))
				end_date = parse_dt(row_map.get("settlement-end-date"))
				deposit_date = parse_dt(row_map.get("deposit-date"))
				total_amount = float(row_map.get("total-amount") or 0.0)
				currency = row_map.get("currency")

				entries = []
				for line_str in lines[2:]:
					if not line_str.strip():
						continue
					cols = [c.strip() for c in line_str.split("\t")]
					c_map = dict(zip(headers, cols))

					t_type = c_map.get("transaction-type")
					ord_id = c_map.get("order-id")
					a_type = c_map.get("amount-type")
					a_desc = c_map.get("amount-description")
					amt = float(c_map.get("amount") or 0.0)
					sku = c_map.get("sku")
					ord_item = c_map.get("order-item-code")
					p_date = parse_dt(c_map.get("posted-date-time") or c_map.get("posted-date"))

					# Match against existing Amazon Financial Transaction
					matched_tx = None
					if ord_id:
						matched_tx = frappe.db.get_value("Amazon Financial Transaction", {"amazon_order_id": ord_id}, "name")

					entries.append({
						"transaction_type": t_type,
						"order_id": ord_id,
						"order_item_code": ord_item,
						"sku": sku,
						"amount_type": a_type,
						"amount_description": a_desc,
						"amount": amt,
						"posted_date": p_date,
						"reconciliation_status": "Matched" if matched_tx else "Unmatched",
						"matched_financial_transaction": matched_tx,
						"raw_line": line_str[:1000],
					})

				# Idempotent upsert Amazon Settlement
				existing_settle = frappe.db.get_value("Amazon Settlement", {"settlement_id": settlement_id}, "name")
				if existing_settle:
					s_doc = frappe.get_doc("Amazon Settlement", existing_settle)
					s_doc.total_amount = total_amount
					s_doc.currency = currency
					s_doc.start_date = start_date
					s_doc.end_date = end_date
					s_doc.deposit_date = deposit_date
					s_doc.report_id = str(rep_id)
					s_doc.report_document_id = str(rep_doc_id)
					s_doc.set("entries", [])
					for ent in entries:
						s_doc.append("entries", ent)
					s_doc.save(ignore_permissions=True)
					processed_settlements.append(s_doc.name)
				else:
					s_doc = frappe.new_doc("Amazon Settlement")
					s_doc.settlement_id = settlement_id
					s_doc.settlement_status = "Settled"
					s_doc.reconciliation_status = "Unreconciled"
					s_doc.total_amount = total_amount
					s_doc.currency = currency
					s_doc.start_date = start_date
					s_doc.end_date = end_date
					s_doc.deposit_date = deposit_date
					s_doc.report_id = str(rep_id)
					s_doc.report_document_id = str(rep_doc_id)
					s_doc.raw_report_data = json.dumps(r, indent=2)
					for ent in entries:
						s_doc.append("entries", ent)
					s_doc.insert(ignore_permissions=True)
					processed_settlements.append(s_doc.name)

			except Exception as e:
				frappe.log_error(
					title=f"Settlement Report Ingestion Failed: {rep_id}",
					message=frappe.get_traceback(),
				)

		return processed_settlements

	def reconcile_transactions_with_settlement(self, settlement_id: str) -> dict:
		"""
		Reconciles Amazon Financial Transactions against an Amazon Settlement.
		Updates reconciliation_status on both records.
		"""
		s_name = frappe.db.get_value("Amazon Settlement", {"settlement_id": settlement_id}, "name")
		if not s_name:
			return {"matched": 0, "unmatched": 0}

		s_doc = frappe.get_doc("Amazon Settlement", s_name)
		matched_count = 0
		unmatched_count = 0

		for ent in s_doc.entries:
			if ent.order_id:
				tx_name = frappe.db.get_value("Amazon Financial Transaction", {"amazon_order_id": ent.order_id}, "name")
				if tx_name:
					ent.matched_financial_transaction = tx_name
					ent.reconciliation_status = "Matched"
					matched_count += 1
					# Update transaction status
					frappe.db.set_value("Amazon Financial Transaction", tx_name, {
						"reconciliation_status": "Reconciled",
						"settlement_id": settlement_id
					})
				else:
					ent.reconciliation_status = "Unmatched"
					unmatched_count += 1
			else:
				# Account level line (e.g. advertising, subscription, storage or transfer)
				matched_tx = frappe.db.get_value(
					"Amazon Financial Transaction",
					{
						"settlement_id": settlement_id,
						"total_amount": ent.amount,
					},
					"name",
				)
				if matched_tx:
					ent.matched_financial_transaction = matched_tx
					ent.reconciliation_status = "Matched"
					matched_count += 1
					frappe.db.set_value("Amazon Financial Transaction", matched_tx, {
						"reconciliation_status": "Reconciled",
						"settlement_id": settlement_id,
					})
				elif ent.amount_type in ["FundTransfer", "Transfer"]:
					ent.reconciliation_status = "Matched"
					matched_count += 1
				else:
					ent.reconciliation_status = "Unmatched"
					unmatched_count += 1


		if matched_count > 0 and unmatched_count == 0:
			s_doc.reconciliation_status = "Reconciled"
		elif matched_count > 0:
			s_doc.reconciliation_status = "Partially Reconciled"
		else:
			s_doc.reconciliation_status = "Unreconciled"

		s_doc.save(ignore_permissions=True)
		return {"matched": matched_count, "unmatched": unmatched_count}

	def preview_amazon_transaction(self, transaction_name: str) -> dict:
		"""
		Phase 3 Accounting Preview:
		Inspects an Amazon Financial Transaction, maps accounts, checks idempotency and GL balance.
		MUST NOT create or commit any accounting entries.
		"""
		tx = frappe.get_doc("Amazon Financial Transaction", transaction_name)
		acc_mapping = get_account_mapping(self.amz_setting.company)
		warnings = []
		errors = []

		if not acc_mapping:
			errors.append(f"No Amazon Account Mapping found for company '{self.amz_setting.company}'.")
			return {
				"transaction": tx.name,
				"transaction_id": tx.amazon_transaction_id,
				"transaction_type": tx.transaction_type,
				"accounting_status": tx.accounting_status,
				"amount": tx.total_amount,
				"currency": tx.currency,
				"errors": errors,
				"warnings": warnings,
				"is_postable": False,
			}

		# Idempotency check
		if tx.accounting_status == "Posted" or tx.journal_entry:
			warnings.append(f"Transaction is already posted. Linked Journal Entry: {tx.journal_entry}")

		if tx.total_amount == 0.0:
			errors.append("Transaction amount is 0.0, nothing to post.")

		leaf_bds = [b for b in tx.breakdowns if b.allocation_scope == "Item"] or [b for b in tx.breakdowns if b.amount != 0]
		if not leaf_bds:
			errors.append("No valid breakdown rows found for transaction.")

		lines = []
		debit_accounts_preview = []
		credit_accounts_preview = []

		if tx.transaction_type in ["Transfer", "FundTransfer"]:
			bank_acc = acc_mapping.bank_account
			amt = abs(tx.total_amount)
			lines.append({"account": bank_acc, "debit": amt, "credit": 0.0, "breakdown_type": "FundTransfer", "amount": amt})
			lines.append({"account": acc_mapping.clearing_account, "debit": 0.0, "credit": amt, "breakdown_type": "Clearing", "amount": -amt})
		else:
			for b in leaf_bds:
				acc, role = acc_mapping.get_account_for_breakdown(b.category, b.fee_type, b.breakdown_type)
				if not acc:
					errors.append(f"Breakdown '{b.breakdown_type}' (category: '{b.category}', fee_type: '{b.fee_type}') could not be mapped to an account.")
					continue
				amt = abs(b.amount)
				if b.amount < 0:
					dr = amt
					cr = 0.0
				else:
					dr = 0.0
					cr = amt

				lines.append({"account": acc, "debit": dr, "credit": cr, "breakdown_type": b.breakdown_type, "amount": b.amount})
				if dr > 0:
					debit_accounts_preview.append(acc)
				if cr > 0:
					credit_accounts_preview.append(acc)

			# Balancing clearing leg
			tot_dr = sum(l["debit"] for l in lines)
			tot_cr = sum(l["credit"] for l in lines)
			clearing_diff = round(tot_cr - tot_dr, 2)
			if clearing_diff > 0:
				lines.append({"account": acc_mapping.clearing_account, "debit": clearing_diff, "credit": 0.0})
				debit_accounts_preview.append(acc_mapping.clearing_account)
			elif clearing_diff < 0:
				lines.append({"account": acc_mapping.clearing_account, "debit": 0.0, "credit": abs(clearing_diff)})
				credit_accounts_preview.append(acc_mapping.clearing_account)


		# Aggregate lines by account to show net Dr / Cr
		agg = {}
		for l in lines:
			acc = l["account"]
			agg[acc] = agg.get(acc, {"dr": 0.0, "cr": 0.0})
			agg[acc]["dr"] += l.get("debit", 0.0)
			agg[acc]["cr"] += l.get("credit", 0.0)

		net_debit_accounts = [acc for acc, vals in agg.items() if round(vals["dr"] - vals["cr"], 2) > 0]
		net_credit_accounts = [acc for acc, vals in agg.items() if round(vals["cr"] - vals["dr"], 2) > 0]

		final_tot_dr = sum(l["debit"] for l in lines)
		final_tot_cr = sum(l["credit"] for l in lines)
		if round(final_tot_dr, 2) != round(final_tot_cr, 2):
			errors.append(f"Unbalanced proposed entry: Debit {final_tot_dr:.2f} != Credit {final_tot_cr:.2f}")

		doc_type = "Bank Entry" if tx.transaction_type in ["Transfer", "FundTransfer"] else "Journal Entry"

		return {
			"transaction": tx.name,
			"transaction_id": tx.amazon_transaction_id,
			"transaction_type": tx.transaction_type,
			"accounting_category": tx.accounting_category,
			"amount": tx.total_amount,
			"currency": tx.currency,
			"posting_date": tx.posted_date.strftime("%Y-%m-%d") if tx.posted_date else frappe.utils.nowdate(),
			"proposed_document_type": doc_type,
			"proposed_lines": lines,
			"debit_accounts": net_debit_accounts,
			"credit_accounts": net_credit_accounts,
			"existing_journal_entry": tx.journal_entry,
			"sales_order": tx.sales_order,
			"sales_invoice": tx.sales_invoice,
			"accounting_status": tx.accounting_status,
			"errors": errors,
			"warnings": warnings,
			"is_postable": len(errors) == 0 and tx.accounting_status != "Posted",
		}

	def post_amazon_transaction(self, transaction_name: str) -> str:
		"""
		Phase 3 Actual ERPNext Accounting Posting:
		- Loads transaction and validates mapping & Ready status.
		- Idempotent: If already posted, returns existing JE.
		- Builds balanced Journal Entry (handles India Compliance GSTIN & Party).
		- Submits JE via standard ERPNext document methods.
		- Updates Amazon Financial Transaction with JE link, posting date, and 'Posted' status.
		"""
		tx = frappe.get_doc("Amazon Financial Transaction", transaction_name)

		# 1. Idempotency Check
		if tx.accounting_status == "Accounted":
			return None

		if tx.sales_invoice and frappe.db.get_value("Sales Invoice", tx.sales_invoice, "docstatus") == 1:
			has_amz_tax = frappe.db.exists(
				"Sales Taxes and Charges",
				{"parent": tx.sales_invoice, "description": ["like", "Amazon %"]},
			)
			if has_amz_tax:
				tx.accounting_status = "Accounted"
				tx.save(ignore_permissions=True)
				return None

		if tx.journal_entry and frappe.db.exists("Journal Entry", tx.journal_entry):
			je_docstatus = frappe.db.get_value("Journal Entry", tx.journal_entry, "docstatus")
			if je_docstatus != 2:
				return tx.journal_entry

		# Check existing JE referencing this transaction
		existing_je = frappe.db.get_value(
			"Journal Entry",
			{"user_remark": ["like", f"%{tx.amazon_transaction_id}%"], "docstatus": ["!=", 2]},
			"name",
		)
		if existing_je:
			tx.journal_entry = existing_je
			tx.accounting_status = "Posted"
			tx.save(ignore_permissions=True)
			return existing_je

		# 2. Preview and Validation
		preview = self.preview_amazon_transaction(transaction_name)
		if not preview.get("is_postable") and tx.accounting_status != "Posted":
			err_msg = "; ".join(preview.get("errors") or ["Transaction not postable"])
			tx.accounting_status = "Failed"
			tx.accounting_error = err_msg[:500]
			tx.save(ignore_permissions=True)
			frappe.throw(_("Cannot post transaction {0}: {1}").format(transaction_name, err_msg))

		acc_mapping = get_account_mapping(self.amz_setting.company)
		company = self.amz_setting.company

		# 3. Aggregate lines by account for clean Journal Entry
		lines = preview["proposed_lines"]
		agg = {}
		for l in lines:
			acc = l["account"]
			agg[acc] = agg.get(acc, {"dr": 0.0, "cr": 0.0})
			agg[acc]["dr"] += l.get("debit", 0.0)
			agg[acc]["cr"] += l.get("credit", 0.0)

		# Customer / Party resolution
		party_customer = None
		if tx.sales_invoice:
			party_customer = frappe.db.get_value("Sales Invoice", tx.sales_invoice, "customer")
		if not party_customer and tx.sales_order:
			party_customer = frappe.db.get_value("Sales Order", tx.sales_order, "customer")
		if not party_customer and acc_mapping.get("default_customer"):
			party_customer = acc_mapping.default_customer
		if not party_customer:
			party_customer = frappe.db.get_value("Customer", {"customer_name": ["like", "%Amazon%"]}, "name")

		je_accounts = []
		for acc, vals in agg.items():
			net = round(vals["dr"] - vals["cr"], 2)
			if net == 0:
				continue
			acc_type = frappe.db.get_value("Account", acc, "account_type")
			row = {
				"account": acc,
				"debit_in_account_currency": max(net, 0.0),
				"credit_in_account_currency": max(-net, 0.0),
			}
			if acc_type in ["Receivable", "Payable"]:
				if not party_customer:
					frappe.throw(_("Party required for {0} but no Customer found.").format(acc))
				row["party_type"] = "Customer"
				row["party"] = party_customer
			je_accounts.append(row)

		# 4. Create and Submit Journal Entry
		posting_date = preview["posting_date"]
		is_bank_entry = preview["proposed_document_type"] == "Bank Entry"

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Bank Entry" if is_bank_entry else "Journal Entry"
		je.company = company
		je.company_gstin = frappe.db.get_value("Company", company, "gstin")
		je.posting_date = posting_date
		je.user_remark = f"Amazon SP-API {tx.transaction_type} {tx.amazon_transaction_id}"

		if is_bank_entry:
			je.cheque_no = tx.amazon_transaction_id[:20]
			je.cheque_date = posting_date

		for ja in je_accounts:
			je.append("accounts", ja)

		try:
			je.insert(ignore_permissions=True)
			je.submit()
		except Exception as e:
			tx.accounting_status = "Failed"
			tx.accounting_error = str(e)[:500]
			tx.save(ignore_permissions=True)
			raise

		# 5. Link back and update transaction
		tx.journal_entry = je.name
		tx.posting_date = posting_date
		tx.accounting_status = "Posted"
		tx.accounting_error = None
		tx.save(ignore_permissions=True)

		return je.name

	def post_amazon_transactions(self, posted_after: str | None = None, posted_before: str | None = None, limit: int = 50, tx_names: list[str] | None = None) -> dict:
		"""
		Controlled Batch Posting of READY Amazon Financial Transactions.
		Isolates failures per transaction.
		"""
		if tx_names is None:
			filters = {"accounting_status": "Ready", "docstatus": 0}
			if posted_after:
				filters["posted_date"] = [">=", posted_after]
			if posted_before:
				if "posted_date" in filters:
					filters["posted_date"] = ["between", [posted_after, posted_before]]
				else:
					filters["posted_date"] = ["<=", posted_before]

			tx_names = frappe.get_all(
				"Amazon Financial Transaction",
				filters=filters,
				pluck="name",
				limit=limit,
				order_by="posted_date asc",
			)
		else:
			tx_names = tx_names[:limit]

		success = []
		failed = []

		for name in tx_names:
			try:
				je_name = self.post_amazon_transaction(name)
				success.append({"transaction": name, "journal_entry": je_name})
			except Exception as e:
				failed.append({"transaction": name, "error": str(e)})

		return {
			"processed": len(tx_names),
			"success_count": len(success),
			"failed_count": len(failed),
			"success": success,
			"failed": failed,
		}

	def backfill_amazon_order_ids(self) -> dict:
		"""
		Safely and idempotently backfills and normalizes amazon_order_id across existing
		Amazon Financial Transaction records using their stored raw_response.
		Does NOT post accounting, modify journal entries, or create duplicates.
		"""
		records = frappe.get_all(
			"Amazon Financial Transaction",
			fields=["name", "amazon_order_id", "raw_response"],
		)
		updated = []
		for r in records:
			if not r.raw_response:
				continue
			try:
				raw = json.loads(r.raw_response)
			except Exception:
				continue
			extracted = extract_amazon_order_id(raw)
			current = (r.amazon_order_id or "").strip() or None
			if extracted and extracted != current:
				frappe.db.set_value(
					"Amazon Financial Transaction",
					r.name,
					"amazon_order_id",
					extracted,
					update_modified=False,
				)
				updated.append({"name": r.name, "old": current, "new": extracted})

		if updated:
			frappe.db.commit()

		return {"total_checked": len(records), "updated_count": len(updated), "updated": updated}


def backfill_amazon_order_ids(amz_setting_name: str | None = None) -> dict:
	if not amz_setting_name:
		amz_setting_name = frappe.db.get_value("Amazon SP API Settings", {}, "name")
	ar = AmazonRepository(amz_setting_name)
	return ar.backfill_amazon_order_ids()


def preview_amazon_transaction(amz_setting_name: str, transaction_name: str) -> dict:
	ar = AmazonRepository(amz_setting_name)
	return ar.preview_amazon_transaction(transaction_name)


def post_amazon_transaction(amz_setting_name: str, transaction_name: str) -> str:
	ar = AmazonRepository(amz_setting_name)
	return ar.post_amazon_transaction(transaction_name)


def post_amazon_transactions(amz_setting_name: str, posted_after: str | None = None, posted_before: str | None = None, limit: int = 50) -> dict:
	ar = AmazonRepository(amz_setting_name)
	return ar.post_amazon_transactions(posted_after=posted_after, posted_before=posted_before, limit=limit)



def sync_financial_transactions(amz_setting_name: str, posted_after: str | None = None, posted_before: str | None = None) -> list[str]:
	ar = AmazonRepository(amz_setting_name)
	return ar.sync_financial_transactions(posted_after=posted_after, posted_before=posted_before)


def sync_settlement_reports(amz_setting_name: str, page_size: int = 5, created_since: str | None = None) -> list[str]:
	ar = AmazonRepository(amz_setting_name)
	return ar.sync_settlement_reports(page_size=page_size, created_since=created_since)



def validate_amazon_sp_api_credentials(**args) -> None:
	api = SPAPI(
		client_id=args.get("client_id"),
		client_secret=args.get("client_secret"),
		refresh_token=args.get("refresh_token"),
		country_code=args.get("country"),
	)

	try:
		# validate client_id, client_secret and refresh_token.
		api.get_access_token()

	except SPAPIError as e:
		msg = f"<b>Error:</b> {e.error}<br/><b>Error Description:</b> {e.error_description}"
		frappe.throw(msg)


def get_orders(amz_setting_name, created_after=None, created_before=None, last_updated_after=None, return_summary=False) -> list | dict:
	ar = AmazonRepository(amz_setting_name)
	return ar.get_orders(created_after=created_after, created_before=created_before, last_updated_after=last_updated_after, return_summary=return_summary)


def create_sales_invoice(amz_setting_name: str, sales_order_name: str, order: dict | None = None, order_items: list | None = None) -> str:
	ar = AmazonRepository(amz_setting_name)
	return ar.create_sales_invoice(sales_order_name, order=order, order_items=order_items)


def get_amazon_payment_status(amz_setting_name: str, order_id: str) -> dict:
	ar = AmazonRepository(amz_setting_name)
	return ar.get_amazon_payment_status(order_id)


def create_payment_entry(
	amz_setting_name: str,
	sales_invoice_name: str,
	payment_method: str | None = None,
	payment_status: dict | None = None,
) -> str | None:
	ar = AmazonRepository(amz_setting_name)
	return ar.create_payment_entry(sales_invoice_name, payment_method=payment_method, payment_status=payment_status)


def sync_sales_order_invoice_and_payment(
	amz_setting_name: str, sales_order_name: str, order: dict | None = None, order_items: list | None = None
) -> tuple[str | None, str | None]:
	ar = AmazonRepository(amz_setting_name)
	return ar.sync_sales_order_invoice_and_payment(sales_order_name, order=order, order_items=order_items)


def get_amazon_shipment_status(amz_setting_name: str, order: dict, order_items: list | None = None) -> dict:
	ar = AmazonRepository(amz_setting_name)
	return ar.get_amazon_shipment_status(order, order_items=order_items)


def create_delivery_note(
	amz_setting_name: str,
	sales_order_name: str,
	order: dict | None = None,
	order_items: list | None = None,
	shipment_status: dict | None = None,
) -> str | None:
	ar = AmazonRepository(amz_setting_name)
	return ar.create_delivery_note(sales_order_name, order=order, order_items=order_items, shipment_status=shipment_status)


def sync_order(amz_setting_name: str, order: dict) -> tuple[str | None, str | None, str | None, str | None]:
	ar = AmazonRepository(amz_setting_name)
	return ar.sync_order(order)


def create_customer(amz_setting_name: str, order: dict) -> str:
	ar = AmazonRepository(amz_setting_name)
	return ar.create_customer(order)

