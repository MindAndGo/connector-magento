# -*- coding: utf-8 -*-
# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from contextlib import contextmanager

from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError

from odoo.addons.component.core import Component
from odoo.addons.connector.checkpoint import checkpoint
from ...components.backend_adapter import MagentoLocation, MagentoAPI
from odoo.addons.queue_job.job import identity_exact

_logger = logging.getLogger(__name__)

IMPORT_DELTA_BUFFER = 30  # seconds


class MagentoBackend(models.Model):
    _name = 'magento.backend'
    _description = 'Magento Backend'
    _inherit = 'connector.backend'

    @api.model
    def select_versions(self):
        """ Available versions in the backend.

        Can be inherited to add custom versions.  Using this method
        to add a version from an ``_inherit`` does not constrain
        to redefine the ``version`` field in the ``_inherit`` model.
        """
        return [('1.7', '1.7+'),
                ('2.0', '2.0+') ]


    @api.model
    def _get_stock_field_id(self):
        field = self.env['ir.model.fields'].search(
            [('model', '=', 'product.product'),
             ('name', '=', 'virtual_available')],
            limit=1)
        return field

    @api.model
    def _select_state(self):
        """Available States for this Backend"""
        return [('draft', 'Draft'),
                ('checked', 'Checked'),
                ('production', 'In Production')]

    @api.multi
    def button_reset_to_draft(self):
        self.ensure_one()
        self.write({'state': 'draft'})

    @api.multi
    def _check_connection(self):
        self.ensure_one()
        with self.work_on('magento.backend') as work:
            component = work.component_by_name(name='magento.adapter.test')
            with api_handle_errors('Connection failed'):
                component.head()

    @api.multi
    def button_check_connection(self):
        # TODO : use self._check_connection() as in connector prestashop
        # raise exceptions.UserError(_('Connection successful'))
        self.write({'state': 'checked'})

    active = fields.Boolean(
        string='Active',
        default=True
    )
    state = fields.Selection(
        selection='_select_state',
        string='State',
        default='draft'
    )
    version = fields.Selection(selection='select_versions', required=True)
    location = fields.Char(
        string='Location',
        required=True,
        help="Url to magento application",
    )
    admin_location = fields.Char(string='Admin Location')
    use_custom_api_path = fields.Boolean(
        string='Custom Api Path',
        help="The default API path is '/index.php/api/xmlrpc'. "
             "Check this box if you use a custom API path, in that case, "
             "the location has to be completed with the custom API path ",
    )
    username = fields.Char(
        string='Username',
        help="Webservice user (leave empty for Magento 2.0)",
    )
    password = fields.Char(
        string='Password',
        help="Webservice password, or authentication token when connecting to"
              " Magento 2.0")
    verify_ssl = fields.Boolean(
        string="Verify SSL certficate",
        default=True,
        help="Only for Magento 2 REST API")
    use_auth_basic = fields.Boolean(
        string='Use HTTP Auth Basic',
        help="Use a Basic Access Authentication for the API. "
             "The Magento server could be configured to restrict access "
             "using a HTTP authentication based on a username and "
             "a password.",
    )
    auth_basic_username = fields.Char(
        string='Basic Auth. Username',
        help="Basic access authentication web server side username",
    )
    auth_basic_password = fields.Char(
        string='Basic Auth. Password',
        help="Basic access authentication web server side password",
    )
    sale_prefix = fields.Char(
        string='Sale Prefix',
        help="A prefix put before the name of imported sales orders.\n"
             "For instance, if the prefix is 'mag-', the sales "
             "order 100000692 in Magento, will be named 'mag-100000692' "
             "in Odoo.",
    )
    export_all_options = fields.Boolean(
        string='Export and Delete all attribute options',
        default=False
    )
    always_create_new_attributes = fields.Boolean(
        string='Always create new odoo attributes on import',
        default=True
    )
    rename_duplicate_values = fields.Boolean(
        string='Rename duplicate values in Odoo',
        default=True
    )
    warehouse_id = fields.Many2one(
        comodel_name='stock.warehouse',
        string='Warehouse',
        required=True,
        help='Warehouse used to compute the '
             'stock quantities.',
    )
    weight_uom_id = fields.Many2one(
        comodel_name='product.uom',
        string='Unit for weight used in Magento',
        required=True,
        help='Helps to convert weight ',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        related='warehouse_id.company_id',
        string='Company',
        readonly=True,
    )
    website_ids = fields.One2many(
        comodel_name='magento.website',
        inverse_name='backend_id',
        string='Website',
        readonly=True,
    )
    default_lang_id = fields.Many2one(
        comodel_name='res.lang',
        string='Default Language',
        help="If a default language is selected, the records "
             "will be imported in the translation of this language.\n"
             "Note that a similar configuration exists "
             "for each storeview.",
    )
    default_pricelist_id = fields.Many2one('product.pricelist', string="Default pricelist")
    default_category_id = fields.Many2one(
        comodel_name='product.category',
        string='Default Product Category',
        help='If a default category is selected, products imported '
             'without a category will be linked to it.',
    )
    default_attribute_group_id = fields.Many2one(
        comodel_name='magento.product.attributes.group',
        string='Default Attribute Group'
    )
    default_code_method = fields.Selection([
        ('none', 'Do not touch odoo default code'),
        ('update', 'Do update odoo default code if not already set'),
        ('overwrite', 'Do overwrite odoo default code'),
    ], default='none', string='Default code handling')
    default_cod_product_id = fields.Many2one(
        'product.product',
        string="COD Product",
        domain="[('type', '=', 'service')]"
    )
    default_gift_product_id = fields.Many2one(
        'product.product',
        string="Gift Product",
        domain="[('type', '=', 'service')]"
    )
    auto_create_category = fields.Boolean('Auto Create Category On export', default=True)
    auto_create_category_on_import = fields.Boolean('Auto Create Category On Import', default=True)

    # TODO? add a field `auto_activate` -> activate a cron
    import_products_from_date = fields.Datetime(
        string='Import products from date',
    )
    import_product_templates_from_date = fields.Datetime(
        string='Import product templates from date',
    )
    import_product_bundles_from_date = fields.Datetime(
        string='Import product bundles from date',
    )
    import_categories_from_date = fields.Datetime(
        string='Import categories from date',
    )
    product_stock_field_id = fields.Many2one(
        comodel_name='ir.model.fields',
        string='Stock Field',
        default=_get_stock_field_id,
        domain="[('model', 'in', ['product.product', 'product.template']),"
               " ('ttype', '=', 'float')]",
        help="Choose the field of the product which will be used for "
             "stock inventory updates.\nIf empty, Quantity Available "
             "is used.",
    )
    no_stock_sync = fields.Boolean(
        string='No Stock Synchronization',
        required=False,
        default=False,
        help="Check this to default exclude new products "
             "from stock synchronizations.",
    )
    product_binding_ids = fields.One2many(
        comodel_name='magento.product.product',
        inverse_name='backend_id',
        string='Magento Products',
        readonly=True,
    )
    account_analytic_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string='Analytic account',
        help='If specified, this analytic account will be used to fill the '
        'field  on the sale order created by the connector. The value can '
        'also be specified on website or the store or the store view.'
    )
    fiscal_position_id = fields.Many2one(
        comodel_name='account.fiscal.position',
        string='Fiscal position',
        help='If specified, this fiscal position will be used to fill the '
        'field fiscal position on the sale order created by the connector.'
        'The value can also be specified on website or the store or the '
        'store view.'
    )
    rounding_diff_account_id = fields.Many2one(
        comodel_name='account.account',
        string='Rounding Diff Account'
    )
    is_multi_company = fields.Boolean(
        string='Is Backend Multi-Company',
        help="If this flag is set, it is possible to choose warehouse at each "
        "level. "
        "When import partner, ignore company_id if this flag is set.",
    )

    _sql_constraints = [
        ('sale_prefix_uniq', 'unique(sale_prefix)',
         "A backend with the same sale prefix already exists")
    ]

    @api.multi
    def check_magento_structure(self):
        """ Used in each data import.

        Verify if a website exists for each backend before starting the import.
        """
        for backend in self:
            websites = backend.website_ids
            if not websites:
                backend.synchronize_metadata()
        return True

    @contextmanager
    @api.multi
    def work_on(self, model_name, **kwargs):
        self.ensure_one()
        lang = self.default_lang_id
        if lang.code != self.env.context.get('lang'):
            self = self.with_context(lang=lang.code)
        magento_location = MagentoLocation(
            self.location,
            self.username,
            self.password,
            self.version,
            use_custom_api_path=self.use_custom_api_path,
            verify_ssl=self.verify_ssl,
        )
        if self.use_auth_basic:
            magento_location.use_auth_basic = True
            magento_location.auth_basic_username = self.auth_basic_username
            magento_location.auth_basic_password = self.auth_basic_password
        # We create a Magento Client API here, so we can create the
        # client once (lazily on the first use) and propagate it
        # through all the sync session, instead of recreating a client
        # in each backend adapter usage.
        with MagentoAPI(magento_location) as magento_api:
            _super = super(MagentoBackend, self)
            # from the components we'll be able to do: self.work.magento_api
            with _super.work_on(
                    model_name, magento_api=magento_api, **kwargs) as work:
                yield work

    @api.multi
    def add_checkpoint(self, record):
        self.ensure_one()
        record.ensure_one()
        return checkpoint.add_checkpoint(self.env, record._name, record.id,
                                         self._name, self.id)

    @api.multi
    def synchronize_metadata(self):
        try:
            for backend in self:
                for model_name in ('magento.website',
                                   'magento.store',
                                   'magento.storeview'):
                    # import directly, do not delay because this
                    # is a fast operation, a direct return is fine
                    # and it is simpler to import them sequentially
                    self.env[model_name].import_batch(backend)
            return True
        except Exception as e:
            _logger.error(e.message, exc_info=True)
            raise UserError(
                _(u"Check your configuration, we can't get the data. "
                  u"Here is the error:\n%s") %
                str(e).decode('utf-8', 'ignore'))

    @api.multi
    def button_resync_products(self):
        for backend in self:
            for model_name in ('magento.product.template',
                               'magento.product.bundle',
                               'magento.product.product'):
                self.env[model_name].search([('backend_id', '=', backend.id)]).with_delay(identity_key=identity_exact).sync_from_magento()

    @api.multi
    def button_check_products(self):
        for backend in self:
            with backend.work_on("magento.product.template") as work:
                adapter = work.component(usage='backend.adapter')
                filters = {}
                products = adapter.search_read(filters)
                tskus = []
                pskus = []
                for product in products['items']:
                    if product['type_id'] == 'configurable':
                        tskus.append(product['sku'])
                        binding = self.env['magento.product.template'].search([
                            ('backend_id', '=', backend.id),
                            ('external_id', '=', product['sku']),
                            ('active', 'in', [True,False]),
                        ])
                    else:
                        pskus.append(product['sku'])
                        binding = self.env['magento.product.product'].search([
                            ('backend_id', '=', backend.id),
                            ('external_id', '=', product['sku']),
                            ('active', 'in', [True, False]),
                        ])
                    if not binding:
                        _logger.info("Found Magento product without binding: %s with status=%s,type=%s", product['sku'], product['status'], product['type_id'])
                        if product['type_id'] == 'simple':
                            # Do delete the magento product
                            adapter.delete(product['sku'])
                            continue
                        continue
                    if binding.magento_id != product['id']:
                        _logger.info("Binding ID does not match magento ID !. %s, %s", product, binding)
                    if not binding.magento_url_key:
                        for cattribute in product['custom_attributes']:
                            if cattribute['attribute_code'] == 'url_key' and cattribute['value']:
                                _logger.info("Do update stored url key to %s", cattribute['value'])
                                binding.with_context(connector_no_export=True).magento_url_key = cattribute['value']
                tbindings = self.env['magento.product.template'].search([
                    ('backend_id', '=', backend.id),
                    ('external_id', 'not in', tskus)
                ])
                if tbindings:
                    _logger.info("These template bindings do not have a magento configurable: %s", tbindings)
                pbindings = self.env['magento.product.product'].search([
                    ('backend_id', '=', backend.id),
                    ('external_id', 'not in', pskus)
                ])
                if pbindings:
                    _logger.info("These product bindings do not have a magento product: %s", pbindings)

    @api.multi
    def import_partners(self):
        """ Import partners from all websites """
        for backend in self:
            backend.check_magento_structure()
            backend.website_ids.import_partners()
        return True

    @api.multi
    def import_sale_orders(self):
        """ Import sale orders from all store views """
        storeview_obj = self.env['magento.storeview']
        storeviews = storeview_obj.search([('backend_id', 'in', self.ids)])
        storeviews.import_sale_orders()
        return True

    @api.multi
    def import_tax_classes(self):
        """ Import tax class """
        for backend in self:
            self.env['magento.account.tax'].import_batch(backend)
        return True

    @api.multi
    def import_customer_groups(self):
        for backend in self:
            backend.check_magento_structure()
            self.env['magento.res.partner.category'].with_delay(identity_key=identity_exact).import_batch(
                backend,
            )
        return True

    @api.multi
    def _import_from_date(self, model, from_date_field):
        import_start_time = datetime.now()
        for backend in self:
            backend.check_magento_structure()
            from_date = backend[from_date_field]
            if from_date:
                from_date = fields.Datetime.from_string(from_date)
            else:
                from_date = None
            self.env[model].with_delay(identity_key=identity_exact).import_batch(
                backend,
                filters={'from_date': from_date,
                         'to_date': import_start_time}
            )
        # Records from Magento are imported based on their `created_at`
        # date.  This date is set on Magento at the beginning of a
        # transaction, so if the import is run between the beginning and
        # the end of a transaction, the import of a record may be
        # missed.  That's why we add a small buffer back in time where
        # the eventually missed records will be retrieved.  This also
        # means that we'll have jobs that import twice the same records,
        # but this is not a big deal because they will be skipped when
        # the last `sync_date` is the same.
        next_time = import_start_time - timedelta(seconds=IMPORT_DELTA_BUFFER)
        next_time = fields.Datetime.to_string(next_time)
        self.write({from_date_field: next_time})

    @api.multi
    def import_product_categories(self):
        self._import_from_date('magento.product.category',
                               'import_categories_from_date')
        return True

    @api.multi
    def import_product_product(self):
        self._import_from_date('magento.product.product',
                               'import_products_from_date')
        return True

    @api.multi
    def import_product_template(self):
        self._import_from_date('magento.product.template',
                               'import_product_templates_from_date')
        return True

    @api.multi
    def import_product_bundle(self):
        self._import_from_date('magento.product.bundle',
                               'import_product_bundles_from_date')
        return True

    @api.multi
    def import_attributes_set(self):
        """ Import attribute sets from backend """
        for backend in self:
            backend.check_magento_structure()
            self.env['magento.product.attributes.set'].with_delay(identity_key=identity_exact).import_batch(backend)
        return True

    @api.multi
    def update_product_stock_qty(self):
        magento_products = self.env['magento.product.product'].search([
            ('backend_id', 'in', self.ids),
            ('type', '!=', 'service'),
            ('no_stock_sync', '=', False),
        ])
        _logger.info("Got products for stock sync: %s", magento_products)
        for mproduct in magento_products:
            mproduct.magento_stock_item_ids.filtered(lambda si: si.backend_id.id in self.ids).sync_to_magento(True)
        return True

    @api.model
    def _magento_backend(self, callback, domain=None):
        if domain is None:
            domain = []
        backends = self.search(domain)
        if backends:
            getattr(backends, callback)()

    @api.model
    def _scheduler_import_sale_orders(self, domain=None):
        self._magento_backend('import_sale_orders', domain=domain)

    @api.model
    def _scheduler_import_customer_groups(self, domain=None):
        self._magento_backend('import_customer_groups', domain=domain)

    @api.model
    def _scheduler_import_partners(self, domain=None):
        self._magento_backend('import_partners', domain=domain)

    @api.model
    def _scheduler_import_product_categories(self, domain=None):
        self._magento_backend('import_product_categories', domain=domain)

    @api.model
    def _scheduler_import_product_product(self, domain=None):
        self._magento_backend('import_product_product', domain=domain)
        self._magento_backend('import_product_template', domain=domain)
        self._magento_backend('import_product_bundle', domain=domain)

    @api.model
    def _scheduler_update_product_stock_qty(self, domain=None):
        self._magento_backend('update_product_stock_qty', domain=domain)


class MagentoConfigSpecializer(models.AbstractModel):
    _name = 'magento.config.specializer'

    specific_account_analytic_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string='Specific analytic account',
        help='If specified, this analytic account will be used to fill the '
        'field on the sale order created by the connector. The value can '
        'also be specified on website or the store or the store view.'
    )
    specific_fiscal_position_id = fields.Many2one(
        comodel_name='account.fiscal.position',
        string='Specific fiscal position',
        help='If specified, this fiscal position will be used to fill the '
        'field fiscal position on the sale order created by the connector.'
        'The value can also be specified on website or the store or the '
        'store view.'
    )
    specific_warehouse_id = fields.Many2one(
        comodel_name='stock.warehouse',
        string='Specific warehouse',
        help='If specified, this warehouse will be used to load fill the '
        'field warehouse (and company) on the sale order created by the '
        'connector.'
        'The value can also be specified on website or the store or the '
        'store view.'
    )
    account_analytic_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string='Analytic account',
        compute='_compute_account_analytic_id',
    )
    fiscal_position_id = fields.Many2one(
        comodel_name='account.fiscal.position',
        string='Fiscal position',
        compute='_compute_fiscal_position_id',
    )
    warehouse_id = fields.Many2one(
        comodel_name='stock.warehouse',
        string='warehouse',
        compute='_compute_warehouse_id')

    @property
    def _parent(self):
        return getattr(self, self._parent_name)

    @api.multi
    def _compute_account_analytic_id(self):
        for this in self:
            this.account_analytic_id = (
                this.specific_account_analytic_id or
                this._parent.account_analytic_id)

    @api.multi
    def _compute_fiscal_position_id(self):
        for this in self:
            this.fiscal_position_id = (
                this.specific_fiscal_position_id or
                this._parent.fiscal_position_id)

    @api.multi
    def _compute_warehouse_id(self):
        for this in self:
            this.warehouse_id = (
                this.specific_warehouse_id or
                this._parent.warehouse_id)


class NoModelAdapter(Component):
    """ Used to test the connection """
    _name = 'magento.adapter.test'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.backend'
    _magento_model = ''
