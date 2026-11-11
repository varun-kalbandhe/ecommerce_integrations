# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


from requests import request

__all__ = [
	"SPAPIError",
	"Finances",
	"FinancesV20240619",
	"Orders",
	"CatalogItems",
	"ListingsItems",
	"ProductFees",
	"Reports",
]


# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#selling-partner-api-endpoints
MARKETPLACES = {
	"North America": {
		"CA": "A2EUQ1WTGCTBG2",
		"US": "ATVPDKIKX0DER",
		"MX": "A1AM78C64UM0Y8",
		"BR": "A2Q3Y263D00KWC",
		"AWS Region": "us-east-1",
		"Endpoint": "https://sellingpartnerapi-na.amazon.com",
	},
	"Europe": {
		"ES": "A1RKKUPIHCS9HS",
		"GB": "A1F83G8C2ARO7P",
		"FR": "A13V1IB3VIYZZH",
		"NL": "A1805IZSGTT6HS",
		"DE": "A1PA6795UKMFR9",
		"IT": "APJ6JRA9NG5V4",
		"SE": "A2NODRKZP88ZB9",
		"PL": "A1C3SOZRARQ6R3",
		"EG": "ARBP9OOSHTCHU",
		"TR": "A33AVAJ2PDY3EV",
		"SA": "A17E79C6D8DWNP",
		"AE": "A2VIGQ35RCS4UG",
		"IN": "A21TJRUUN4KGV",
		"AWS Region": "eu-west-1",
		"Endpoint": "https://sellingpartnerapi-eu.amazon.com",
	},
	"Far East": {
		"SG": "A19VAU5U5O7RUS",
		"AU": "A39IBJ37TRP1C6",
		"JP": "A1VC38T7YXB528",
		"AWS Region": "us-west-2",
		"Endpoint": "https://sellingpartnerapi-fe.amazon.com",
	},
}




class SPAPIError(Exception):
	"""
	Main SP-API Exception class
	"""

	def __init__(self, *args, **kwargs) -> None:
		self.error = kwargs.get("error", "-")
		self.error_description = kwargs.get("error_description", "-")
		super().__init__(f"{self.error}: {self.error_description}")


class SPAPI:
	"""Base Amazon SP-API class"""

	# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#connecting-to-the-selling-partner-api
	AUTH_URL = "https://api.amazon.com/auth/o2/token"

	BASE_URI = "/"

	def __init__(
		self,
		client_id: str,
		client_secret: str,
		refresh_token: str,
		country_code: str = "US",
	) -> None:
		self.client_id = client_id
		self.client_secret = client_secret
		self.refresh_token = refresh_token
		self.country_code = country_code
		self.region, self.endpoint, self.marketplace_id = Util.get_marketplace_data(country_code)

	def get_access_token(self) -> str:
		data = {
			"grant_type": "refresh_token",
			"client_id": self.client_id,
			"client_secret": self.client_secret,
			"refresh_token": self.refresh_token,
		}

		response = request(method="POST", url=self.AUTH_URL, data=data)
		result = response.json()
		if response.status_code == 200:
			return result.get("access_token")
		exception = SPAPIError(error=result.get("error"), error_description=result.get("error_description"))
		raise exception



	def get_headers(self) -> dict:
		return {"x-amz-access-token": self.get_access_token()}

	def make_request(
		self,
		method: str = "GET",
		append_to_base_uri: str = "",
		params: dict | None = None,
		data: dict | None = None,
	) -> dict:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		url = self.endpoint + self.BASE_URI + append_to_base_uri

		headers = self.get_headers()
		if data and isinstance(data, str) and "content-type" not in [k.lower() for k in headers.keys()]:
			headers["content-type"] = "application/json"

		response = request(
			method=method,
			url=url,
			params=params,
			data=data,
			headers=headers,
		)
		result = response.json()
		if "errors" in result:
			error = result["errors"][0]
			raise SPAPIError(error=error.get("code"), error_description=error.get("message"))
		return result

	def list_to_dict(self, key: str, values: list, data: dict) -> None:
		if values and isinstance(values, list):
			for idx in range(len(values)):
				data[f"{key}[{idx}]"] = values[idx]

	def list_to_comma_str(self, key: str, values: list | str | None, data: dict) -> None:
		if values:
			if isinstance(values, list):
				val_str = ",".join(str(v) for v in values if v is not None and str(v).strip())
				if val_str:
					data[key] = val_str
			elif isinstance(values, str) and values.strip():
				data[key] = values.strip()


class FinancesV20240619(SPAPI):
	"""Amazon Finances API v2024-06-19"""

	BASE_URI = "/finances/2024-06-19/"

	def list_transactions(
		self,
		related_identifier_name: str | None = None,
		related_identifier_value: str | None = None,
		posted_after: str | None = None,
		posted_before: str | None = None,
		next_token: str | None = None,
	) -> dict:
		"""Returns transactions filtered by identifier (e.g. ORDER_ID) or date range."""
		data = dict(
			relatedIdentifierName=related_identifier_name,
			relatedIdentifierValue=related_identifier_value,
			postedAfter=posted_after,
			postedBefore=posted_before,
			nextToken=next_token,
		)
		return self.make_request(append_to_base_uri="transactions", params=data)

	def get_transactions(
		self,
		posted_after: str | None = None,
		posted_before: str | None = None,
		max_pages: int | None = None,
		max_retries: int = 3,
		retry_delay: float = 2.0,
	) -> list[dict]:
		"""
		Fetches all transactions in date range [posted_after, posted_before] across all pages.
		Handles nextToken pagination, rate-limit backoff (429 / QuotaExceeded), and errors defensively.
		Returns a list of raw transaction dictionaries.
		"""
		import time

		all_transactions = []
		next_token = None
		page_count = 0

		while True:
			params = {
				"posted_after": posted_after if not next_token else None,
				"posted_before": posted_before if not next_token else None,
				"next_token": next_token,
			}

			# Rate limit / retry loop per page
			resp = None
			last_err = None
			for attempt in range(max_retries):
				try:
					resp = self.list_transactions(**params)
					break
				except SPAPIError as e:
					last_err = e
					# Check for rate limit / throttle indicators
					err_code = str(getattr(e, "error", "")).lower()
					err_desc = str(getattr(e, "error_description", "")).lower()
					is_rate_limit = any(term in err_code or term in err_desc for term in ["429", "throttl", "quota", "ratelimit"])
					if is_rate_limit and attempt < max_retries - 1:
						backoff = retry_delay * (2 ** attempt)
						time.sleep(backoff)
						continue
					elif attempt < max_retries - 1:
						time.sleep(retry_delay)
						continue
					else:
						raise last_err

			if not resp:
				break

			payload = resp.get("payload") or {}
			transactions = payload.get("transactions") or []
			all_transactions.extend(transactions)

			page_count += 1
			if max_pages and page_count >= max_pages:
				break

			next_token = payload.get("nextToken")
			if not next_token:
				break

		return all_transactions


class Finances(SPAPI):
	"""Amazon Finances API"""

	BASE_URI = "/finances/v0/"

	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int | None = None, next_token: str | None = None
	) -> dict:
		"""Returns all financial events for the specified order."""
		append_to_base_uri = f"orders/{order_id}/financialEvents"
		data = dict(MaxResultsPerPage=max_results, NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)

	def list_transactions(
		self,
		related_identifier_name: str | None = None,
		related_identifier_value: str | None = None,
		posted_after: str | None = None,
		posted_before: str | None = None,
		next_token: str | None = None,
	) -> dict:
		v2024 = FinancesV20240619(
			client_id=self.client_id,
			client_secret=self.client_secret,
			refresh_token=self.refresh_token,
			country_code=self.country_code,
		)
		return v2024.list_transactions(
			related_identifier_name=related_identifier_name,
			related_identifier_value=related_identifier_value,
			posted_after=posted_after,
			posted_before=posted_before,
			next_token=next_token,
		)


class Orders(SPAPI):
	"""Amazon Orders API"""

	BASE_URI = "/orders/v0/orders"

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
	) -> dict:
		"""Returns orders created or updated during the time frame indicated by the specified parameters. You can also apply a range of filtering criteria to narrow the list of orders returned. If NextToken is present, that will be used to retrieve the orders instead of other criteria."""
		data = dict(
			CreatedAfter=created_after,
			CreatedBefore=created_before,
			LastUpdatedAfter=last_updated_after,
			LastUpdatedBefore=last_updated_before,
			BuyerEmail=buyer_email,
			SellerOrderId=seller_order_id,
			MaxResultsPerPage=max_results,
			NextToken=next_token,
			ActualFulfillmentSupplySourceId=actual_fulfillment_supply_source_id,
			IsISPU=is_ispu,
			StoreChainStoreId=store_chain_store_id,
		)
		data = Util.remove_empty(data)

		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]

		self.list_to_comma_str("OrderStatuses", order_statuses, data)
		self.list_to_comma_str("MarketplaceIds", marketplace_ids, data)
		self.list_to_comma_str("FulfillmentChannels", fulfillment_channels, data)
		self.list_to_comma_str("PaymentMethods", payment_methods, data)
		self.list_to_comma_str("EasyShipShipmentStatuses", easyship_shipment_statuses, data)
		self.list_to_comma_str("AmazonOrderIds", amazon_order_ids, data)

		return self.make_request(params=data)

	def get_order(self, order_id: str) -> dict:
		"""Returns the order that you specify."""
		append_to_base_uri = f"/{order_id}"
		return self.make_request(append_to_base_uri=append_to_base_uri)

	def get_order_items(self, order_id: str, next_token: str | None = None) -> dict:
		"""Returns detailed order item information for the order indicated by the specified order ID. If NextToken is provided, it's used to retrieve the next page of order items."""
		append_to_base_uri = f"/{order_id}/orderItems"
		data = dict(NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class CatalogItems(SPAPI):
	"""Amazon Catalog Items API"""

	BASE_URI = "/catalog/2022-04-01"

	def get_catalog_item(
		self,
		asin: str,
		marketplace_ids: str | list | None = None,
		included_data: str | list | None = None,
		**kwargs,
	) -> dict:
		"""Returns a specified item and its attributes."""
		if not marketplace_ids:
			marketplace_ids = kwargs.get("marketplace_id") or self.marketplace_id

		if isinstance(marketplace_ids, list):
			marketplace_ids = ",".join(marketplace_ids)

		if not included_data:
			included_data = "summaries,attributes,productTypes"
		elif isinstance(included_data, list):
			included_data = ",".join(included_data)

		append_to_base_uri = f"/items/{asin}"
		data = dict(marketplaceIds=marketplace_ids, includedData=included_data)

		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class ListingsItems(SPAPI):
	"""Amazon Listings Items API"""

	BASE_URI = "/listings/2021-08-01"

	def get_listings_item(
		self,
		seller_id: str,
		sku: str,
		marketplace_ids: str | list | None = None,
		included_data: str | list | None = None,
	) -> dict:
		"""Returns details about a listing item for a seller."""
		if not marketplace_ids:
			marketplace_ids = self.marketplace_id

		if isinstance(marketplace_ids, list):
			marketplace_ids = ",".join(marketplace_ids)

		if not included_data:
			included_data = "attributes"
		elif isinstance(included_data, list):
			included_data = ",".join(included_data)

		from urllib.parse import quote

		append_to_base_uri = f"/items/{seller_id}/{quote(str(sku), safe='')}"
		params = dict(marketplaceIds=marketplace_ids, includedData=included_data)

		return self.make_request(append_to_base_uri=append_to_base_uri, params=params)


class ProductFees(SPAPI):
	"""Amazon Product Fees API"""

	BASE_URI = "/products/fees/v0"

	def get_my_fees_estimate_for_asin(
		self,
		asin: str,
		price: float = 100.0,
		currency: str = "INR",
		identifier: str = "fees_est",
	) -> dict:
		import json

		body = {
			"FeesEstimateRequest": {
				"MarketplaceId": self.marketplace_id,
				"PriceToEstimateFees": {
					"ListingPrice": {"CurrencyCode": currency, "Amount": price}
				},
				"Identifier": identifier,
			}
		}
		append_to_base_uri = f"/items/{asin}/feesEstimate"
		return self.make_request(
			method="POST", append_to_base_uri=append_to_base_uri, data=json.dumps(body)
		)


class Reports(SPAPI):
	"""Amazon Reports API (2021-06-30)"""

	BASE_URI = "/reports/2021-06-30/"

	def get_reports(
		self,
		report_types: list | str | None = None,
		processing_statuses: list | str | None = None,
		marketplace_ids: list | str | None = None,
		page_size: int = 10,
		created_since: str | None = None,
		created_until: str | None = None,
		next_token: str | None = None,
	) -> dict:
		"""Returns reports matching criteria."""
		data = dict(
			pageSize=page_size,
			createdSince=created_since,
			createdUntil=created_until,
			nextToken=next_token,
		)
		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]

		self.list_to_comma_str("reportTypes", report_types, data)
		self.list_to_comma_str("processingStatuses", processing_statuses, data)
		self.list_to_comma_str("marketplaceIds", marketplace_ids, data)

		return self.make_request(append_to_base_uri="reports", params=data)

	def get_report_document(self, report_document_id: str) -> dict:
		"""Returns report document download URL and encryption metadata."""
		append_to_base_uri = f"documents/{report_document_id}"
		return self.make_request(append_to_base_uri=append_to_base_uri)


class Util:
	@staticmethod
	def get_marketplace(country_code):
		for selling_region in MARKETPLACES:
			for country in MARKETPLACES.get(selling_region):
				if country_code == country:
					return MARKETPLACES.get(selling_region)
		else:
			raise KeyError(f"Invalid Country Code: {country_code}")

	@staticmethod
	def get_marketplace_data(country_code):
		marketplace = Util.get_marketplace(country_code)
		region = marketplace.get("AWS Region")
		endpoint = marketplace.get("Endpoint")
		marketplace_id = marketplace.get(country_code)

		return region, endpoint, marketplace_id

	@staticmethod
	def remove_empty(dict):
		"""
		Helper function that removes all keys from a dictionary (dict), that have an empty value.
		"""
		for key in list(dict):
			if not dict[key]:
				del dict[key]
		return dict
