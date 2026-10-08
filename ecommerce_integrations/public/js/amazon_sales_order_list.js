frappe.listview_settings['Sales Order'] = {
	onload: function(listview) {
		listview.page.add_inner_button(__('Sync Amazon Orders'), function() {
			frappe.call({
				method: "ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings.sync_amazon_orders",
				freeze: true,
				freeze_message: __("Syncing Amazon Orders..."),
				callback: function(r) {
					if (!r.exc) {
						frappe.msgprint(__('Amazon Order Sync triggered successfully.'));
					}
				}
			});
		}, __('Sync'));
	}
};
