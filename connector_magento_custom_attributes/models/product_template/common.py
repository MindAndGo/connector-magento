# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, models, fields
from odoo.addons.queue_job.job import job, related_action
from lxml import etree
import ast
from odoo.osv.orm import setup_modifiers


_logger = logging.getLogger(__name__)


class MagentoProductTemplate(models.Model):
    _inherit = 'magento.product.template'


    @api.depends('magento_template_attribute_value_ids')
    def _compute_custom_values_count(self):
        for product in self:
            product.custom_values_count = len(product.magento_template_attribute_value_ids)


    magento_template_attribute_value_ids = fields.One2many(
        comodel_name='magento.custom.template.attribute.values',
        inverse_name='magento_product_template_id',
        string='Magento Simple Custom Attributes Values for templates',
    )

    custom_values_count = fields.Integer('Custom Values Count', compute='_compute_custom_values_count')

    def action_magento_template_custom_attributes(self):
        action = self.env['ir.actions.act_window'].for_xml_id(
            'connector_magento_product_catalog',
            'action_magento_custom_template_attributes')

        action['domain'] = unicode([('magento_product_template_id', '=', self.id)])
        ctx = action.get('context', '{}') or '{}'

        action_context = ast.literal_eval(ctx)
        action_context.update({
            'default_attribute_set_id': self.attribute_set_id.id,
            'default_magento_product_template_id': self.id})
        #
        # #         action_context = ctx
        #         action_context.update({
        #             'default_project_id': self.project_id.id})
        action['context'] = action_context
        return action


    @api.multi
    def recheck_field_mapping(self, values=None):
        for mproduct in self:
            attribute_set_id = mproduct.attribute_set_id or mproduct.backend_id.default_attribute_set_id
            attributes = attribute_set_id.attribute_ids.filtered(lambda x: x.odoo_field_name)
            for att in attributes:
                mproduct.check_field_mapping(att.odoo_field_name.name, vals=values)



    @api.model
    def create(self, vals):
        '''
        Double purpose on this inheritance
        First if coming from connector, the method
        complete the attributes values and tr to find the missing values from odoo mapping
        
        If coming from a direct input in odoo, will also complete the
        structure of the template in order to prepare the next export
        '''
        mg_prod_id = super(MagentoProductTemplate, self).create(vals)
        org_vals = vals.copy()
        att_from_attset = mg_prod_id.attribute_set_id.attribute_ids
        attr_from_prod = mg_prod_id.magento_template_attribute_value_ids.mapped('attribute_id')
        attributes = att_from_attset - attr_from_prod
        cstm_att_mdl = self.env['magento.custom.template.attribute.values']
        for att in attributes:
            #Add missing attribute values reagrding the attribute set 
            vals = {
                'magento_product_template_id': mg_prod_id.id,
                'attribute_id': att.id,
            }
            cst_value = cstm_att_mdl.with_context(no_update=True).create(vals)
            if cst_value.odoo_field_name.id:
                #If a mapping exists, try to find out the value from odoo
                if cst_value.odoo_field_name.name not in mg_prod_id:
                    _logger.debug(
                        'Field %s not present in the model %s \nCheck your mapping!' % 
                        (cst_value.odoo_field_name.name, mg_prod_id))
                else:
                    _logger.debug(
                        'Prepare check for Odoo field [%s] value' % 
                        cst_value.odoo_field_name.name)
                    
                    mg_prod_id.check_field_mapping(
                        cst_value.odoo_field_name.name,
                        mg_prod_id[cst_value.odoo_field_name.name])
        
        if 'custom_attributes' in org_vals:
            #TODO : Check weither of not thispart still usefull
            '''
            If the key is present,
            should come form connector
            '''
            for cst in org_vals['custom_attributes']:
                cst_value_id = mg_prod_id.magento_template_attribute_value_ids.filtered(
                    lambda v: v.attribute_id.attribute_code == cst['attribute_code'])
                _logger.debug(
                        'Prepare mapping for Custom dict  %s and values already set %s' % 
                        ( cst, cst_value_id ))
                if cst_value_id.odoo_field_name.id:
                    mg_prod_id.check_field_mapping(
                        cst_value_id.odoo_field_name.name,
                        cst['value']
                    )
                elif cst_value_id.id:
                    cst_value_id.write({
                        'attribute_text': cst['value']})

        return mg_prod_id

    @api.multi
    def check_field_mapping(self, field, vals):
        """
        # Check if the Odoo Field has a matching attribute in Magento
        # Update the value
        # Return an appropriate dictionnary
        :param field : field representation as ...
        :param vals : dictionnary of ...

        :return : nothing
        """
        self.ensure_one()
        #         att_id = 0
        custom_model = self.env['magento.custom.template.attribute.values']
        odoo_fields = self.env['ir.model.fields'].search([
            ('name', '=', field),
            ('model', 'in', ['product.template'])])

        att_ids = self.env['magento.product.attribute'].search(
            [('odoo_field_name', 'in', [o.id for o in odoo_fields]),
             ('backend_id', '=', self.backend_id.id)
             ])

        if len(att_ids) > 0:
            att_id = att_ids[0]
            values = custom_model.search(
                [('magento_product_template_id', '=', self.id),
                 ('attribute_id', '=', att_id.id),
                 ('store_view_id', '=', False)
                 ])
            custom_vals = {
                'magento_product_template_id': self.id,
                'attribute_id': att_id.id,
                'attribute_text': self[field],
                'attribute_select': False,
                'attribute_multiselect': False,
            }
            odoo_field_type = odoo_fields[0].ttype
            if odoo_field_type in ['many2one', 'many2many'] \
                    and 'text' in att_id.frontend_input:
                custom_vals.update({
                    'attribute_text': str(
                        [v.magento_bind_ids.external_id for v in self[field]
                         ])})

            if att_id.frontend_input == 'boolean':
                custom_vals.update({
                    'attribute_text': str(int(self[field]))})
            if att_id.frontend_input == 'select':
                custom_vals.update({
                    'attribute_text': False,
                    'attribute_multiselect': False,
                    'attribute_select': self[field].magento_bind_ids[0].id})
            if att_id.frontend_input == 'multiselect':
                custom_vals.update({
                    'attribute_text': False,
                    'attribute_multiselect': False,
                    'attribute_multiselect':
                        [(6, False, [
                            v.id for v in self[field].magento_bind_ids])]})
            if len(values) == 0:
                custom_model.with_context(no_update=True).create(custom_vals)
            else:
                values.with_context(no_update=True).write(custom_vals)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        res = super(ProductTemplate, self).fields_view_get(
            view_id=view_id, 
            view_type=view_type, 
            toolbar=toolbar,
            submenu=submenu)

        if res['model'] in ['product.template', 'product.product'] and \
                res['type'] == 'form':
            doc = etree.XML(res['arch'])
            mapped_field_ids = self.env['magento.product.attribute'].search(
                [('odoo_field_name', '!=', False)]).mapped('odoo_field_name')

            for field in mapped_field_ids:
                nodes = doc.xpath("//field[@name='%s']" % field.name)
                for node in nodes:
                    node.set('class', 'magento-mapped-field-view')
                    help = node.get('help', '')
                    node.set('help', '** Magento ** \n %s' % help)
                    setup_modifiers(
                        node, res['fields'][field.name])
            res['arch'] = etree.tostring(doc)
        return res
