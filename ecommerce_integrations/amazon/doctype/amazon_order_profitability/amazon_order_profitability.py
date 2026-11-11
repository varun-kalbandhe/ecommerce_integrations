# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AmazonOrderProfitability(Document):
	def validate(self):
		self.calculate_totals()

	def calculate_totals(self):
		tot_rev = 0.0
		tot_cogs = 0.0
		tot_direct_fees = 0.0
		tot_allocated_exp = 0.0
		tot_refund = 0.0

		for row in self.items:
			tot_rev += row.revenue or 0.0
			tot_cogs += row.product_cogs or 0.0
			tot_direct_fees += row.direct_amazon_fees or 0.0
			tot_allocated_exp += row.allocated_order_expense or 0.0
			tot_refund += row.refund_amount or 0.0

			row.net_profit = round(
				(row.revenue or 0.0)
				- (row.product_cogs or 0.0)
				- (row.direct_amazon_fees or 0.0)
				- (row.allocated_order_expense or 0.0)
				- (row.refund_amount or 0.0),
				2,
			)
			if row.revenue and row.revenue > 0:
				row.profit_margin = round((row.net_profit / row.revenue) * 100.0, 2)
			else:
				row.profit_margin = 0.0

		if not self.revenue:
			self.revenue = round(tot_rev, 2)
		if not self.product_cogs:
			self.product_cogs = round(tot_cogs, 2)

		self.net_profit = round(
			(self.revenue or 0.0)
			- (self.product_cogs or 0.0)
			- (self.amazon_fees or 0.0)
			- (self.shipping_expense or 0.0)
			- (self.advertising_expense or 0.0)
			- (self.other_amazon_expense or 0.0)
			- (self.refund_amount or 0.0)
			+ (self.adjustment_amount or 0.0),
			2,
		)

		if self.revenue and self.revenue > 0:
			self.profit_margin = round((self.net_profit / self.revenue) * 100.0, 2)
		else:
			self.profit_margin = 0.0
