frappe.listview_settings['Sales Order'] = {
	onload: function(listview) {
		listview.page.add_inner_button(__('Sync Amazon Orders'), function() {
			var d = new frappe.ui.Dialog({
				title: __('Sync Amazon Orders'),
				fields: [
					{
						label: 'From Date',
						fieldname: 'after_date',
						fieldtype: 'Date',
						reqd: 0
					},
					{
						label: 'To Date',
						fieldname: 'before_date',
						fieldtype: 'Date',
						reqd: 0
					}
				],
				primary_action_label: __('Sync'),
				primary_action: function(values) {
					d.hide();
					frappe.call({
						method: "ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api_settings.sync_amazon_orders",
						args: {
							after_date: values.after_date || null,
							before_date: values.before_date || null
						},
						freeze: true,
						freeze_message: __("Syncing Amazon Orders..."),
						callback: function(r) {
							if (!r.exc) {
								frappe.msgprint(__('Amazon Order Sync triggered successfully.'));
							}
						}
					});
				}
			});
			d.show();
		}, __('Sync'));
	}
};
