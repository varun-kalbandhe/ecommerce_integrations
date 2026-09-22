# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


from requests import request

__all__ = [
	"SPAPIError",
	"Finances",
	"Orders",
	"CatalogItems",
	"ListingsItems",
	"ProductFees",
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
		super().__init__(*args)


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
		return response.json()

	def list_to_dict(self, key: str, values: list, data: dict) -> None:
		if values and isinstance(values, list):
			for idx in range(len(values)):
				data[f"{key}[{idx}]"] = values[idx]


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


class Orders(SPAPI):
	"""Amazon Orders API"""

	BASE_URI = "/orders/v0/orders"

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

		self.list_to_dict("OrderStatuses", order_statuses, data)
		self.list_to_dict("MarketplaceIds", marketplace_ids, data)
		self.list_to_dict("FulfillmentChannels", fulfillment_channels, data)
		self.list_to_dict("PaymentMethods", payment_methods, data)
		self.list_to_dict("EasyShipShipmentStatuses", easyship_shipment_statuses, data)
		self.list_to_dict("AmazonOrderIds", amazon_order_ids, data)

		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]
			data["MarketplaceIds"] = marketplace_ids

		return self.make_request(params=data)

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

		append_to_base_uri = f"/items/{seller_id}/{sku}"
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
