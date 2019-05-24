import logging
from odoo import models, fields, api
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.component.core import Component
from odoo.addons.component_event import skip_if
from odoo.addons.queue_job.job import job, related_action

_logger = logging.getLogger(__name__)

class MagentoProductAttribute(models.Model):
    _name = 'magento.product.attribute'
    _inherit = 'magento.binding'
    _inherits = {'product.attribute': 'odoo_id'}
    _description = 'Magento attribute'
    
    odoo_id = fields.Many2one(comodel_name='product.attribute',
                              string='Product attribute',
                              required=True,
                              ondelete='restrict')
    
    magento_attribute_value_ids = fields.One2many(
        comodel_name='magento.product.attribute.value',
        inverse_name='magento_attribute_id',
        string='Magento product attribute value'
    )
    
    attribute_set_ids = fields.Many2many('magento.product.attributes.set', string='Attribute(s)')
    
    
class ProductAttribute(models.Model):
    _inherit = 'product.attribute'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.attribute',
        inverse_name='odoo_id',
        string='Magento Bindings',
    )
    
    odoo_field_name = fields.Many2one(comodel_name='ir.model.fields', string="Odoo Field Name",)
#    magento_mapping_ids = fields.One2many('channel.attribute.mappings', 'attribute_name', readonly=1)
    
    

class ProductAttributeAdapter(Component):
    _name = 'magento.product.attribute.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.product.attribute'

    _magento2_model = 'products/attributes'
    _magento2_search = 'products/attributes'
    _magento2_key = 'attribute_id'


    def _call(self, method, arguments):
        try:
            return super(ProductAttributeAdapter, self)._call(method, arguments)
        except xmlrpclib.Fault as err:
            # this is the error in the Magento API
            # when the product does not exist
            if err.faultCode == 101:
                raise IDMissingInBackend
            else:
                raise
            





