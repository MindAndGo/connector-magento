# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.h

from odoo.addons.component.core import Component
from odoo.addons.component_event import skip_if
from odoo.addons.queue_job.job import identity_exact


class MagentoProductTemplateBindingExportListener(Component):
    _name = 'magento.product.template.binding.export.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['magento.product.template']

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        if record.backend_id.product_synchro_strategy == 'magento_first': 
                return
        record.with_delay(identity_key=identity_exact).export_record(record.backend_id)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if record.backend_id.product_synchro_strategy == 'magento_first': 
                return        
        record.with_delay(identity_key=identity_exact).export_record(record.backend_id)

    def on_record_unlink(self, record):
        if record.backend_id.product_synchro_strategy == 'magento_first': 
                return    
        with record.backend_id.work_on(record._name) as work:
            external_id = work.component(usage='binder').to_external(record)
            if external_id:
                record.with_delay(identity_key=identity_exact).export_delete_record(record.backend_id,
                                                         external_id)


class MagentoProductTemplateExportListener(Component):
    _name = 'magento.product.template.export.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['product.template']

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):     
        if 'image_medium' in fields:
            # We do ignore image field
            fields.remove('image_medium')
        if 'image_small' in fields:
            # We do ignore image field
            fields.remove('image_small')
        if not len(fields):
            return
        # Check to see if it is a single variant template
        if record.product_variant_count == 1:
            binding_ids = record.product_variant_ids[0].magento_bind_ids
        else:
            binding_ids = record.magento_template_bind_ids
        for binding in binding_ids:
            if binding.backend_id.product_synchro_strategy == 'magento_first': 
                continue       
            binding.with_delay(identity_key=identity_exact).export_record(binding.backend_id)
