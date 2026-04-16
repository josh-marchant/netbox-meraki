from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from netbox_meraki.models import ReviewItem, SyncLog, SyncReview
from netbox_meraki.templatetags.meraki_extras import json_pretty
from netbox_meraki.views import ReviewBulkClearView, ReviewClearView, ReviewDetailView, ReviewListView, _review_sections


class ReviewSectionsTests(SimpleTestCase):
    def test_groups_items_by_expected_section_order(self):
        items = [
            SimpleNamespace(item_type="vlan", status="pending"),
            SimpleNamespace(item_type="site", status="approved"),
            SimpleNamespace(item_type="device", status="failed"),
            SimpleNamespace(item_type="site", status="pending"),
        ]

        class QuerySetStub:
            def __init__(self, values):
                self.values = values

            def order_by(self, *_args):
                return self.values

        sections = _review_sections(QuerySetStub(items))

        self.assertEqual([section["item_type"] for section in sections], ["site", "device", "vlan"])
        self.assertEqual(sections[0]["pending_count"], 1)
        self.assertEqual(sections[0]["approved_count"], 1)
        self.assertEqual(sections[1]["failed_count"], 1)


class JsonPrettyFilterTests(SimpleTestCase):
    def test_json_pretty_formats_dict_for_display(self):
        rendered = json_pretty({"name": "Branch", "vid": 20})

        self.assertIn('"name": "Branch"', rendered)
        self.assertIn('"vid": 20', rendered)


class ReviewClearViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request_with_messages(self, method, path, data=None, permissions=None):
        request = getattr(self.factory, method.lower())(path, data=data or {})
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))

        class UserStub:
            username = "tester"
            is_authenticated = True

            def __init__(self, granted_permissions):
                self.granted_permissions = set(granted_permissions or [])

            def has_perm(self, permission):
                return permission in self.granted_permissions

            def __str__(self):
                return self.username

        request.user = UserStub(permissions or set())
        return request

    def _create_review(self, *, log_status="pending_review", message="Awaiting review", object_name="Branch VLAN"):
        sync_log = SyncLog.objects.create(status=log_status, message=message, sync_mode="review")
        review = SyncReview.objects.create(sync_log=sync_log, status="pending", items_total=1)
        item = ReviewItem.objects.create(
            review=review,
            item_type="vlan",
            action_type="update",
            object_name=object_name,
            object_identifier=f"nb:{object_name}",
            current_data={"name": "Old"},
            proposed_data={"name": "New"},
        )
        return review, item, sync_log

    def test_review_list_renders_bulk_clear_controls_for_authorized_user(self):
        review, _item, _sync_log = self._create_review()

        response = ReviewListView.as_view()(
            self._request_with_messages(
                "GET",
                "/reviews/",
                permissions={"netbox_meraki.view_syncreview", "netbox_meraki.review_sync"},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Clear Selected Reviews", content)
        self.assertIn(f"Clear review #{review.pk}?", content)

    def test_review_detail_renders_clear_action_for_authorized_user(self):
        review, _item, _sync_log = self._create_review()

        response = ReviewDetailView.as_view()(
            self._request_with_messages(
                "GET",
                f"/review/{review.pk}/",
                permissions={"netbox_meraki.view_syncreview", "netbox_meraki.review_sync"},
            ),
            pk=review.pk,
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Clear Review", content)
        self.assertIn("keeps the underlying sync log", content)

    def test_clear_single_review_deletes_review_and_keeps_sync_log(self):
        review, item, sync_log = self._create_review()

        with patch("netbox_meraki.views.redirect", return_value=HttpResponse(status=302)):
            response = ReviewClearView.as_view()(
                self._request_with_messages(
                    "POST",
                    f"/review/{review.pk}/clear/",
                    permissions={"netbox_meraki.review_sync"},
                ),
                pk=review.pk,
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SyncReview.objects.filter(pk=review.pk).exists())
        self.assertFalse(ReviewItem.objects.filter(pk=item.pk).exists())
        sync_log.refresh_from_db()
        self.assertEqual(sync_log.status, "success")
        self.assertIn("Review", sync_log.message)
        self.assertIn("cleared", sync_log.message)

    def test_bulk_clear_deletes_only_selected_reviews(self):
        review_one, item_one, sync_log_one = self._create_review(object_name="Selected")
        review_two, item_two, sync_log_two = self._create_review(object_name="Kept")

        with patch("netbox_meraki.views.redirect", return_value=HttpResponse(status=302)):
            response = ReviewBulkClearView.as_view()(
                self._request_with_messages(
                    "POST",
                    "/reviews/clear/",
                    data={"review_ids": [str(review_one.pk), "not-a-number"]},
                    permissions={"netbox_meraki.review_sync"},
                )
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SyncReview.objects.filter(pk=review_one.pk).exists())
        self.assertFalse(ReviewItem.objects.filter(pk=item_one.pk).exists())
        self.assertTrue(SyncReview.objects.filter(pk=review_two.pk).exists())
        self.assertTrue(ReviewItem.objects.filter(pk=item_two.pk).exists())
        sync_log_one.refresh_from_db()
        sync_log_two.refresh_from_db()
        self.assertEqual(sync_log_one.status, "success")
        self.assertEqual(sync_log_two.status, "pending_review")

    def test_review_list_hides_clear_controls_without_review_permission(self):
        self._create_review()

        response = ReviewListView.as_view()(
            self._request_with_messages(
                "GET",
                "/reviews/",
                permissions={"netbox_meraki.view_syncreview"},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("Clear Selected Reviews", content)
        self.assertNotIn(">Clear<", content)
