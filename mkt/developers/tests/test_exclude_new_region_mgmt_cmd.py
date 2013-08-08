from StringIO import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from nose.tools import eq_, ok_, raises
from mock import patch

import amo
import amo.tests
import mkt
from market.models import AddonPremium, Price
from mkt.developers.management.commands import (
    exclude_new_region)
from mkt.site.fixtures import fixture
from mkt.webapps.models import AddonExcludedRegion as AER, Webapp


from mkt.developers.management.commands.exclude_new_region import (
    get_paid_app_ids, get_free_app_ids, get_region_obj)

from mkt.regions import PL, VE, US, WORLDWIDE


class TestExcludeNewRegionCommand(amo.tests.TestCase):
    fixtures = fixture('prices')

    def setUp(self):
        # Free app no exclusions.
        self.f1 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app free f1',
                                        type=amo.ADDON_WEBAPP)

        # Free app with an exclusion for worldwide.
        self.f2 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app free f2',
                                        type=amo.ADDON_WEBAPP)
        self.f2.addonexcludedregion.create(region=WORLDWIDE.id)

        # Free app with an exclusion for worldwide.
        self.f3 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app free f3',
                                        type=amo.ADDON_WEBAPP)
        self.f3.addonexcludedregion.create(region=WORLDWIDE.id)

        # Premium app opted in to new regions.
        self.p1 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app premium p1',
                                        type=amo.ADDON_WEBAPP,
                                        premium_type=amo.ADDON_PREMIUM,
                                        enable_new_regions=True)
        # Has price valid for US,PL,DE
        price1 = Price.objects.get(pk=1)
        AddonPremium.objects.create(addon=self.p1, price=price1)

        # Premium app opted in to new regions.
        self.p2 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app premium p2',
                                        type=amo.ADDON_WEBAPP,
                                        premium_type=amo.ADDON_PREMIUM,
                                        enable_new_regions=True)
        # Price is inactive. But is ok for US,BR
        price2 = Price.objects.get(pk=2)
        AddonPremium.objects.create(addon=self.p2, price=price2)

        # Premium app Opted out of new regions by default.
        self.p3 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                        name='Public app premium p3',
                                        type=amo.ADDON_WEBAPP,
                                        premium_type=amo.ADDON_PREMIUM)

        # Premium in-app opted in to new regions
        self.pia1 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                          name='Public app premium pia1',
                                          type=amo.ADDON_WEBAPP,
                                          premium_type=amo.ADDON_PREMIUM_INAPP,
                                          enable_new_regions=True)
        # Price is valid for VE
        price3 = Price.objects.get(pk=3)
        AddonPremium.objects.create(addon=self.pia1, price=price3)

        # Premium in-app opted out of new regions.
        self.pia2 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                          name=('Public app premium pia2'),
                                          type=amo.ADDON_WEBAPP,
                                          premium_type=amo.ADDON_PREMIUM_INAPP)

        # Free in-app opted out of new regions by default.
        self.fia1 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                          name='Public app free in_app fia1',
                                          type=amo.ADDON_WEBAPP,
                                          premium_type=amo.ADDON_FREE_INAPP)

        # Free in-app opted into new regions
        self.fia2 = Webapp.objects.create(status=amo.STATUS_PUBLIC,
                                          name='Public app free in_app fia2',
                                          type=amo.ADDON_WEBAPP,
                                          premium_type=amo.ADDON_FREE_INAPP,
                                          enable_new_regions=True)

    @raises(CommandError)
    def test_unknown_region_slug(self):
        exclude_new_region.Command().handle('whatever')

    @patch('mkt.regions.ALL_PAID_REGIONS', new=[mkt.regions.US.id])
    @patch('sys.stdout', new_callable=StringIO)
    @raises(CommandError)
    def test_update_paid_not_a_paid_region(self, mock_stdout):
        exclude_new_region.Command().handle(PL.slug)
        ok_('WARNING: region_id %s is not enabled for payments'
            'ignoring paid apps.' % PL.id in mock_stdout)

    @raises(CommandError)
    def test_no_args(self):
        exclude_new_region.Command().handle()

    def test_get_ids_free(self):
        ids = get_free_app_ids()
        self.assertSetEqual(ids, [
            self.f2.pk,  # Free app with the worldwide exclusion
            self.f3.pk,  # Free app with the worldwide exclusion
        ])

    def test_get_ids_paid_pl(self):
        region = get_region_obj(PL.slug)
        ids = get_paid_app_ids(region)
        self.assertSetEqual(ids, [
            self.p2.pk,  # Opted in but price inactive and only support BR,US.
            self.p3.pk,  # Opted out
            self.pia1.pk,  # Opted-in but price only supports VE
            self.pia2.pk,  # Opted out
            self.fia1.pk  # Opted out
        ])

    def test_get_ids_paid_ve(self):
        region = get_region_obj(VE.slug)
        ids = get_paid_app_ids(region)
        self.assertSetEqual(sorted(ids), [
            self.p1.pk,  # Opted in but price only supports PL,US,DE
            self.p2.pk,  # Opted in but price inactive and only supports BR,US.
            self.p3.pk,  # Opted out.
            self.pia2.pk,  # Opted out.
            self.fia1.pk   # Opted out.
        ])

    def test_get_ids_paid_us(self):
        region = get_region_obj(US.slug)
        ids = get_paid_app_ids(region)
        self.assertSetEqual(sorted(ids), [
            self.p2.pk,  # Opted in, but price inactive and only support BR,US.
            self.p3.pk,  # Opted out.
            self.pia1.pk,  # Opted-in but price only supports VE
            self.pia2.pk,  # Opted out.
            self.fia1.pk   # Opted out.
        ])

    @patch('sys.stdout', new_callable=StringIO)
    def test_dry_run(self, mock_stdout):
        call_command('exclude_new_region', PL.slug, dry_run=True)
        stdout_val = mock_stdout.getvalue()
        ok_('Running command for app_type: paid' in stdout_val)
        ok_('Paid Apps: 5' in stdout_val)
        ok_('Free Apps: 0' in stdout_val)

    @patch('sys.stdout', new_callable=StringIO)
    def test_dry_run_free(self, mock_stdout):
        call_command('exclude_new_region', PL.slug, dry_run=True,
                     app_type='free')
        stdout_val = mock_stdout.getvalue()
        ok_('Running command for app_type: free' in stdout_val)
        ok_('Paid Apps: 0' in stdout_val)
        ok_('Free Apps: 2' in stdout_val)

    @patch('sys.stdout', new_callable=StringIO)
    def test_dry_run_paid(self, mock_stdout):
        call_command('exclude_new_region', PL.slug, dry_run=True,
                     app_type='paid')
        stdout_val = mock_stdout.getvalue()
        ok_('Running command for app_type: paid' in stdout_val)
        ok_('Paid Apps: 5' in stdout_val)
        ok_('Free Apps: 0' in stdout_val)

    @patch('sys.stdout', new_callable=StringIO)
    def test_dry_run_all(self, mock_stdout):
        call_command('exclude_new_region', PL.slug, dry_run=True,
                     app_type='all')

        stdout_val = mock_stdout.getvalue()
        ok_('Running command for app_type: all' in stdout_val)
        ok_('Paid Apps: 5' in stdout_val)
        ok_('Free Apps: 2' in stdout_val)

    @patch('sys.stdout', new_callable=StringIO)
    def test_paid(self, mock_stdout):
        for app in (self.p2, self.p3, self.pia1,
                    self.pia2, self.fia1):
            eq_(AER.objects.filter(addon=app, region=PL.id).exists(), False)

        call_command('exclude_new_region', PL.slug,
                     app_type='paid')

        for app in (self.p2, self.p3, self.pia1,
                    self.pia2, self.fia1):
            eq_(AER.objects.filter(addon=app, region=PL.id).exists(), True)

    @patch('sys.stdout', new_callable=StringIO)
    def test_free(self, mock_stdout):
        for free_app in (self.f2, self.f3):
            eq_(AER.objects.filter(
                addon=free_app, region=PL.id).exists(), False)

        call_command('exclude_new_region', PL.slug,
                     app_type='free')

        for free_app in (self.f2, self.f3):
            eq_(AER.objects.filter(
                addon=free_app, region=PL.id).exists(), True)

    @patch('sys.stdout', new_callable=StringIO)
    def test_all(self, mock_stdout):
        call_command('exclude_new_region', PL.slug,
                     app_type='all')

        for app in (self.p2, self.p3, self.pia1,
                    self.pia2, self.fia1):
            eq_(AER.objects.filter(addon=app, region=PL.id).exists(), True)

        for free_app in (self.f2, self.f3):
            eq_(AER.objects.filter(
                addon=free_app, region=PL.id).exists(), True)
