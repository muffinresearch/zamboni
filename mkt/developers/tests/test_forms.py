import json
import os
import shutil

from django.conf import settings
from django.core.files.storage import default_storage as storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import ValidationError

import mock
from nose.tools import eq_
from test_utils import RequestFactory

import amo
import amo.tests
from amo.tests import app_factory
from amo.tests.test_helpers import get_image_path
from addons.models import Addon, AddonCategory, Category, CategorySupervisor
from files.helpers import copyfileobj
from market.models import AddonPremium, Price
from users.models import UserProfile

import mkt
from mkt.developers import forms
from mkt.site.fixtures import fixture
from mkt.webapps.models import (AddonExcludedRegion as AER, ContentRating,
                                Webapp)


class TestPreviewForm(amo.tests.TestCase):
    fixtures = ['base/addon_3615']

    def setUp(self):
        self.addon = Addon.objects.get(pk=3615)
        self.dest = os.path.join(settings.TMP_PATH, 'preview')
        if not os.path.exists(self.dest):
            os.makedirs(self.dest)

    @mock.patch('amo.models.ModelBase.update')
    def test_preview_modified(self, update_mock):
        name = 'transparent.png'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        shutil.copyfile(get_image_path(name), os.path.join(self.dest, name))
        assert form.is_valid(), form.errors
        form.save(self.addon)
        assert update_mock.called

    def test_preview_size(self):
        name = 'non-animated.gif'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        with storage.open(os.path.join(self.dest, name), 'wb') as f:
            copyfileobj(open(get_image_path(name)), f)
        assert form.is_valid(), form.errors
        form.save(self.addon)
        eq_(self.addon.previews.all()[0].sizes,
            {u'image': [250, 297], u'thumbnail': [180, 214]})

    def check_file_type(self, type_):
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': type_,
                                  'position': 1})
        assert form.is_valid(), form.errors
        form.save(self.addon)
        return self.addon.previews.all()[0].filetype

    @mock.patch('lib.video.tasks.resize_video')
    def test_preview_good_file_type(self, resize_video):
        eq_(self.check_file_type('x.video-webm'), 'video/webm')

    def test_preview_other_file_type(self):
        eq_(self.check_file_type('x'), 'image/png')

    def test_preview_bad_file_type(self):
        eq_(self.check_file_type('x.foo'), 'image/png')


class TestCategoryForm(amo.tests.WebappTestCase):
    fixtures = fixture('user_999', 'webapp_337141')

    def setUp(self):
        super(TestCategoryForm, self).setUp()
        self.user = UserProfile.objects.get(username='regularuser')
        self.app = Webapp.objects.get(pk=337141)
        self.request = RequestFactory()
        self.request.user = self.user
        self.request.groups = ()

        self.cat = Category.objects.create(type=amo.ADDON_WEBAPP)
        self.op_cat = Category.objects.create(
            type=amo.ADDON_WEBAPP, region=1, carrier=2)

    def _make_form(self, data=None):
        self.form = forms.CategoryForm(
            data, product=self.app, request=self.request)

    def _cat_count(self):
        return self.form.fields['categories'].queryset.count()

    def test_has_no_cats(self):
        self._make_form()
        eq_(self._cat_count(), 1)
        eq_(self.form.max_categories(), 2)

    def test_has_users_cats(self):
        CategorySupervisor.objects.create(
            user=self.user, category=self.op_cat)
        self._make_form()
        eq_(self._cat_count(), 2)
        eq_(self.form.max_categories(), 3)  # Special cats increase the max.

    def test_save_cats(self):
        self.op_cat.delete()  # We don't need that one.

        # Create more special categories than we could otherwise save.
        for i in xrange(10):
            CategorySupervisor.objects.create(
                user=self.user,
                category=Category.objects.create(
                    type=11, region=1, carrier=2))

        self._make_form({'categories':
            map(str, Category.objects.filter(type=11)
                                     .values_list('id', flat=True))})
        assert self.form.is_valid(), self.form.errors
        self.form.save()
        eq_(AddonCategory.objects.filter(addon=self.app).count(),
            Category.objects.count())
        eq_(self.form.max_categories(), 12)  # 2 (default) + 10 (above)

    def test_unavailable_special_cats(self):
        AER.objects.create(addon=self.app, region=1)

        self._make_form()
        eq_(self._cat_count(), 1)
        eq_(self.form.max_categories(), 2)


class TestRegionForm(amo.tests.WebappTestCase):
    fixtures = fixture('webapp_337141', 'prices')

    def setUp(self):
        super(TestRegionForm, self).setUp()
        self.request = RequestFactory()
        self.kwargs = {'product': self.app}

    def test_initial_empty(self):
        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(form.initial['regions'], mkt.regions.REGION_IDS)
        eq_(form.initial['other_regions'], True)

    def test_initial_excluded_in_region(self):
        AER.objects.create(addon=self.app, region=mkt.regions.BR.id)

        regions = list(mkt.regions.REGION_IDS)
        regions.remove(mkt.regions.BR.id)

        eq_(self.get_app().get_region_ids(), regions)

        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(form.initial['regions'], regions)
        eq_(form.initial['other_regions'], True)

    def test_initial_excluded_in_regions_and_future_regions(self):
        for region in [mkt.regions.BR, mkt.regions.UK, mkt.regions.WORLDWIDE]:
            AER.objects.create(addon=self.app, region=region.id)

        regions = list(mkt.regions.REGION_IDS)
        regions.remove(mkt.regions.BR.id)
        regions.remove(mkt.regions.UK.id)

        eq_(self.get_app().get_region_ids(), regions)

        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(form.initial['regions'], regions)
        eq_(form.initial['other_regions'], False)

    @mock.patch('mkt.regions.BR.has_payments', new=True)
    def test_disable_regions_on_paid(self):
        eq_(self.app.get_region_ids(), mkt.regions.REGION_IDS)
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        price = Price.objects.get(id=1)
        AddonPremium.objects.create(addon=self.app,
                                    price=price)
        self.kwargs['price'] = price
        form = forms.RegionForm(data=None, **self.kwargs)
        assert not form.is_valid()
        assert form.has_inappropriate_regions()

        form = forms.RegionForm(
            data={'regions': mkt.regions.ALL_PAID_REGION_IDS}, **self.kwargs)
        assert not form.is_valid()
        assert form.has_inappropriate_regions()

        form = forms.RegionForm(data={'regions': [mkt.regions.PL.id]},
                                **self.kwargs)
        assert form.is_valid(), form.errors
        assert not form.has_inappropriate_regions()
        form.save()

        self.assertSetEqual(self.app.get_region_ids(),
                            [mkt.regions.PL.id])

    mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
               new=set([1,2,3,4,5]))
    def test_inappropriate_regions(self):
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        form = forms.RegionForm(data=None, **self.kwargs)
        form.price_region_ids = set([2, 3, 5])
        form.disabled_regions = set([5])
        form.allow_worldwide_paid = True

        # 5 is in disabled_regions so should be True.
        form.region_ids = [5]
        assert form.has_inappropriate_regions()

        # 1 worldwide and worldwide is allowed so should be False.
        form.region_ids = [1]
        assert not form.has_inappropriate_regions()

        # 1 worldwide and worldwide is not allowed so should be True.
        form.allow_worldwide_paid = False
        form.region_ids = [1]
        assert form.has_inappropriate_regions()

        # 4 is not in price_region_ids so should be True.
        form.region_ids = [4]
        assert form.has_inappropriate_regions()

        # 2 is in price_region_ids so should be False.
        form.region_ids = [2]
        assert not form.has_inappropriate_regions()

    def test_inappropriate_regions_free_app(self):
        self.app.update(premium_type=amo.ADDON_FREE)
        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(form.has_inappropriate_regions(), None)

    def test_free_inapp_with_non_paid_region(self):
        # Start with a free app with in_app payments.
        self.app.update(premium_type=amo.ADDON_FREE_INAPP)
        self.kwargs['price'] = 'free'
        form = forms.RegionForm(data=None, **self.kwargs)
        assert not form.is_valid()
        assert form.has_inappropriate_regions()

        all_paid_regions = set(mkt.regions.ALL_PAID_REGION_IDS)

        new_paid_set = all_paid_regions.difference(set([mkt.regions.BR.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.BR.id]},
                                    **self.kwargs)
            assert not form.is_valid()
            assert form.has_inappropriate_regions()

        new_paid_set = all_paid_regions.difference(set([mkt.regions.UK.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.UK.id]},
                                    **self.kwargs)
            assert not form.is_valid()
            assert form.has_inappropriate_regions()

        new_paid_set = all_paid_regions.union(set([mkt.regions.PL.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.PL.id]},
                                    **self.kwargs)
            assert form.is_valid()
            assert not form.has_inappropriate_regions()

    def test_premium_to_free_inapp_with_non_paid_region(self):
        # At this point the app is premium.
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        self.kwargs['price'] = 'free'
        form = forms.RegionForm(data=None, **self.kwargs)
        assert not form.is_valid()
        assert form.has_inappropriate_regions()

        all_paid_regions = set(mkt.regions.ALL_PAID_REGION_IDS)
        new_paid_set = all_paid_regions.difference(set([mkt.regions.BR.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.BR.id]},
                                    **self.kwargs)
            assert not form.is_valid()
            assert form.has_inappropriate_regions()

        new_paid_set = all_paid_regions.difference(set([mkt.regions.UK.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.UK.id]},
                                    **self.kwargs)
            assert not form.is_valid()
            assert form.has_inappropriate_regions()

        new_paid_set = all_paid_regions.union(set([mkt.regions.PL.id]))
        with mock.patch('mkt.developers.forms.ALL_PAID_REGION_IDS',
                        new=new_paid_set):
            form = forms.RegionForm(data={'regions': [mkt.regions.PL.id]},
                                    **self.kwargs)
            assert form.is_valid()
            assert not form.has_inappropriate_regions()

    def test_paid_enable_region(self):
        for region in mkt.regions.ALL_REGION_IDS:
            AER.objects.create(addon=self.app, region=region)
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        price = Price.objects.get(id=1)
        AddonPremium.objects.create(addon=self.app,
                                    price=price)
        self.kwargs['price'] = price
        form = forms.RegionForm(data={'regions': []}, **self.kwargs)
        assert not form.is_valid()  # Fails due to needing at least 1 region
        assert not form.has_inappropriate_regions(), form.has_inappropriate_regions()

        form = forms.RegionForm(data={'regions': [mkt.regions.PL.id]},
                                **self.kwargs)
        assert form.is_valid(), form.errors
        assert not form.has_inappropriate_regions()

        form = forms.RegionForm(data={'regions': [mkt.regions.BR.id]},
                                **self.kwargs)
        assert not form.is_valid()
        assert form.has_inappropriate_regions()

    def test_worldwide_only(self):
        form = forms.RegionForm(data={'other_regions': 'on'}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.app.get_region_ids(True), [mkt.regions.WORLDWIDE.id])

    def test_worldwide_paid(self):
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        self.kwargs['price'] = Price.objects.get(id=1)
        form = forms.RegionForm(data={'other_regions': 'on'}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.app.get_region_ids(True), [mkt.regions.WORLDWIDE.id])

    def test_worldwide_not_allowed_flag(self):
        self.app.update(premium_type=amo.ADDON_PREMIUM)
        self.kwargs['price'] = Price.objects.get(id=1)
        form = forms.RegionForm(data={'other_regions': 'on'}, **self.kwargs)
        form.allow_worldwide_paid = False
        assert not form.is_valid()
        form = forms.RegionForm(data={'other_regions': 'on'}, **self.kwargs)
        form.allow_worldwide_paid = True
        assert form.is_valid()

    def test_no_regions(self):
        form = forms.RegionForm(data={}, **self.kwargs)
        assert not form.is_valid()
        eq_(form.errors,
            {'__all__': ['You must select at least one region or '
                         '"Other and new regions."']})

    def test_exclude_each_region(self):
        """Test that it's possible to exclude each region."""

        for region_id in mkt.regions.REGION_IDS:
            if region_id == mkt.regions.WORLDWIDE.id:
                continue

            to_exclude = list(mkt.regions.REGION_IDS)
            to_exclude.remove(region_id)

            form = forms.RegionForm(
                data={'regions': to_exclude,
                      'other_regions': True}, **self.kwargs)
            assert form.is_valid(), form.errors
            form.save()

            eq_(self.app.get_region_ids(False), to_exclude)

    def test_brazil_games_excluded(self):
        games = Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')
        AddonCategory.objects.create(addon=self.app, category=games)

        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': True}, **self.kwargs)

        # Developers should still be able to save form OK, even
        # if they pass a bad region. Think of the grandfathered developers.
        assert form.is_valid(), form.errors
        form.save()

        # No matter what the developer tells us, still exclude Brazilian
        # games.
        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(set(form.initial['regions']),
            set(mkt.regions.REGION_IDS) -
            set([mkt.regions.BR.id, mkt.regions.WORLDWIDE.id]))
        eq_(form.initial['other_regions'], True)

    def test_brazil_games_already_excluded(self):
        AER.objects.create(addon=self.app, region=mkt.regions.BR.id)

        games = Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')
        AddonCategory.objects.create(addon=self.app, category=games)

        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': True}, **self.kwargs)

        assert form.is_valid()
        form.save()

        form = forms.RegionForm(data=None, **self.kwargs)
        eq_(set(form.initial['regions']),
            set(mkt.regions.REGION_IDS) -
            set([mkt.regions.BR.id, mkt.regions.WORLDWIDE.id]))
        eq_(form.initial['other_regions'], True)

    def test_brazil_games_with_content_rating(self):
        # This game has a government content rating!
        rb = mkt.regions.BR.ratingsbodies[0]
        ContentRating.objects.create(
            addon=self.app, ratings_body=rb.id, rating=rb.ratings[0].id)

        games = Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')
        AddonCategory.objects.create(addon=self.app, category=games)

        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': 'on'}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()

        eq_(self.app.get_region_ids(True), mkt.regions.ALL_REGION_IDS)

    def test_exclude_worldwide(self):
        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': False}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.app.get_region_ids(True), mkt.regions.REGION_IDS)

    def test_reinclude_region(self):
        AER.objects.create(addon=self.app, region=mkt.regions.BR.id)

        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': True}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.app.get_region_ids(True), mkt.regions.ALL_REGION_IDS)

    def test_reinclude_worldwide(self):
        AER.objects.create(addon=self.app, region=mkt.regions.WORLDWIDE.id)

        form = forms.RegionForm(data={'regions': mkt.regions.REGION_IDS,
                                      'other_regions': True}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.app.get_region_ids(True), mkt.regions.ALL_REGION_IDS)


class TestNewManifestForm(amo.tests.TestCase):

    @mock.patch('mkt.developers.forms.verify_app_domain')
    def test_normal_validator(self, _verify_app_domain):
        form = forms.NewManifestForm({'manifest': 'http://omg.org/yes.webapp'},
            is_standalone=False)
        assert form.is_valid()
        assert _verify_app_domain.called

    @mock.patch('mkt.developers.forms.verify_app_domain')
    def test_standalone_validator(self, _verify_app_domain):
        form = forms.NewManifestForm({'manifest': 'http://omg.org/yes.webapp'},
            is_standalone=True)
        assert form.is_valid()
        assert not _verify_app_domain.called


class TestValidateOrigin(amo.tests.TestCase):

    def test_invalid_origins(self):
        origins = [
            'this-is-not-an-origin',
            'ftp://domain.com',
            'mail:someone@somewhere.com',
            '//domain.com',
            'http://domain.com',
            'https://domain.com',
        ]
        for origin in origins:
            with self.assertRaises(ValidationError):
                forms.validate_origin(origin)

    def test_valid_origins(self):
        origins = [
            'app://domain.com',
            'app://domain.com/with/path.exe?q=yo',
            # TODO: Should that be valid? ^
        ]
        for origin in origins:
            origin = forms.validate_origin(origin)
            assert origin, 'Origin invalid: %s' % origin


class TestPackagedAppForm(amo.tests.AMOPaths, amo.tests.WebappTestCase):

    def setUp(self):
        path = self.packaged_app_path('mozball.zip')
        self.files = {'upload': SimpleUploadedFile('mozball.zip',
                                                   open(path).read())}

    def test_not_there(self):
        form = forms.NewPackagedAppForm({}, {})
        assert not form.is_valid()
        eq_(form.errors['upload'], [u'This field is required.'])
        eq_(form.file_upload, None)

    def test_right_size(self):
        form = forms.NewPackagedAppForm({}, self.files)
        assert form.is_valid(), form.errors
        assert form.file_upload

    def test_too_big(self):
        form = forms.NewPackagedAppForm({}, self.files, max_size=5)
        assert not form.is_valid()
        validation = json.loads(form.file_upload.validation)
        assert 'messages' in validation, 'No messages in validation.'
        eq_(validation['messages'][0]['message'],
            [u'Packaged app too large for submission.',
             u'Packages must be less than 5 bytes.'])


class TestTransactionFilterForm(amo.tests.TestCase):

    def setUp(self):
        (app_factory(), app_factory())
        # Need queryset to initialize form.
        self.apps = Webapp.objects.all()
        self.data = {
            'app': self.apps[0].id,
            'transaction_type': 1,
            'transaction_id': 1,
            'date_from_day': '1',
            'date_from_month': '1',
            'date_from_year': '2012',
            'date_to_day': '1',
            'date_to_month': '1',
            'date_to_year': '2013',
        }

    def test_basic(self):
        """Test the form doesn't crap out."""
        form = forms.TransactionFilterForm(self.data, apps=self.apps)
        assert form.is_valid(), form.errors

    def test_app_choices(self):
        """Test app choices."""
        form = forms.TransactionFilterForm(self.data, apps=self.apps)
        for app in self.apps:
            assertion = (app.id, app.name) in form.fields['app'].choices
            assert assertion, '(%s, %s) not in choices' % (app.id, app.name)


class TestAppFormBasic(amo.tests.TestCase):

    def setUp(self):
        self.data = {
            'slug': 'yolo',
            'manifest_url': 'https://omg.org/yes.webapp',
            'description': 'You Only Live Once'
        }
        self.request = mock.Mock()
        self.request.groups = ()

    def post(self):
        self.form = forms.AppFormBasic(
            self.data, instance=Webapp.objects.create(app_slug='yolo'),
            request=self.request)

    def test_success(self):
        self.post()
        eq_(self.form.is_valid(), True, self.form.errors)
        eq_(self.form.errors, {})

    def test_slug_invalid(self):
        Webapp.objects.create(app_slug='yolo')
        self.post()
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors,
            {'slug': ['This slug is already in use. Please choose another.']})
