# -*- coding: utf-8 -*-
# © 2019 Wolfgang Pichler,Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ProductImportMapper(Component):
    _inherit = 'magento.product.product.import.mapper'

    @mapping
    def price(self, record):
        if record['visibility'] == 1:
            # This is a product variant - so the price will get set in fix_price
            return {
                'fix_price': record.get('price', 0.0),
            }
        return super(ProductImportMapper, self).price(record)
