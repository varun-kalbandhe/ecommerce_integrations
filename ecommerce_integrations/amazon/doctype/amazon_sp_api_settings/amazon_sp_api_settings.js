// Copyright (c) 2022, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Amazon SP API Settings', {
	refresh(frm) {
		if (frm.doc.__islocal && !frm.doc.amazon_fields_map) {
			frm.trigger("set_default_fields_map");
		}
		frm.trigger("set_queries");
		frm.set_df_property("amazon_fields_map", "cannot_add_rows", true);
		frm.set_df_property("amazon_fields_map", "cannot_delete_rows", true);

		if (!frm.doc.__islocal && frm.doc.is_active) {
			const run_sync = (sync_type) => {
				frappe.show_alert({
					message: __(`Amazon ${sync_type} sync started in the background...`),
					indicator: 'blue'
				}, 5);
				frappe.call({
					method: "ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings.trigger_production_sync",
					args: {
						amz_setting_name: frm.doc.name,
						sync_type: sync_type
					},
					callback: (r) => {
						if (r.message && r.message.status === "enqueued") {
							frappe.show_alert({
								message: __(`Job queued. You will be notified once finished.`),
								indicator: 'green'
							}, 5);
						}
					}
				});
			};

			frm.add_custom_button(__('Run Full Sync'), () => run_sync("All"), __('Actions'));
			frm.add_custom_button(__('Sync Orders'), () => run_sync("Orders"), __('Actions'));
			frm.add_custom_button(__('Sync Finances'), () => run_sync("Finances"), __('Actions'));
			frm.add_custom_button(__('Sync Settlements'), () => run_sync("Settlements"), __('Actions'));
			frm.add_custom_button(__('Recalculate Profitability'), () => run_sync("Profitability"), __('Actions'));
		}
	},

	set_default_fields_map(frm) {
		frappe.call({
			method: "set_default_fields_map",
			doc: frm.doc,
			callback: (r) => {
				if (!r.exc) refresh_field("amazon_fields_map");
			},
		});
	},

	set_queries(frm) {
		frm.set_query("warehouse", () => {
			return {
				filters: {
					"is_group": 0,
					"company": frm.doc.company,
				}
			};
		});

		frm.set_query("market_place_account_group", () => {
			return {
				filters: {
					"is_group": 1,
					"company": frm.doc.company,
				}
			};
		});
	}
});
