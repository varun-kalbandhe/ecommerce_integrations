# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


from datetime import datetime

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import add_days, today


class AmazonSPAPISettings(Document):
	def before_validate(self):
		if not self.amazon_fields_map:
			self.set_default_fields_map()

	def validate(self):
		self.validate_amazon_fields_map()
		self.validate_after_date()

		if self.is_active == 1:
			self.validate_credentials()
			setup_custom_fields()
		else:
			self.enable_sync = 0

		if not self.max_retry_limit:
			self.max_retry_limit = 1
		elif self.max_retry_limit and self.max_retry_limit > 5:
			frappe.throw(frappe._("Value for <b>Max Retry Limit</b> must be less than or equal to 5."))

	def save(self):
		super().save()

		if not self.is_old_data_migrated:
			migrate_old_data()
			self.db_set("is_old_data_migrated", 1)

	def validate_amazon_fields_map(self):
		count = 0
		for field_map in self.amazon_fields_map:
			item_meta = frappe.get_meta("Item")
			field_meta = item_meta.get_field(field_map.item_field)

			if field_map.item_field and not field_meta:
				frappe.throw(
					_("Row #{0}: Item Field {1} does not exist.").format(
						field_map.idx, frappe.bold(field_map.item_field)
					)
				)

			if field_map.use_to_find_item_code:
				# `Item Field` is required if `Use To Find Item Code` is checked.
				if not field_map.item_field:
					frappe.throw(_("Row #{0}: Item Field is required.").format(field_map.idx))

				# `Item Field` should be unique if `Use To Find Item Code` is checked.
				elif not field_meta.unique:
					frappe.throw(
						_("Row #{0}: Item Field {1} must be unique.").format(
							field_map.idx, frappe.bold(field_map.item_field)
						)
					)

				count += 1

		if count == 0:
			frappe.throw(_("At least one field must be selected to find the item code."))
		elif count > 1:
			frappe.throw(_("Only one field can be selected to find the item code."))

	def validate_after_date(self):
		pass
		# if datetime.strptime(add_days(today(), -30), "%Y-%m-%d") > datetime.strptime(
		# 	self.after_date, "%Y-%m-%d"
		# ):
		# 	frappe.throw(_("The date must be within the last 30 days."))

	def validate_credentials(self):
		from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
			validate_amazon_sp_api_credentials,
		)

		validate_amazon_sp_api_credentials(
			client_id=self.get("client_id"),
			client_secret=self.get_password("client_secret"),
			refresh_token=self.get("refresh_token"),
			country=self.get("country"),
		)

	@frappe.whitelist()
	def set_default_fields_map(self):
		for field_map in [
			{"amazon_field": "ASIN", "item_field": "item_code", "use_to_find_item_code": 1},
			{
				"amazon_field": "SellerSKU",
				"item_field": None,
				"use_to_find_item_code": 0,
			},
			{
				"amazon_field": "Title",
				"item_field": None,
				"use_to_find_item_code": 0,
			},
		]:
			self.append("amazon_fields_map", field_map)

	@frappe.whitelist()
	def get_order_details(self):
		from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
			get_orders,
		)

		if self.is_active == 1:
			job_name = f"Get Amazon Orders - {self.name}"

			if frappe.db.get_all("RQ Job", {"job_name": job_name, "status": ["in", ["queued", "started"]]}):
				return frappe.msgprint(_("The order details are currently being fetched in the background."))

			frappe.enqueue(
				job_name=job_name,
				method=get_orders,
				amz_setting_name=self.name,
				created_after=self.after_date,
				created_before=self.before_date,
				timeout=4000,
				now=frappe.flags.in_test,
			)

			frappe.msgprint(_("Order details will be fetched in the background."))
		else:
			frappe.msgprint(_("Please enable the Amazon SP API Settings {0}.").format(frappe.bold(self.name)))


# Called via a hook in every hour.
def schedule_get_order_details():
	from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
		get_orders,
		AmazonRepository,
	)
	from frappe.utils import add_days, nowdate, now_datetime

	amz_settings = frappe.get_all(
		"Amazon SP API Settings",
		filters={"is_active": 1, "enable_sync": 1},
		fields=["name", "company", "financial_lookback_days", "auto_post_accounting"],
	)

	for s in amz_settings:
		repo = AmazonRepository(s.name)
		lookback = s.get("financial_lookback_days") or 14

		# 1. Orders Sync (Rolling 3 days)
		try:
			recent_after = add_days(nowdate(), -3)
			get_orders(amz_setting_name=s.name, created_after=recent_after, created_before=None)
			frappe.db.set_value("Amazon SP API Settings", s.name, "last_order_sync", now_datetime(), update_modified=False)
		except Exception as e:
			frappe.log_error(f"Scheduled Amazon Order Sync Failed for {s.name}: {str(e)}", "Amazon Production Sync")

		# 2. Financial Transactions Sync (Rolling lookback window to capture late fees, refunds, adjustments)
		synced_tx_names = []
		try:
			fin_after = f"{add_days(nowdate(), -int(lookback))}T00:00:00Z"
			synced_tx_names = repo.sync_financial_transactions(posted_after=fin_after)
			frappe.db.set_value("Amazon SP API Settings", s.name, "last_financial_sync", now_datetime(), update_modified=False)
		except Exception as e:
			frappe.log_error(f"Scheduled Amazon Financial Sync Failed for {s.name}: {str(e)}", "Amazon Production Sync")

		# 3. Settlement Reports Sync & Auto-Reconciliation
		try:
			settle_since = f"{add_days(nowdate(), -int(lookback * 2))}T00:00:00Z"
			repo.sync_settlement_reports(page_size=5, created_since=settle_since)
			frappe.db.set_value("Amazon SP API Settings", s.name, "last_settlement_sync", now_datetime(), update_modified=False)
		except Exception as e:
			frappe.log_error(f"Scheduled Amazon Settlement Sync Failed for {s.name}: {str(e)}", "Amazon Production Sync")

		# 4. Controlled Accounting Posting (Only if explicitly enabled and status is Ready)
		if s.get("auto_post_accounting"):
			try:
				repo.post_batch_transactions(batch_size=50)
			except Exception as e:
				frappe.log_error(f"Scheduled Amazon Accounting Post Failed for {s.name}: {str(e)}", "Amazon Production Sync")

		# 5. Profitability Recalculation for Affected Orders
		try:
			from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
			engine = AmazonProfitabilityEngine()
			affected_orders = set()
			for t_name in synced_tx_names:
				oid = frappe.db.get_value("Amazon Financial Transaction", t_name, "amazon_order_id")
				if oid:
					affected_orders.add(oid)
			for oid in affected_orders:
				try:
					engine.calculate_order_profitability(oid)
				except Exception:
					pass
			frappe.db.set_value("Amazon SP API Settings", s.name, {
				"last_profitability_sync": now_datetime(),
				"last_sync_status": "Success",
			}, update_modified=False)
		except Exception as e:
			frappe.log_error(f"Scheduled Amazon Profitability Calculation Failed for {s.name}: {str(e)}", "Amazon Production Sync")

		frappe.db.commit()


@frappe.whitelist()
def trigger_production_sync(amz_setting_name: str, sync_type: str = "All") -> dict:
	"""
	Enqueues the Amazon sync job in the background and returns immediately
	so the UI stays clean and unblocked.
	"""
	frappe.enqueue(
		"ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings.execute_production_sync_job",
		queue="long",
		timeout=1500,
		amz_setting_name=amz_setting_name,
		sync_type=sync_type,
		user=frappe.session.user,
	)
	return {"status": "enqueued", "message": f"{sync_type} sync started in the background."}


def execute_production_sync_job(amz_setting_name: str, sync_type: str = "All", user: str = None):
	summary = run_production_sync(amz_setting_name=amz_setting_name, sync_type=sync_type)
	if user:
		failed_count = summary.get("orders_failed", 0)
		failed_details = summary.get("orders_failed_details", [])
		failed_msg = ""
		if failed_details:
			reasons_preview = "<br>".join(
				[f"&bull; <code>{f.get('order_id')}</code>: {f.get('reason')}" for f in failed_details[:5]]
			)
			if len(failed_details) > 5:
				reasons_preview += f"<br>...and {len(failed_details) - 5} more (see Error Log)"
			failed_msg = f"<br><b>Failed Orders ({failed_count}):</b><br>{reasons_preview}"

		orders_breakdown = (
			f"Total: {summary.get('orders_total', 0)} | "
			f"Success: {summary.get('orders_successful', 0)} | "
			f"Failed: {summary.get('orders_failed', 0)} | "
			f"Skipped: {summary.get('orders_skipped', 0)}"
		) if "orders_total" in summary else summary.get("orders_status")

		indicator = "green"
		if failed_count > 0:
			indicator = "orange" if summary.get("orders_successful", 0) > 0 else "red"

		msg = (
			f"<b>Amazon {sync_type} Sync Completed!</b><br><br>"
			f"<b>Orders:</b> {summary.get('orders_status')}<br>"
			f"({orders_breakdown}){failed_msg}<br><br>"
			f"<b>Finances:</b> {summary.get('finances_synced', 0)} synced<br>"
			f"<b>Settlements:</b> {summary.get('settlements_synced', 0)} synced<br>"
			f"<b>Profitability:</b> {summary.get('profitability_recalculated', 0)} recalculated"
		)
		frappe.publish_realtime(
			"msgprint",
			{"message": msg, "title": f"Amazon {sync_type} Sync Summary", "indicator": indicator},
			user=user,
		)
		frappe.publish_realtime(
			"doc_update",
			{"doctype": "Amazon SP API Settings", "name": amz_setting_name},
			user=user,
		)


@frappe.whitelist()
def run_production_sync(amz_setting_name: str, sync_type: str = "All") -> dict:
	"""
	Manual administrator trigger executing the unified production sync pipeline.
	sync_type options: 'All', 'Orders', 'Finances', 'Settlements', 'Profitability'
	"""
	from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
		get_orders,
		AmazonRepository,
	)
	from frappe.utils import add_days, nowdate, now_datetime

	doc = frappe.get_doc("Amazon SP API Settings", amz_setting_name)
	repo = AmazonRepository(doc.name)
	lookback = doc.get("financial_lookback_days") or 14

	summary = {
		"start_time": frappe.utils.now(),
		"setting": doc.name,
		"sync_type": sync_type,
		"orders_status": "Skipped",
		"finances_synced": 0,
		"settlements_synced": 0,
		"profitability_recalculated": 0,
	}

	# 1. Orders
	if sync_type in ["All", "Orders"]:
		try:
			recent_after = add_days(nowdate(), -3)
			orders_summary = get_orders(amz_setting_name=doc.name, created_after=recent_after, created_before=None, return_summary=True)
			doc.last_order_sync = now_datetime()

			if isinstance(orders_summary, dict):
				total = orders_summary.get("total_orders", 0)
				successful = len(orders_summary.get("successful_orders", []))
				failed = len(orders_summary.get("failed_orders", []))
				skipped = len(orders_summary.get("skipped_orders", []))

				summary["orders_total"] = total
				summary["orders_successful"] = successful
				summary["orders_failed"] = failed
				summary["orders_skipped"] = skipped
				summary["orders_successful_ids"] = orders_summary.get("successful_orders", [])
				summary["orders_failed_details"] = orders_summary.get("failed_orders", [])
				summary["orders_skipped_ids"] = orders_summary.get("skipped_orders", [])
				summary["sales_orders_created"] = orders_summary.get("sales_orders", [])
				summary["sales_invoices_created"] = orders_summary.get("sales_invoices", [])
				summary["delivery_notes_created"] = orders_summary.get("delivery_notes", [])

				if total == 0:
					summary["orders_status"] = "No Orders in Window"
				elif failed == 0:
					summary["orders_status"] = f"Success ({successful} processed, {skipped} skipped)"
				elif successful > 0:
					summary["orders_status"] = f"Partial ({successful} succeeded, {failed} failed, {skipped} skipped)"
				else:
					summary["orders_status"] = f"Failed ({failed} failed, {skipped} skipped)"
			else:
				# Fallback if list returned
				summary["orders_status"] = "Success"
		except Exception as e:
			summary["orders_status"] = f"Failed: {str(e)}"
			summary["orders_failed_details"] = [{"order_id": "All", "reason": str(e)}]
			frappe.log_error(f"Manual Amazon Order Sync Failed: {str(e)}", "Amazon Manual Sync")

	# 2. Finances
	synced_tx_names = []
	if sync_type in ["All", "Finances"]:
		try:
			fin_after = f"{add_days(nowdate(), -int(lookback))}T00:00:00Z"
			synced_tx_names = repo.sync_financial_transactions(posted_after=fin_after)
			doc.last_financial_sync = now_datetime()
			summary["finances_synced"] = len(synced_tx_names)
		except Exception as e:
			summary["finances_error"] = str(e)
			frappe.log_error(f"Manual Amazon Financial Sync Failed: {str(e)}", "Amazon Manual Sync")

	# 3. Settlements
	if sync_type in ["All", "Settlements"]:
		try:
			settle_since = f"{add_days(nowdate(), -int(lookback * 2))}T00:00:00Z"
			s_names = repo.sync_settlement_reports(page_size=5, created_since=settle_since)
			doc.last_settlement_sync = now_datetime()
			summary["settlements_synced"] = len(s_names)
		except Exception as e:
			summary["settlements_error"] = str(e)
			frappe.log_error(f"Manual Amazon Settlement Sync Failed: {str(e)}", "Amazon Manual Sync")

	# 4. Profitability
	if sync_type in ["All", "Profitability"] or synced_tx_names:
		try:
			from ecommerce_integrations.amazon.doctype.amazon_order_profitability.amazon_profitability_engine import AmazonProfitabilityEngine
			engine = AmazonProfitabilityEngine()
			affected_orders = set()
			if synced_tx_names:
				for t_name in synced_tx_names:
					oid = frappe.db.get_value("Amazon Financial Transaction", t_name, "amazon_order_id")
					if oid:
						affected_orders.add(oid)
			else:
				recent_order_ids = frappe.db.sql(
					"""
					select distinct amazon_order_id from `tabAmazon Financial Transaction`
					where amazon_order_id is not null and modified >= %s
					""",
					add_days(nowdate(), -int(lookback)),
					pluck=True,
				)
				affected_orders = set(recent_order_ids)

			count = 0
			for oid in affected_orders:
				try:
					engine.calculate_order_profitability(oid)
					count += 1
				except Exception:
					pass
			doc.last_profitability_sync = now_datetime()
			summary["profitability_recalculated"] = count
		except Exception as e:
			summary["profitability_error"] = str(e)
			frappe.log_error(f"Manual Amazon Profitability Calculation Failed: {str(e)}", "Amazon Manual Sync")

	doc.last_sync_status = "Success"
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	summary["end_time"] = frappe.utils.now()
	return summary


@frappe.whitelist()
def sync_amazon_orders(after_date=None, before_date=None):
	from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import get_orders
	from frappe.utils import add_days, nowdate
	
	amz_settings = frappe.get_all(
		"Amazon SP API Settings",
		filters={"is_active": 1},
		fields=["name"],
	)

	for amz_setting in amz_settings:
		_after = after_date or add_days(nowdate(), -3)
		_before = before_date
		
		frappe.enqueue(
			"ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository.get_orders",
			queue="long",
			timeout=3600,
			amz_setting_name=amz_setting.name,
			created_after=_after,
			created_before=_before
		)
	
	return True

def setup_custom_fields():
	custom_fields = {
		"Sales Order": [
			dict(
				fieldname="amazon_order_id",
				label="Amazon Order ID",
				fieldtype="Data",
				insert_after="title",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="amazon_order_status",
				label="Amazon Order Status",
				fieldtype="Data",
				insert_after="amazon_order_id",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="amazon_fulfillment_channel",
				label="Amazon Fulfillment Channel",
				fieldtype="Data",
				insert_after="amazon_order_status",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="amazon_shipment_status",
				label="Amazon Shipment Status",
				fieldtype="Data",
				insert_after="amazon_fulfillment_channel",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="amazon_payment_method",
				label="Amazon Payment Method",
				fieldtype="Data",
				insert_after="amazon_shipment_status",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="amazon_cancellation_reason",
				label="Amazon Cancellation Reason",
				fieldtype="Data",
				insert_after="amazon_payment_method",
				read_only=1,
				print_hide=1,
				allow_on_submit=1,
			),
		],
	}

	create_custom_fields(custom_fields)


def migrate_old_data():
	column_exists = frappe.db.has_column("Item", "amazon_item_code")

	if column_exists:
		item = frappe.qb.DocType("Item")
		items = (frappe.qb.from_(item).select("*").where(item.amazon_item_code.notnull())).run(as_dict=True)

		for item in items:
			if not frappe.db.exists("Ecommerce Item", {"erpnext_item_code": item.name}):
				ecomm_item = frappe.new_doc("Ecommerce Item")
				ecomm_item.integration = "Amazon"
				ecomm_item.erpnext_item_code = item.name
				ecomm_item.integration_item_code = item.amazon_item_code
				ecomm_item.has_variants = 0
				ecomm_item.sku = item.amazon_item_code
				ecomm_item.flags.ignore_mandatory = True
				ecomm_item.save(ignore_permissions=True)
