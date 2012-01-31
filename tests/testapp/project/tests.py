# -*- coding: utf-8 -*-
import time
import httplib
import urllib
import urlparse
from decimal import Decimal
from django.contrib.sites.models import Site
from django.test import LiveServerTestCase
from django.test.client import Client, RequestFactory
#from django.http import HttpResponseRedirect
from django.conf import settings
from django.core.urlresolvers import reverse, resolve
from django.contrib.auth.models import User
#from django.core.context_processors import csrf
#from shop.models import Cart
#from shop.views.checkout import CheckoutSelectionView
from shop.util.cart import get_or_create_cart
from shop.addressmodel.models import Country
from shop.models.ordermodel import Order
from shop.backends_pool import backends_pool
from shop.tests.util import Mock
from ipayment.models import Confirmation
from models import DiaryProduct


class IPaymentTest(LiveServerTestCase):
    def setUp(self):
        current_site = Site.objects.get(id=settings.SITE_ID)
        current_site.domain = settings.HOST_NAME
        current_site.save()
        self.ipayment_backend = backends_pool.get_payment_backends_list()[0]
        self.factory = RequestFactory()
        self.request = Mock()
        setattr(self.request, 'session', {})
        setattr(self.request, 'is_secure', lambda: False)
        user = User.objects.create(username="test", email="test@example.com",
            first_name="Test", last_name="Tester", 
            password="sha1$fc341$59561b971056b176e8ebf0b456d5eac47b49472b")
        setattr(self.request, 'user', user)
        self.country_usa = Country(name='USA')
        self.country_usa.save()
        self.client = Client()
        self.client.login(username='test', password='123')
        self._create_cart()
        self._go_shopping()

    def _create_cart(self):
        self.product = DiaryProduct(isbn='1234567890', number_of_pages=100)
        self.product.name = 'test'
        self.product.slug = 'test'
        self.product.short_description = 'test'
        self.product.long_description = 'test'
        self.product.unit_price = Decimal('1.0')
        self.product.save()
        self.cart = get_or_create_cart(self.request)
        self.cart.add_product(self.product, 1)
        self.cart.save()

    def _go_shopping(self):

        # add address information
        post = {
            'ship-name': 'John Doe',
            'ship-address': 'Rosestreet',
            'ship-address2': '',
            'ship-zip_code': '01234',
            'ship-city': 'Toledeo',
            'ship-state': 'Ohio',
            'ship-country': self.country_usa.pk,
            'bill-name': 'John Doe',
            'bill-address': 'Rosestreet',
            'bill-address2': '',
            'bill-zip_code': '01234',
            'bill-city': 'Toledeo',
            'bill-state': 'Ohio',
            'bill-country': self.country_usa.pk,
            'shipping_method': 'flat',
            'payment_method': 'ipayment',
        }
        response = self.client.post(reverse('checkout_selection'), post, follow=True)
        urlobj = urlparse.urlparse(response.redirect_chain[0][0])
        self.assertEqual(resolve(urlobj.path).url_name, 'checkout_shipping')
        urlobj = urlparse.urlparse(response.redirect_chain[1][0])
        self.assertEqual(resolve(urlobj.path).url_name, 'flat')
        #request = self.factory.get(reverse('ipayment'))
        self.order = self.ipayment_backend.shop.get_order(self.request)

    def test_one(self):
        """
        
        """
        processorUrls = self.ipayment_backend.getProcessorURLs(self.request)
        post = {
            'silent': 1,
            'shopper_id': self.ipayment_backend.shop.get_order_unique_id(self.order),
            'advanced_strict_id_check': 0, # disabled for testing 
            'invoice_text': 'invoice text',
            'error_lang': 'en',
            'silent': 1,
            'trxuser_id': settings.IPAYMENT['trxUserId'],
            'trxpassword': settings.IPAYMENT['trxPassword'],
            'trx_amount': int(self.ipayment_backend.shop.get_order_total(self.order)*100),
            'trx_currency': 'EUR',
            'trx_paymenttyp': 'cc',
            'redirect_url': processorUrls['redirectUrl'],
            'silent_error_url': processorUrls['silentErrorUrl'],
            'hidden_trigger_url': processorUrls['hiddenTriggerUrl'],
            'addr_name': 'John Doe',
            'cc_number': '4012888888881881',
            'cc_checkcode': '123',
            'cc_expdate_month': '12',
            'cc_expdate_year': '2029',
        }
        post['trx_securityhash'] = self.ipayment_backend.calcTrxSecurityHash(post)
        ipayment_uri = '/merchant/%s/processor/2.0/' % settings.IPAYMENT['accountId']
        headers = {"Content-type": "application/x-www-form-urlencoded",
                   "Accept": "text/plain"}
        conn = httplib.HTTPSConnection('ipayment.de')
        conn.request("POST", ipayment_uri, urllib.urlencode(post), headers)
        httpresp = conn.getresponse()
        self.assertEqual(httpresp.status, 302, 'Expected to be redirected back from IPayment') 
        redir_url = urlparse.urlparse(httpresp.getheader('location'))
        query_params = urlparse.parse_qs(redir_url.query)
        redir_uri = redir_url.path+'?'+redir_url.query
        conn.close()
        self.assertEqual(query_params['ret_status'][0], 'SUCCESS', 'IPayment reported: '+redir_uri)
        
        # IPayent redirected the customer onto 'redir_uri'. Continue to complete the order.
        response = self.client.get(redir_uri, follow=True)
        self.assertEqual(len(response.redirect_chain), 1, '')
        urlobj = urlparse.urlparse(response.redirect_chain[0][0])
        self.assertEqual(resolve(urlobj.path).url_name, 'thank_you_for_your_order')
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(pk=self.order.id)
        self.assertEqual(order.status, Order.COMPLETED)
        confirmation = Confirmation.objects.get(pk=self.order.id)
        self.assertEqual(confirmation.ret_status, 'SUCCESS')
        time.sleep(10) # this keeps the server running 