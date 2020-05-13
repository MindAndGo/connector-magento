# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo.addons.component.core import Component
from odoo.addons.component_event import skip_if
from odoo.addons.queue_job.job import identity_exact


class MagentoStockItemExportListener(Component):
    _name = 'magento.stock.item.export.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['magento.stock.item']

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if record.should_export:
            record.with_delay(identity_key=identity_exact, priority=5).export_record(record.backend_id)


class MagentoStockMoveListener(Component):
    _name = 'magento.stock.move.picking.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['stock.move']


    def _need_to_update(self, fields, record, binding):
        _logger.info(
            """Trying to identify if updating the stock is needed 
            for record %s, binding %s and fields %s""" % (record, binding, fields))
        if 'state' in fields:
            wh_location_id = binding.backend_id.warehouse_id.lot_stock_id
            #TODO: Imporve the comparison with clid of syntax
            
            if record.location_dest_id.get_warehouse() == wh_location_id.get_warehouse() or \
                   record.location_id.get_warehouse() == wh_location_id.get_warehouse() :
                return True
        
        return False 


    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_picking_out_done(self, record, picking_method):
        for binding in record.product_id.magento_bind_ids:
            
            #Revert 05562521028a6a2c0f122b38fd1c09ad6faac7f4
#             if not self._need_to_update(fields, record, binding):
#                 return
            for stock_item in binding.magento_stock_item_ids:
                if stock_item.should_export:
                    stock_item.with_delay(identity_key=identity_exact, priority=5).export_record(stock_item.backend_id)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        for binding in record.product_id.magento_bind_ids:
            #Revert 05562521028a6a2c0f122b38fd1c09ad6faac7f4
#             if not self._need_to_update(fields, record, binding):
#                 return 
            for stock_item in binding.magento_stock_item_ids:
                if stock_item.should_export:
                    stock_item.with_delay(identity_key=identity_exact, priority=5).export_record(stock_item.backend_id)
