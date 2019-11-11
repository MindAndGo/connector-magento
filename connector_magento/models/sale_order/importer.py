# -*- coding: utf-8 -*-
# Copyright 2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging

from datetime import datetime, timedelta

from odoo import _
from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping
from odoo.addons.queue_job.exception import NothingToDoJob, FailedJobError
from ...components.mapper import normalize_datetime
from ...exception import OrderImportRuleRetry

_logger = logging.getLogger(__name__)


class SaleOrderBatchImporter(Component):
    _name = 'magento.sale.order.batch.importer'
    _inherit = 'magento.delayed.batch.importer'
    _apply_on = 'magento.sale.order'

    def _import_record(self, external_id, job_options=None, **kwargs):
        job_options = {
            'max_retries': 0,
            'priority': 5,
        }
        return super(SaleOrderBatchImporter, self)._import_record(
            external_id, job_options=job_options)

    def run(self, filters=None):
        """ Run the synchronization """
        if filters is None:
            filters = {}
        filters['state'] = {'neq': 'canceled'}
        from_date = filters.pop('from_date', None)
        to_date = filters.pop('to_date', None)
        magento_storeview_ids = [filters.pop('magento_storeview_id')]
        external_ids = self.backend_adapter.search(
            filters,
            from_date=from_date,
            to_date=to_date,
            magento_storeview_ids=magento_storeview_ids)
        _logger.info('search for magento saleorders %s returned %s',
                     filters, external_ids)
        for external_id in external_ids:
            self._import_record(external_id)


class SaleImportRule(Component):
    _name = 'magento.sale.import.rule'
    _inherit = 'base.magento.connector'
    _apply_on = 'magento.sale.order'
    _usage = 'sale.import.rule'

    def _rule_always(self, record, method):
        """ Always import the order """
        return True

    def _rule_never(self, record, method):
        """ Never import the order """
        raise NothingToDoJob('Orders with payment method %s '
                             'are never imported.' %
                             record['payment']['method'])

    def _rule_authorized(self, record, method):
        """ Import the order only if payment has been authorized. """
        if not record.get('payment', {}).get('base_amount_authorized'):
            raise OrderImportRuleRetry('The order has not been authorized.\n'
                                       'The import will be retried later.')

    def _rule_paid(self, record, method):
        """ Import the order only if it has received a payment """
        if not record.get('payment', {}).get('amount_paid'):
            raise OrderImportRuleRetry('The order has not been paid.\n'
                                       'The import will be retried later.')

    _rules = {'always': _rule_always,
              'paid': _rule_paid,
              'authorized': _rule_authorized,
              'never': _rule_never,
              }

    def _rule_global(self, record, method):
        """ Rule always executed, whichever is the selected rule """
        # the order has been canceled since the job has been created
        order_id = record['increment_id']
        if record['state'] == 'canceled':
            raise NothingToDoJob('Order %s canceled' % order_id)
        max_days = method.days_before_cancel
        if max_days:
            fmt = '%Y-%m-%d %H:%M:%S'
            order_date = datetime.strptime(record['created_at'], fmt)
            if order_date + timedelta(days=max_days) < datetime.now():
                raise NothingToDoJob('Import of the order %s canceled '
                                     'because it has not been paid since %d '
                                     'days' % (order_id, max_days))

    def check(self, record):
        """ Check whether the current sale order should be imported
        or not. It will actually use the payment method configuration
        and see if the choosed rule is fullfilled.

        :returns: True if the sale order should be imported
        :rtype: boolean
        """
        payment_method = record['payment']['method']
        binder = self.binder_for('magento.account.payment.mode')
        method = binder.to_internal(payment_method, unwrap=True)
        if not method:
            raise FailedJobError(
                "The configuration is missing for the Payment Mode '%s'.\n\n"
                "Resolution:\n"
                "- Create a new Payment Method Mapping" % (payment_method,))
        self._rule_global(record, method)
        self._rules[method.import_rule](self, record, method)


class SaleOrderImportMapper(Component):

    _name = 'magento.sale.order.mapper'
    _inherit = 'magento.import.mapper'
    _apply_on = 'magento.sale.order'

    direct = [('increment_id', 'external_id'),
              ('order_id', 'magento_order_id'),
              ('grand_total', 'total_amount'),
              ('tax_amount', 'total_amount_tax'),
              (normalize_datetime('created_at'), 'date_order'),
              ('store_id', 'storeview_id'),
              ('coupon_code', 'webshop_coupon_code'),
              ]

    children = [
        ('items', 'magento_order_line_ids', 'magento.sale.order.line'),
        ('status_histories', 'magento_order_history_ids', 'magento.sale.order.historie')
    ]

    def _add_shipping_line(self, map_record, values):
        record = map_record.source
        amount_incl = float(record.get('base_shipping_incl_tax') or 0.0)
        amount_excl = float(record.get('shipping_amount') or 0.0)
        line_builder = self.component(usage='order.line.builder.shipping')
        # add even if the price is 0, otherwise odoo will add a shipping
        # line in the order when we ship the picking
        if self.options.tax_include:
            discount = float(record.get('shipping_discount_amount') or 0.0)
            line_builder.price_unit = (amount_incl - discount)
        else:
            line_builder.price_unit = amount_excl

        if values.get('carrier_id'):
            carrier = self.env['delivery.carrier'].browse(values['carrier_id'])
            line_builder.product = carrier.product_id

        line = (0, 0, line_builder.get_line())
        values['order_line'].append(line)
        return values

    def _add_cash_on_delivery_line(self, map_record, values):
        record = map_record.source
        if 'extension_attributes' not in record or 'cash_on_delivery' not in record['extension_attributes']:
            return values
        amount_excl = float(record['extension_attributes']['cash_on_delivery'].get('fee') or 0.0)
        amount_incl = float(record['extension_attributes']['cash_on_delivery'].get('fee_incl_tax') or 0.0)
        if not (amount_excl or amount_incl):
            return values
        tax_include = self.options.tax_include
        line = {
            'product_id': self.backend_record.default_cod_product_id.id,
            'price_unit': amount_incl if tax_include else amount_excl,
            'product_uom_qty': 1,
        }
        values['order_line'].append((0, 0, line))
        return values

    def _add_gift_certificate_line(self, map_record, values):
        record = map_record.source
        if 'parent_item' in record:
            # Discount values are in the parent record if present
            record = record['parent_item']
        if 'discount_amount' not in record:
            return values
        # if gift_cert_amount is zero
        if not record.get('discount_amount'):
            return values
        # If discount_percent is set - then we did already used this in the line mapping
        _logger.info("Discount percent is: %s", record.get('discount_percent', 0))
        if float(record.get('discount_percent', 0)) > 0:
            _logger.info("Do not add extra discount line - it is a discount percent line")
            return values
        if float(record.get('discount_tax_compensation_amount', 0)) > 0:
            _logger.info("Do not add extra discount line - it is a tax relevant discount amount line")
            return values
        amount = float(record['discount_amount'])
        name = 'Gift'
        if 'discount_description' in record:
            name = record['discount_description']
        if 'discount_code' in record:
            name = "%s (%s)" % (name, record['discount_code'], )
        line = {
            'product_id': self.backend_record.default_gift_product_id.id,
            'price_unit': amount,
            'name': name,
            'product_uom_qty': 1,
        }
        values['order_line'].append((0, 0, line))
        return values

    def finalize(self, map_record, values):
        values.setdefault('order_line', [])
        values = self._add_shipping_line(map_record, values)
        values = self._add_cash_on_delivery_line(map_record, values)
        values = self._add_gift_certificate_line(map_record, values)
        values.update({
            'partner_id': self.options.partner_id,
            'partner_invoice_id': self.options.partner_invoice_id,
            'partner_shipping_id': self.options.partner_shipping_id,
        })
        onchange = self.component(
            usage='ecommerce.onchange.manager.sale.order'
        )
        return onchange.play(values, values['magento_order_line_ids'])

    @mapping
    def name(self, record):
        name = record['increment_id']
        prefix = self.backend_record.sale_prefix
        if prefix:
            name = prefix + name
        return {'name': name}

    @mapping
    def customer_id(self, record):
        binder = self.binder_for('magento.res.partner')
        partner = binder.to_internal(record['customer_id'], unwrap=True)
        assert partner, (
            "customer_id %s should have been imported in "
            "SaleOrderImporter._import_dependencies" % record['customer_id'])
        return {'partner_id': partner.id}

    @mapping
    def payment(self, record):
        record_method = record['payment']['method']
        binder = self.binder_for('magento.account.payment.mode')
        method = binder.to_internal(record_method, unwrap=True)
        assert method, ("method %s should exist because the import fails "
                        "in SaleOrderImporter._before_import when it is "
                        " missing" % record['payment']['method'])
        return {'payment_mode_id': method.id}

    @mapping
    def shipping_method(self, record):
        ext_field = record.get('extension_attributes')
        if not ext_field:
            return
        ship_asg = ext_field['shipping_assignments'][0]
        if not ship_asg:
            return
        if not 'method' in ship_asg['shipping']:
            return
        ifield = ship_asg['shipping']['method']
    
        carrier = self.env['delivery.carrier'].search(
            [('magento_code', '=', ifield)],
            limit=1,
        )
        if carrier:
            result = {'carrier_id': carrier.id}
        else:
            # FIXME: a mapper should not have any side effects
            product = self.env.ref(
                'connector_ecommerce.product_product_shipping')
            carrier = self.env['delivery.carrier'].create({
                'product_id': product.id,
                'name': ifield,
                'magento_code': ifield})
            result = {'carrier_id': carrier.id}
        return result

    @mapping
    def sales_team(self, record):
        team = self.options.storeview.team_id
        if team:
            return {'team_id': team.id}

    @mapping
    def project_id(self, record):
        project_id = self.options.storeview.account_analytic_id
        if project_id:
            return {'project_id': project_id.id}

    @mapping
    def fiscal_position(self, record):
        fiscal_position = self.options.storeview.fiscal_position_id
        if fiscal_position:
            return {'fiscal_position_id': fiscal_position.id}

    @mapping
    def warehouse_id(self, record):
        warehouse = self.options.storeview.warehouse_id
        if warehouse:
            return {'warehouse_id': warehouse.id}

    @mapping
    def pricelist_id(self, record):
        if self.backend_record.default_pricelist_id:
            return {'pricelist_id': self.backend_record.default_pricelist_id.id}

    # partner_id, partner_invoice_id, partner_shipping_id
    # are done in the importer

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}

    @mapping
    def user_id(self, record):
        """ Do not assign to a Salesperson otherwise sales orders are hidden
        for the salespersons (access rules)"""
        return {'user_id': False}


class SaleOrderImporter(Component):
    _name = 'magento.sale.order.importer'
    _inherit = 'magento.importer'
    _apply_on = 'magento.sale.order'

    def _must_skip(self):
        """ Hook called right after we read the data from the backend.

        If the method returns a message giving a reason for the
        skipping, the import will be interrupted and the message
        recorded in the job (if the import is called directly by the
        job, not by dependencies).

        If it returns None, the import will continue normally.

        :returns: None | str | unicode
        """
        if self.binder.to_internal(self.external_id):
            return _('Already imported')

    def _clean_magento_items(self, resource):
        """
        Method that clean the sale order line given by magento before
        importing it

        This method has to stay here because it allow to customize the
        behavior of the sale order.

        """
        top_items = []

        # Remove top level configurable items
        for item in resource['items']:
            if item.get('product_type') and item.get('product_type') == 'configurable':
                continue
            if item.get('product_type') and item.get('product_type') == 'bundle':
                item['bundle_items'] = [];
                # We do append the child items to the bundle item - so the mapper does have them available in the record
                for subitem in resource['items']:
                    if subitem.get('parent_item', False) and subitem['parent_item']['item_id'] == item['item_id']:
                        item['bundle_items'].append(subitem)
            top_items.append(item)
        resource['items'] = top_items
        return resource

    def _import_customer_group(self, group_id):
        self._import_dependency(group_id, 'magento.res.partner.category')

    def _before_import(self):
        rules = self.component(usage='sale.import.rule')
        rules.check(self.magento_record)

    def _link_parent_orders(self, binding):
        """ Link the magento.sale.order to its parent orders.

        When a Magento sales order is modified, it:
         - cancel the sales order
         - create a copy and link the canceled one as a parent

        So we create the link to the parent sales orders.
        Note that we have to walk through all the chain of parent sales orders
        in the case of multiple editions / cancellations.
        """
        parent_id = self.magento_record.get('relation_parent_id')
        if not parent_id:
            return
        all_parent_ids = []
        while parent_id:
            all_parent_ids.append(parent_id)
            parent_id = self.backend_adapter.get_parent(parent_id)
        current_binding = binding
        for parent_id in all_parent_ids:
            parent_binding = self.binder.to_internal(parent_id)
            if not parent_binding:
                # may happen if several sales orders have been
                # edited / canceled but not all have been imported
                continue
            # link to the nearest parent
            current_binding.write({'magento_parent_id': parent_binding.id})
            parent_canceled = parent_binding.canceled_in_backend
            if not parent_canceled:
                parent_binding.write({'canceled_in_backend': True})
            current_binding = parent_binding

    def _create(self, data):
        binding = super(SaleOrderImporter, self)._create(data)
        if binding.fiscal_position_id:
            binding.odoo_id._compute_tax_id()
        return binding

    def _link_messages(self, binding):
        for historie in binding.magento_order_history_ids:
            historie.update({
                'model': 'sale.order',
                'res_id': binding.odoo_id.id
            })

    def _import_payment(self, binding):
        payment = self.magento_record['payment']
        if payment.get('amount_paid', 0.0) == 0.0:
            # No payment !
            return
        binder = self.binder_for('magento.account.payment')
        payment_binding = binder.to_internal(payment['entity_id'])
        if not payment_binding:
            importer = self.component(usage='record.importer',
                                      model_name='magento.account.payment')
            importer.run_with_data(payment, order_binding=binding)

    def _after_import(self, binding):
        self._link_parent_orders(binding)
        self._link_messages(binding)
        self._import_payment(binding)

    def _get_storeview(self, record):
        """ Return the tax inclusion setting for the appropriate storeview """
        storeview_binder = self.binder_for('magento.storeview')
        # we find storeview_id in store_id!
        # (http://www.magentocommerce.com/bug-tracking/issue?issue=15886)
        return storeview_binder.to_internal(record['store_id'])

    def _get_magento_data(self, binding=None):
        """ Return the raw Magento data for ``self.external_id`` """
        record = super(SaleOrderImporter, self)._get_magento_data(binding)
        # sometimes we don't have website_id...
        # we fix the record!
        if not record.get('website_id'):
            storeview = self._get_storeview(record)
            # deduce it from the storeview
            record['website_id'] = storeview.store_id.website_id.external_id
        # sometimes we need to clean magento items (ex : configurable
        # product in a sale)
        # Not needed anymore - product bundle support is here
        record = self._clean_magento_items(record)
        return record
    
    def _get_shipping_address(self):
        if self.collection.version == '1.7':
            return self.magento_record['shipping_address']
        elif self.collection.version == '2.0':
            # TODO: Magento2 allows for a different shipping address per line.
            # Look to https://github.com/OCA/sale-workflow/tree/8.0/sale_allotment?
            shippings = self.magento_record['extension_attributes']['shipping_assignments']
            return shippings and shippings[0]['shipping'].get('address')

    def _import_addresses(self):
        record = self.magento_record

        # Magento allows to create a sale order not registered as a user
        is_guest_order = bool(int(record.get('customer_is_guest', 0) or 0))

        # For a guest order or when magento does not provide customer_id
        # on a non-guest order (it happens, Magento inconsistencies are
        # common)
        if (is_guest_order or not record.get('customer_id')):
            website_binder = self.binder_for('magento.website')
            website_binding = website_binder.to_internal(record['website_id'])

            # search an existing partner with the same email
            partner = self.env['magento.res.partner'].search(
                [('emailid', '=', record['customer_email']),
                 ('website_id', '=', website_binding.id)],
                limit=1)

            # if we have found one, we "fix" the record with the magento
            # customer id
            if partner:
                magento = partner.external_id
                # If there are multiple orders with "customer_id is
                # null" and "customer_is_guest = 0" which share the same
                # customer_email, then we may get a external_id that is a
                # marker 'guestorder:...' for a guest order (which is
                # set below).  This causes a problem with
                # "importer.run..." below where the id is cast to int.
                if str(magento).startswith('guestorder:'):
                    is_guest_order = True
                else:
                    record['customer_id'] = magento

            # no partner matching, it means that we have to consider it
            # as a guest order
            else:
                is_guest_order = True

        partner_binder = self.binder_for('magento.res.partner')
        if is_guest_order:
            # ensure that the flag is correct in the record
            record['customer_is_guest'] = True
            guest_customer_id = 'guestorder:%s' % record['increment_id']
            # "fix" the record with a on-purpose built ID so we can found it
            # from the mapper
            record['customer_id'] = guest_customer_id

            address = record['billing_address']

            customer_group = record.get('customer_group_id')
            if customer_group:
                self._import_customer_group(customer_group)

            customer_record = {
                'firstname': address['firstname'],
                'middlename': address.get('middlename'),
                'lastname': address['lastname'],
                'prefix': address.get('prefix'),
                'suffix': address.get('suffix'),
                'telephone': address.get('telephone'),
                'email': record.get('customer_email'),
                'taxvat': record.get('customer_taxvat'),
                'group_id': customer_group,
                'gender': record.get('customer_gender'),
                'store_id': record['store_id'],
                'created_at': normalize_datetime('created_at')(self,
                                                               record, ''),
                'updated_at': False,
                'created_in': False,
                'dob': record.get('customer_dob'),
                'website_id': record.get('website_id'),
            }
            mapper = self.component(usage='import.mapper',
                                    model_name='magento.res.partner')
            map_record = mapper.map_record(customer_record)
            map_record.update(guest_customer=True)
            partner_binding = self.env['magento.res.partner'].with_context(connector_no_export=True).create(
                map_record.values(for_create=True))
            partner_binder.bind(guest_customer_id, partner_binding)
        else:

            # we always update the customer when importing an order
            importer = self.component(usage='record.importer',
                                      model_name='magento.res.partner')
            importer.run(record['customer_id'])
            partner_binding = partner_binder.to_internal(record['customer_id'])

        partner = partner_binding.odoo_id

        # Import of addresses. We just can't rely on the
        # ``customer_address_id`` field given by Magento, because it is
        # sometimes empty and sometimes wrong.

        # The addresses of the sale order are imported as active=false
        # so they are linked with the sale order but they are not displayed
        # in the customer form and the searches.

        # We import the addresses of the sale order as Active = False
        # so they will be available in the documents generated as the
        # sale order or the picking, but they won't be available on
        # the partner form or the searches. Too many adresses would
        # be displayed.
        # They are never synchronized.
        addresses_defaults = {'parent_id': partner.id,
                              'magento_partner_id': partner_binding.id,
                              'email': record.get('customer_email', False),
                              'active': True,
                              'is_magento_order_address': True}

        addr_mapper = self.component(usage='import.mapper',
                                     model_name='magento.address')

        def create_address(address_record, type):
            map_record = addr_mapper.map_record(address_record)
            map_record.update(addresses_defaults)
            map_record.update({
                'type': type
            })
            address_bind = self.env['magento.address'].with_context(connector_no_export=True).create(
                map_record.values(for_create=True,
                                  parent_partner=partner))
            return address_bind.odoo_id.id

        billing_id = create_address(record['billing_address'], 'invoice')

        shipping_id = None
        shipping_address = self._get_shipping_address()
        if shipping_address:
            shipping_id = create_address(shipping_address, 'delivery')

        self.partner_id = partner.id
        self.partner_invoice_id = billing_id
        self.partner_shipping_id = shipping_id or billing_id

    def _check_special_fields(self):
        assert self.partner_id, (
            "self.partner_id should have been defined "
            "in SaleOrderImporter._import_addresses")
        assert self.partner_invoice_id, (
            "self.partner_id should have been "
            "defined in SaleOrderImporter._import_addresses")
        assert self.partner_shipping_id, (
            "self.partner_id should have been defined "
            "in SaleOrderImporter._import_addresses")

    def _create_data(self, map_record, **kwargs):
        storeview = self._get_storeview(map_record.source)
        self._check_special_fields()
        return super(SaleOrderImporter, self)._create_data(
            map_record,
            tax_include=storeview.catalog_price_tax_included,
            partner_id=self.partner_id,
            partner_invoice_id=self.partner_invoice_id,
            partner_shipping_id=self.partner_shipping_id,
            storeview=storeview,
            **kwargs)

    def _update_data(self, map_record, **kwargs):
        storeview = self._get_storeview(map_record.source)
        self._check_special_fields()
        return super(SaleOrderImporter, self)._update_data(
            map_record,
            tax_include=storeview.catalog_price_tax_included,
            partner_id=self.partner_id,
            partner_invoice_id=self.partner_invoice_id,
            partner_shipping_id=self.partner_shipping_id,
            storeview=storeview,
            **kwargs)

    def _import_dependencies(self):
        record = self.magento_record

        self._import_addresses()

        for line in record.get('items', []):
            _logger.debug('line: %s', line)
            field = self.collection.version == '1.7' and 'product_id' or 'sku'
            model = 'magento.product.product'
            if field in line:
                if 'product_type' in line and line['product_type'] == 'bundle':
                    model = 'magento.product.bundle'
                self._import_dependency(line[field], model)


class SaleOrderLineImportMapper(Component):
    _name = 'magento.sale.order.line.mapper'
    _inherit = 'magento.import.mapper'
    _apply_on = 'magento.sale.order.line'

    direct = [('item_id', 'external_id'),]

    @mapping
    def quantity(self, record):
        return {
            'product_uom_qty': record['qty_ordered'],
            'product_qty': record['qty_ordered'],
        }

    @mapping
    def name(self, record):
        name = record['name']
        if record['product_type'] == 'bundle':
            # We do provide extra information about the bundle items here
            for item in record['bundle_items']:
                name = "%s\n\t* %s" % (name, item['name'], )
        return {'name': name}

    @mapping
    def discount_amount(self, record):
        if record.get('parent_item'):
            # Use parent item here if it is set
            record = record.get('parent_item')
        discount_value = float(record.get('discount_amount') or 0)
        discount_tax_compensation_amount = float(record.get('discount_tax_compensation_amount') or 0)
        if self.options.tax_include:
            row_total = float(record.get('row_total_incl_tax') or 0)
        else:
            row_total = float(record.get('row_total') or 0)
        discount = 0
        if discount_value > 0 and row_total > 0 and discount_tax_compensation_amount > 0:
            # We do use the discount value if it is tax relevant
            discount = 100 * discount_value / row_total
        result = {'discount': discount}
        return result
    
    def _get_product_ref(self, record):
        if self.collection.version == '2.0':
            return record['sku']
        return record['product_id']

    @mapping
    def is_bundle_item(self, record):
        if 'parent_item' in record and record['parent_item']['product_type'] == 'bundle':
            # Set Qty to invoice to zero on items of a bundle
            return {'is_bundle_item': True}

    @mapping
    def shipping_item_id(self, record):
        if 'parent_item' in record and 'item_id' in record['parent_item']:
            return {'shipping_item_id': record['parent_item']['item_id']}
        else:
            return {'shipping_item_id': record['item_id']}

    @mapping
    def product_id(self, record):
        model = 'magento.product.product'
        if 'product_type' in record and record['product_type'] == 'bundle':
            model = 'magento.product.bundle'

        binder = self.binder_for(model)
        product_ref = self._get_product_ref(record)
        product = binder.to_internal(product_ref, unwrap=True)
        assert product, (
            "product_id %s should have been imported in "
            "SaleOrderImporter._import_dependencies" % record['product_id'])
        return {'product_id': product.id}

    @mapping
    def product_options(self, record):
        result = {}
        ifield = record.get('product_options')
        if ifield:
            import re
            options_label = []
            clean = re.sub(r'\w:\w:|\w:\w+;', '', ifield)
            for each in clean.split('{'):
                if each.startswith('"label"'):
                    split_info = each.split(';')
                    options_label.append('%s: %s [%s]' % (split_info[1],
                                                          split_info[3],
                                                          record['sku']))
            notes = "".join(options_label).replace('""', '\n').replace('"', '')
            result = {'notes': notes}
        return result

    @mapping
    def price(self, record):
        if 'parent_item' in record and record['parent_item']['product_type'] == 'bundle':
            # This is part of a bundle product - so price here is zero
            return {'price_unit': 0}
        if record.get('parent_item'):
            # Use parent item here if it is set
            record = record.get('parent_item')
        """ tax key may not be present in magento2 when no taxes apply """
        result = {}
        base_row_total = float(record['base_row_total'] or 0.)
        base_row_total_incl_tax = float(
            record.get('base_row_total_incl_tax') or base_row_total)
        qty_ordered = float(record['qty_ordered'])
        if self.options.tax_include:
            result['price_unit'] = base_row_total_incl_tax / qty_ordered
        else:
            result['price_unit'] = base_row_total / qty_ordered
        return result


class SaleOrderHistorieImportMapper(Component):
    _name = 'magento.sale.order.historie.mapper'
    _inherit = 'magento.import.mapper'
    _apply_on = 'magento.sale.order.historie'

    direct = [('entity_name', 'entity_name'),
              ('entity_id', 'external_id'),
              ('status', 'status'),
              ('comment', 'body'),
              (normalize_datetime('created_at'), 'date'),
              ]

    @mapping
    def message_type(self, record):
        return {'message_type': 'notification'}

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}
