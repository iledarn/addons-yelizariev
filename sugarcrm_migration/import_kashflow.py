from openerp.exceptions import except_orm
import MySQLdb
import MySQLdb.cursors
from import_base import import_base

from pandas import merge, DataFrame
from .mapper import *

import re

import time

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
import csv

class fix_kashflow_date(mapper):
    """
    convert '31/12/2012' to '2012-12-31'
    """
    def __init__(self, field_name):
        self.field_name = field_name

    def __call__(self, external_values):
        s = external_values.get(self.field_name)
        if not s:
            return ''
        d,m,y = str(s).split('/')
        return '%s-%s-%s' % (y,m,d)


class import_kashflow(import_base):

    TABLE_COMPANY = 'companies'

    TABLE_CUSTOMER = '-customers'
    TABLE_SUPPLIER = '-suppliers'
    TABLE_PARTNER = '_partners'
    TABLE_JOURNAL = '_journals'
    TABLE_NOMINAL_CODES = '-nominal-codes'
    TABLE_TRANSACTION = '-transactions'

    COL_ID_CUSTOM = 'id'
    COL_LINE_NUM = 'line_num'

    COL_NOMINAL_CODE = 'Nominal Code'
    COL_NOMINAL_CODE_NAME = 'Name'

    COL_TR_TYPE = 'Transaction Type'
    COL_TR_BANK = 'Bank'
    COL_TR_CODE = 'Code'
    COL_TR_DATE = 'Date'
    COL_TR_TRANSACTION = 'Account'
    COL_TR_COMMENT = 'Comment'
    COL_TR_AMOUNT = 'Amount'
    COL_TR_VAT_RATE = 'VAT Rate'
    COL_TR_VAT_AMOUNT = 'VAT Amount'
    COL_TR_DEPARTMENT = 'Department'

    COL_P_CODE = 'Code'
    COL_P_NAME = 'Name'
    COL_P_ADDRESS = 'Address'
    COL_P_LINE_2 = 'Line 2'
    COL_P_LINE_3 = 'Line 3'
    COL_P_LINE_4 = 'Line 4'
    COL_P_POST_CODE = 'Post Code'
    COL_P_FULL_NAME = 'Full Name'
    COL_P_TELEPHONE = 'Telephone'
    COL_P_MOBILE = 'Mobile'
    COL_P_SOURCE = 'Source'

    def initialize(self):
        # files:
        # COMPANY_NAME-customers.csv
        # COMPANY_NAME-suppliers.csv
        # COMPANY_NAME-nominal-codes.csv
        # COMPANY_NAME-transactions.csv

        self.csv_files = self.context.get('csv_files')
        self.import_options.update({'separator':',',
                                    #'quoting':''
                                    })
        companies = []
        for f in self.csv_files:
            if f.endswith('-transactions.csv'):
                c = re.match('.*?([^/]*)-transactions.csv$', f).group(1)
                companies.append(c)

        self.companies = [{'name':c} for c in companies]

    def get_data(self, table):
        file_name = filter(lambda f: f.endswith('/%s.csv' % table), self.csv_files)
        if file_name:
            file_name = file_name[0]
        else:
            return []

        with open(file_name, 'rb') as csvfile:
            fixed_file = StringIO(csvfile.read() .replace('\r\n', '\n'))
        reader = csv.DictReader(fixed_file,
                            delimiter = self.import_options.get('separator'),
                            #quotechar = self.import_options.get('quoting'),
                            )
        res = list(reader)
        for line_num, line in enumerate(res):
            line[self.COL_LINE_NUM] = str(line_num)
        return res

    def get_mapping(self):
        res = [self.get_mapping_company()]
        #for c in self.companies:
        for c in [self.companies[0]]:
            company = c.get('name')
            res.extend(
                self.get_mapping_partners(company) + 
                [
                self.get_mapping_journals(company),
                self.get_mapping_nominal_codes(company),
                self.get_mapping_transactions(company),
                ])
        return res

    def table_company(self):
        t = DataFrame(self.companies)
        return t

    def finalize_companies(self):
        #for c in self.companies:
        for c in [self.companies[0]]:
            context = self.get_context_company(c.get('name'))()
            company_id = context.get('company_id')
            for year in [2012,2013,2014]:
                existed = self.pool.get('account.fiscalyear').search(self.cr, self.uid, [('code','=',str(year)), ('company_id','=', company_id)])
                if existed:
                    continue
                year_id = self.pool.get('account.fiscalyear').create(self.cr, self.uid, {
                    'name':str(year),
                    'code':str(year),
                    'date_start': time.strftime('%s-04-01' % year),
                    'date_stop': time.strftime('%s-03-31' % (year+1)),
                    'company_id': company_id
                    })
                self.pool.get('account.fiscalyear').create_period3(self.cr, self.uid, [year_id])

    def get_mapping_company(self):
        return {
            'name': self.TABLE_COMPANY,
            'table': self.table_company,
            'dependencies' : [],
            'models':[
                {'model' : 'res.company',
                 'finalize': self.finalize_companies,
                 'fields': {
                     'id': xml_id(self.TABLE_COMPANY, 'name'),
                     'name': 'name',
                     }
                 }
                ]
            }

    def get_table(self, company, table):
        def f():
            t = DataFrame(self.get_data(company + table))
            return t
        return f

    def get_mapping_partners(self, company):
        table = company + self.TABLE_PARTNER
        def f(customer=False, supplier=False):
            return {
                'name': table,
                'table': self.get_table(company, self.TABLE_CUSTOMER if customer else self.TABLE_SUPPLIER),
                'dependencies' : [],
                'models':[
                    {'model' : 'res.partner',
                     'fields': {
                         'id': xml_id(table, self.COL_P_CODE),
                         'company_id/id': self.company_id(company),
                         'name': self.COL_P_NAME,
                         'ref': self.COL_P_CODE,
                         'customer': const('1') if customer else const('0'),
                         'supplier': const('1') if supplier else const('0'),
                         'phone': self.COL_P_TELEPHONE,
                         #'mobile': self.COL_P_MOBILE,
                         'zip': self.COL_P_POST_CODE,
                         'street': self.COL_P_ADDRESS,
                         'street2': concat(self.COL_P_LINE_2,self.COL_P_LINE_3,self.COL_P_LINE_4),
                         'comment': ppconcat(self.COL_P_SOURCE),
                         }
                     },
                    {'model' : 'res.partner',
                     'hook': self.get_hook_ignore_empty(self.COL_P_MOBILE, self.COL_P_FULL_NAME),
                     'fields': {
                         'id': xml_id(table+'_child', self.COL_P_CODE),
                         'company_id/id': self.company_id(company),
                         'parent_id/id': xml_id(table, self.COL_P_CODE),
                         'name': value(self.COL_P_FULL_NAME, default='NONAME'),
                         'customer': const('1') if customer else const('0'),
                         'supplier': const('1') if supplier else const('0'),
                         #'phone': self.COL_P_TELEPHONE,
                         'mobile': self.COL_P_MOBILE,
                         }
                     }
                    ]
                }

        return [f(customer=True), f(supplier=True)]


    def company_id(self, company):
        id = self.get_xml_id(self.TABLE_COMPANY, 'name', {'name':company})
        return const(id)

    def get_hook_account_account(self, company):
        def f(external_values):
            id = self.get_xml_id(company + self.TABLE_NOMINAL_CODES, self.COL_NOMINAL_CODE, external_values)
            res_id = self.pool.get('ir.model.data').xmlid_to_res_id(
                self.cr,
                self.uid,
                '.'+id
            )
            if res_id:
                # account already created
                return None

            return external_values
        return f

    def get_mapping_nominal_codes(self, company):
        table = company + self.TABLE_NOMINAL_CODES
        return {
            'name': table,
            'table': self.get_table(company, self.TABLE_NOMINAL_CODES),
            'dependencies' : [],
            'models':[{
                'model' : 'account.account',
                 'context': self.get_context_company(company),
                'hook': self.get_hook_account_account(company),
                'fields': {
                    'id': xml_id(table, self.COL_NOMINAL_CODE),
                    'company_id/id': self.company_id(company),
                    'code': self.COL_NOMINAL_CODE,
                    'name': self.COL_NOMINAL_CODE_NAME,
                    'user_type/id': const('account.data_account_type_view'),
                    }
                }]
            }

    def get_xml_id(self, table, col, external_values):
        id = xml_id(table, col)
        id.set_parent(self)
        return id(external_values)

    def get_hook_move_line(self, company, account):
        def f(external_values):
            external_values['debit'] = 0
            external_values['credit'] = 0
            external_values[self.COL_ID_CUSTOM] = external_values[self.COL_LINE_NUM]

            bank = external_values.get(self.COL_TR_BANK)

            partner_id = ''
            if bank and not bank.isdigit():
                partner_id = self.get_xml_id(company + self.TABLE_PARTNER, self.COL_TR_BANK, external_values)
            external_values['partner_id'] = partner_id

            debit = external_values.copy()
            credit = external_values.copy()
            debit[self.COL_ID_CUSTOM] += '_debit'
            credit[self.COL_ID_CUSTOM] += '_credit'

            amount = float(external_values.get(self.COL_TR_AMOUNT))

            debit['debit'] = amount
            credit['credit'] = amount

            account_from_code = self.get_xml_id(account, self.COL_TR_CODE, external_values)
            if not bank:
                bank = '1200'
            elif not bank.isdigit():
                bank = '1200'
            account_from_bank = self.get_xml_id(account, 'bank', {'bank':bank})

            journal_type = external_values.get(self.COL_TR_TYPE)
            if journal_type in ['BP', 'JD', 'PI', 'SI']:
                # expense
                debit['account_id'] = account_from_code
                credit['account_id'] = account_from_bank
            else:
                # income
                debit['account_id'] = account_from_bank
                credit['account_id'] = account_from_code

            return [debit, credit]
        return f

    map_journal_type = {
        'SI':'sale',# Sales Invoice
        'SC':'sale',# Sales Credit
        'PC':'purchase',# Purchase Credit
        'PI':'purchase',# Purchase Invoice
        'JC':'general',# Journal Credit
        'JD':'general',# Journal Debit
        'BP':'bank',# Bank Payment
        'BR':'bank',# Bank Receipt
        }

    def table_journal(self):
        res = []
        for code in self.map_journal_type:
            res.append({self.COL_TR_TYPE: code})
        t = DataFrame(res)
        return t

    def get_mapping_journals(self, company):
        journal = company + self.TABLE_JOURNAL
        return {
            'name': journal,
            'table': self.table_journal,
            'dependencies' : [],
            'models':[

                {'model' : 'account.journal',
                 'context': self.get_context_company(company),
                 'fields': {
                     'id': xml_id(journal, self.COL_TR_TYPE),
                     'company_id/id': self.company_id(company),
                     'name': self.COL_TR_TYPE,
                     'code': self.COL_TR_TYPE,
                     'type': map_val(self.COL_TR_TYPE, self.map_journal_type),
                     }
                    },
                ]
        }

    def get_context_company(self, company):
        def f():
            company_id = self.pool.get('ir.model.data').xmlid_to_res_id(
                self.cr,
                self.uid,
                '.'+self.company_id(company)({})
            )
            return {'company_id':company_id}
        return f

    def get_mapping_transactions(self, company):
        table = company + self.TABLE_TRANSACTION
        move = table + '_move'
        move_line = move + '_line'
        journal = company + self.TABLE_JOURNAL
        account = company + self.TABLE_NOMINAL_CODES
        return {
            'name': table,
            'table': self.get_table(company, self.TABLE_TRANSACTION),
            'dependencies' : [],
            'models':[
                # TODO COL_TR_DEPARTMENT
                # TODO VAT
                {'model' : 'account.move',
                 'context': self.get_context_company(company),
                 'fields': {
                     'id': xml_id(move, self.COL_LINE_NUM),
                     'company_id/id': self.company_id(company),
                     'ref': self.COL_TR_TRANSACTION,
                     'journal_id/id': xml_id(journal, self.COL_TR_TYPE),
                     'date': fix_kashflow_date(self.COL_TR_DATE),
                     'narration': self.COL_TR_COMMENT,
                     }
                    },
                {'model' : 'account.move.line',
                 'hook': self.get_hook_move_line(company, account),
                 'context': self.get_context_company(company),
                 'fields': {
                     'id': xml_id(move_line, self.COL_ID_CUSTOM),
                     'company_id/id': self.company_id(company),
                     'name': self.COL_TR_COMMENT,
                     'ref': self.COL_TR_TRANSACTION,
                     'date': fix_kashflow_date(self.COL_TR_DATE),
                     'move_id/id': xml_id(move, self.COL_LINE_NUM),
                     'account_id/id': 'account_id',
                     'partner_id/id': 'partner_id',
                     'debit':'debit',
                     'credit':'credit',
                     }
                 },
                ]
            }
