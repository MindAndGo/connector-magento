# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import datetime, timedelta
from odoo.addons.queue_job.job import identity_exact

IMPORT_DELTA_BUFFER = 30  # seconds


class MagentoBackend(models.Model):
    _inherit = 'magento.backend'

    @api.multi
    def export_product_catalog(self):
        import_start_time = datetime.now()
        # TODO make batchExporter class
        for backend in self:
            backend.check_magento_structure()
            domain = []
            if backend.export_products_from_date:
                domain = [('write_date', '>', backend.export_products_from_date)]
            mag_prods = self.env['magento.product.template'].search(domain)
            for mag_prod in mag_prods:
                mag_prod.sync_to_magento()
            next_time = import_start_time - timedelta(seconds=IMPORT_DELTA_BUFFER)
            next_time = fields.Datetime.to_string(next_time)
            backend.write({'export_products_from_date': next_time})
        return True

    @api.multi
    def button_sync_to_magento_products(self):
        for backend in self:
            for model_name in ('magento.product.template',
                               'magento.product.product'):
                self.env[model_name].search([('backend_id', '=', backend.id)]).with_delay(identity_key=identity_exact).sync_to_magento()

    product_synchro_strategy = fields.Selection([
            ('magento_first', 'Magento First'),
            ('odoo_first', 'Odoo First'),
        ],
        string='Product Update Strategy',
        help='Precise which strategy you want to update',
        default='magento_first'
    )
    export_products_from_date = fields.Datetime(
        string='Export products from date',
    )
    default_attribute_set_id = fields.Many2one('magento.product.attributes.set', string="Default Attribute Set id")
    default_magento_status = fields.Selection([
        ('2', 'Disabled'),
        ('1', 'Enabled'),
    ], default='2', string="Default Status", 
        help='''Prefer Disable when working with Odoo first 
        so that teams could check the product coherence in magento before publication''')

