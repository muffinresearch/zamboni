from optparse import make_option

from celery.task.sets import TaskSet
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

import amo
from amo.utils import chunked
from market.models import PriceCurrency
import mkt
from mkt.developers.tasks import region_exclude
from mkt.regions import (ALL_PAID_REGION_IDS,
                         REGIONS_CHOICES_SLUG)
from mkt.webapps.models import Webapp

REGIONS_SLUG_DICT = dict(REGIONS_CHOICES_SLUG[1:])
VALID_REGION_SLUGS = sorted(REGIONS_SLUG_DICT.keys())
VALID_TYPE_DICT = {
    'paid': 'All apps that have payments',
    'free': 'All free apps (not including free with in-app)',
    'all': 'Both free and paid apps'
}
VALID_TYPES = VALID_TYPE_DICT.keys()

valid_opts_help = ['The type of app looked up. Valid options are: %s']
for k, v in VALID_TYPE_DICT.items():
    valid_opts_help.append("'  * %s': %s'" % (k, v))

APP_TYPE_HELP = "\n".join(valid_opts_help)


def get_region_obj(region_slug):
    """Get region object from slug."""
    return REGIONS_SLUG_DICT.get(region_slug)


def get_free_app_ids():
    """Get apps that have opted out of worldwide other."""
    return (Webapp.objects.filter(premium_type=amo.ADDON_FREE)
            .filter(addonexcludedregion__region=mkt.regions.WORLDWIDE.id)
            .values_list('pk', flat=True))


def exclude_regions(ids, regions):
    """Do the actual work to exclude regions."""
    ts = [region_exclude.subtask(args=[chunk, regions])
          for chunk in chunked(ids, 100)]
    TaskSet(ts).apply_async()


def get_paid_app_ids(region_obj):
    """Find all the free apps not opted in AND paid apps where
    enable_new_regions is False OR
    enable_new_regions is True AND the region is not ok
    for the app's current price.

    """

    free_inapp_not_opted_in = Q(premium_type=amo.ADDON_FREE_INAPP,
                                enable_new_regions=False)
    premium_apps = Q(premium_type__in=amo.ADDON_PREMIUMS)
    opted_out = Q(enable_new_regions=False)

    # Get active price tiers that are good for this region.
    price_currencies_qs = PriceCurrency.objects.filter(
        region=region_obj.id, tier__active=True).values('tier')

    # If the price is in the list of prices not relevant for this
    # region then it needs to be excluded.
    bad_price = (Q(enable_new_regions=True) &
                 ~Q(addonpremium__price__in=price_currencies_qs))

    return (Webapp.objects.filter(free_inapp_not_opted_in |
           (premium_apps & (opted_out | bad_price))
    ).values_list('pk', flat=True))


class Command(BaseCommand):
    args = '<region_id>'
    option_list = BaseCommand.option_list + (
        make_option('--app-type', action='store',
                    default='paid',
                    type='string', dest='app_type',
                    help=APP_TYPE_HELP),
        make_option('--dry-run', action='store_true',
                    dest='dry_run',
                    help=("Don't process anything just print how many apps "
                          "would be affected"))
    )

    def write_output(self, value=''):
        self.stdout.write(value + '\n')

    def write_error(self, value=''):
        self.stderr.write(value + '\n')

    def _paid_app_ids(self):
        ids = []
        if self.region.id not in ALL_PAID_REGION_IDS:
            self.write_output('WARNING: region_id %s is not enabled for '
                              'payments. Ignoring paid apps.')
        else:
            ids = get_paid_app_ids(self.region)
        return ids

    def _free_app_ids(self):
        # Lookup all apps that don't have paynents and get their ids
        # where the worldwide region is excluded.
        return get_free_app_ids()

    def handle(self, *args, **options):
        regions = ', '.join(VALID_REGION_SLUGS)
        if not args or len(args) != 1:
            raise CommandError('You must enter a single region slug. '
                               'Available choices: %s' % regions)

        region_slug = args[0]
        if region_slug not in VALID_REGION_SLUGS:
            raise CommandError(('You must enter a valid region slug. '
                                'Available choices: %s' % regions))

        app_type = options.get('app_type', None)
        if app_type not in VALID_TYPES:
            raise CommandError(
                'app_type must be one of (%s)' % ', '.join(VALID_TYPES))

        self.app_type = app_type
        self.region = get_region_obj(region_slug)

        paid_ids = []
        free_ids = []
        if app_type == 'all':
            paid_ids = self._paid_app_ids()
            free_ids = self._free_app_ids()
        elif app_type == 'paid':
            paid_ids = self._paid_app_ids()
        elif app_type == 'free':
            free_ids = self._free_app_ids()

        self.write_output('Running command for app_type: %s' % app_type)
        if options.get('dry_run'):
            self.write_output('Number of apps that would be affected by this '
                              'operation')
            self.write_output('Paid Apps: %s' % len(paid_ids))
            self.write_output('Free Apps: %s' % len(free_ids))
            self.write_output('Run without --dry-run to update the apps.')
        else:
            # Actually update the apps.
            exclude_regions(paid_ids, (self.region,))
            exclude_regions(free_ids, (self.region,))
