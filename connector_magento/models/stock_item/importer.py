# -*- coding: utf-8 -*-
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo import _
from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.connector.exception import MappingError
import json


class MagentoStockItemImportMapper(Component):
    _name = 'magento.stock.item.import.mapper'
    _inherit = 'magento.import.mapper'
    _apply_on = 'magento.stock.item'

    direct = [
        ('qty', 'qty'),
        ('min_sale_qty', 'min_sale_qty'),
        ('is_qty_decimal', 'is_qty_decimal'),
        ('is_in_stock', 'is_in_stock'),
        ('min_qty', 'min_qty'),
        ('use_config_min_qty', 'use_config_min_qty'),
        ('manage_stock', 'manage_stock'),
        ('use_config_backorders', 'use_config_backorders'),
        ('use_config_manage_stock', 'use_config_manage_stock'),
        ('item_id', 'external_id'),
    ]
    
    @mapping
    @only_create
    def magento_product_binding_id(self, record):
        binder = self.binder_for('magento.product.product')
        mproduct = binder.to_internal(record['product_id'], external_field='magento_id', unwrap=False)
        if mproduct:
            return {
                'magento_product_binding_id': mproduct.id,
                'magento_product_template_binding_id': None,
                'product_type': 'product',
            }
        binder = self.binder_for('magento.product.template')
        mproduct = binder.to_internal(record['product_id'], external_field='magento_id', unwrap=False)
        if mproduct:
            return {
                'magento_product_template_binding_id': mproduct.id,
                'magento_product_binding_id': None,
                'product_type': 'configurable',
            }

    @mapping
    @only_create
    def warehouse_id(self, record):
        binder = self.binder_for('magento.stock.warehouse')
        mwarehouse = binder.to_internal(record['stock_id'], unwrap=False)
        return {
            'magento_warehouse_id': mwarehouse.id,
        }

    @mapping
    def backorders(self, record):
        map = {
            0: 'no',
            1: 'yes',
            2: 'yes-and-notification'
        }
        return {'backorders': map[record['backorders']]}

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}


class MagentoStockItemImporter(Component):
    _name = 'magento.stock.item.importer'
    _inherit = 'magento.importer'
    _apply_on = 'magento.stock.item'
    _magento_id_field = 'item_id'
