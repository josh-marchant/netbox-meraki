import logging

from django.contrib import messages
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.core.paginator import Paginator
from django.utils import timezone

from utilities.views import ContentTypePermissionRequiredMixin

from .forms import (
    MerakiVLANResolutionRuleForm,
    PluginSettingsForm,
    PrefixFilterRuleForm,
    ReviewItemEditForm,
    ScheduledSyncForm,
    SiteNameRuleForm,
    SyncRequestForm,
)
from .jobs import MerakiSyncJob
from .meraki_client import MerakiAPIClient
from .models import (
    MerakiSchedule,
    MerakiVLANResolutionRule,
    PluginSettings,
    PrefixFilterRule,
    ReviewItem,
    SiteNameRule,
    SyncLog,
    SyncReview,
)
from .sync_service import MerakiSyncService

logger = logging.getLogger("netbox_meraki")
REVIEW_SECTION_LABELS = {
    "site": "Sites",
    "device": "Devices",
    "vlan": "VLANs",
    "prefix": "Prefixes",
    "ssid": "SSIDs",
    "device_type": "Device Types",
    "interface": "Interfaces",
    "ip_address": "IP Addresses",
}
REVIEW_SECTION_ORDER = ["site", "device", "vlan", "prefix", "ssid", "device_type", "interface", "ip_address"]


def _scheduled_jobs():
    return MerakiSchedule.objects.select_related("current_job", "last_job", "created_by").order_by("-enabled", "next_run_at", "name")


def _job_is_replaceable(job):
    return job is not None and job.status in {"pending", "scheduled"}


class PermissionRequiredView(ContentTypePermissionRequiredMixin, View):
    permission_required = None

    def get_required_permission(self):
        return self.permission_required


def _can_access_meraki_lookup_api(user):
    return any(
        user.has_perm(permission)
        for permission in (
            "netbox_meraki.run_sync",
            "netbox_meraki.add_merakivlanresolutionrule",
            "netbox_meraki.change_merakivlanresolutionrule",
            "core.add_job",
            "core.change_job",
        )
    )


def _review_counts(items_queryset):
    return {
        "total": items_queryset.count(),
        "pending": items_queryset.filter(status="pending").count(),
        "approved": items_queryset.filter(status="approved").count(),
        "rejected": items_queryset.filter(status="rejected").count(),
        "applied": items_queryset.filter(status="applied").count(),
        "failed": items_queryset.filter(status="failed").count(),
    }


def _review_sections(items_queryset):
    items = list(items_queryset.order_by("item_type", "object_name"))
    grouped = {}
    for item in items:
        grouped.setdefault(item.item_type, []).append(item)

    sections = []
    for item_type in REVIEW_SECTION_ORDER:
        section_items = grouped.pop(item_type, [])
        if not section_items:
            continue
        sections.append(
            {
                "item_type": item_type,
                "label": REVIEW_SECTION_LABELS.get(item_type, item_type.replace("_", " ").title()),
                "items": section_items,
                "total_count": len(section_items),
                "pending_count": sum(1 for item in section_items if item.status == "pending"),
                "approved_count": sum(1 for item in section_items if item.status == "approved"),
                "failed_count": sum(1 for item in section_items if item.status == "failed"),
            }
        )

    for item_type, section_items in grouped.items():
        sections.append(
            {
                "item_type": item_type,
                "label": REVIEW_SECTION_LABELS.get(item_type, item_type.replace("_", " ").title()),
                "items": section_items,
                "total_count": len(section_items),
                "pending_count": sum(1 for item in section_items if item.status == "pending"),
                "approved_count": sum(1 for item in section_items if item.status == "approved"),
                "failed_count": sum(1 for item in section_items if item.status == "failed"),
            }
        )

    return sections


def _clear_review(review, user=None):
    sync_log = review.sync_log
    review_id = review.pk
    sync_log.status = "success"
    sync_log.message = f"Review #{review_id} cleared by {getattr(user, 'username', user) or 'user'}."
    sync_log.save(update_fields=["status", "message"])
    review.delete()


class DashboardView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_synclog"

    def get(self, request):
        pending_reviews = (
            SyncReview.objects.filter(status__in=["pending", "approved", "partially_approved"]).order_by("-created")[:10]
            if request.user.has_perm("netbox_meraki.view_syncreview")
            else []
        )
        scheduled_jobs = _scheduled_jobs()[:10] if request.user.has_perm("core.view_job") else []
        context = {
            "recent_logs": SyncLog.objects.order_by("-timestamp")[:10],
            "pending_reviews": pending_reviews,
            "scheduled_jobs": scheduled_jobs,
        }
        return render(request, "netbox_meraki/dashboard.html", context)


class SyncView(PermissionRequiredView):
    permission_required = "netbox_meraki.run_sync"

    def _organizations(self, request):
        try:
            return MerakiAPIClient().get_organizations()
        except Exception:
            logger.exception("Could not load Meraki organizations for sync view")
            messages.warning(request, "Could not load Meraki organizations right now.")
            return []

    def get(self, request):
        form = SyncRequestForm(organizations=self._organizations(request), initial={"sync_mode": PluginSettings.get_settings().sync_mode})
        return render(request, "netbox_meraki/sync.html", {"form": form})

    def post(self, request):
        organizations = self._organizations(request)
        form = SyncRequestForm(request.POST, organizations=organizations)
        if not form.is_valid():
            return render(request, "netbox_meraki/sync.html", {"form": form})

        sync_log = SyncLog.objects.create(status="queued", message="Sync queued", sync_mode=form.cleaned_data["sync_mode"])
        network_ids = [] if form.cleaned_data.get("sync_all_networks", True) else [value.strip() for value in request.POST.getlist("network_ids") if value.strip()]
        job = MerakiSyncJob.enqueue_sync_job(
            user=request.user,
            name=MerakiSyncJob.JOB_NAME,
            sync_log_id=sync_log.pk,
            sync_mode=form.cleaned_data["sync_mode"],
            organization_id=form.cleaned_data.get("organization_id") or None,
            network_ids=network_ids,
        )
        messages.success(request, f"Sync queued successfully as job #{job.pk}.")
        return redirect(sync_log.get_absolute_url())


class SyncLogView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_synclog"

    def get(self, request, pk):
        sync_log = get_object_or_404(SyncLog, pk=pk)
        return render(request, "netbox_meraki/synclog.html", {"sync_log": sync_log})


class SyncProgressAPIView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_synclog"

    def get(self, request, pk):
        sync_log = get_object_or_404(SyncLog, pk=pk)
        return JsonResponse(
            {
                "id": sync_log.pk,
                "status": sync_log.status,
                "message": sync_log.message,
                "current_operation": sync_log.current_operation,
                "progress_percent": sync_log.progress_percent,
                "progress_logs": sync_log.progress_logs,
                "cancel_requested": sync_log.cancel_requested,
            }
        )


class SyncCancelAPIView(PermissionRequiredView):
    permission_required = "netbox_meraki.cancel_sync"

    def post(self, request, pk):
        sync_log = get_object_or_404(SyncLog, pk=pk)
        sync_log.request_cancel()
        return JsonResponse({"ok": True, "status": sync_log.status, "cancel_requested": True})


class OrganizationsAPIView(PermissionRequiredView):
    permission_required = None

    def get(self, request):
        if not _can_access_meraki_lookup_api(request.user):
            return JsonResponse({"detail": "You do not have permission to load Meraki organizations."}, status=403)
        try:
            return JsonResponse({"organizations": MerakiAPIClient().get_organizations()})
        except Exception:
            logger.exception("Could not load Meraki organizations")
            return JsonResponse({"detail": "Unable to load Meraki organizations."}, status=502)


class NetworksAPIView(PermissionRequiredView):
    permission_required = None

    def get(self, request, org_id):
        if not _can_access_meraki_lookup_api(request.user):
            return JsonResponse({"detail": "You do not have permission to load Meraki networks."}, status=403)
        try:
            return JsonResponse({"networks": MerakiAPIClient().get_networks(org_id)})
        except Exception:
            logger.exception("Could not load Meraki networks for organization %s", org_id)
            return JsonResponse({"detail": "Unable to load Meraki networks."}, status=502)


class ConfigView(PermissionRequiredView):
    permission_required = "netbox_meraki.change_pluginsettings"

    def get(self, request):
        form = PluginSettingsForm(instance=PluginSettings.get_settings())
        return render(
            request,
            "netbox_meraki/config.html",
            {
                "form": form,
                "site_rule_count": SiteNameRule.objects.count(),
                "prefix_filter_count": PrefixFilterRule.objects.count(),
                "vlan_rule_count": MerakiVLANResolutionRule.objects.count(),
            },
        )

    @method_decorator(sensitive_post_parameters("meraki_api_key"))
    def post(self, request):
        instance = PluginSettings.get_settings()
        form = PluginSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Plugin settings updated.")
            return redirect("plugins:netbox_meraki:config")
        return render(
            request,
            "netbox_meraki/config.html",
            {
                "form": form,
                "site_rule_count": SiteNameRule.objects.count(),
                "prefix_filter_count": PrefixFilterRule.objects.count(),
                "vlan_rule_count": MerakiVLANResolutionRule.objects.count(),
            },
        )


class JobHistoryView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_synclog"

    def get(self, request):
        paginator = Paginator(SyncLog.objects.order_by("-timestamp"), 25)
        page = paginator.get_page(request.GET.get("page"))
        return render(request, "netbox_meraki/job_history.html", {"logs": page})


class SimpleRuleListView(PermissionRequiredView):
    model = None
    template_name = ""
    permission_required = None

    def get(self, request):
        return render(request, self.template_name, {"objects": self.model.objects.order_by("priority", "name")})


class SimpleRuleFormView(PermissionRequiredView):
    model = None
    form_class = None
    template_name = ""
    permission_required = None

    def get_object(self, pk=None):
        return get_object_or_404(self.model, pk=pk) if pk is not None else None

    def get(self, request, pk=None):
        obj = self.get_object(pk)
        form = self.form_class(instance=obj)
        return render(request, self.template_name, {"form": form, "object": obj})

    def post(self, request, pk=None):
        obj = self.get_object(pk)
        form = self.form_class(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())
        return render(request, self.template_name, {"form": form, "object": obj})

    def get_success_url(self):
        raise NotImplementedError


class SimpleRuleDeleteView(PermissionRequiredView):
    model = None
    template_name = ""
    permission_required = None

    def get(self, request, pk):
        obj = get_object_or_404(self.model, pk=pk)
        return render(request, self.template_name, {"object": obj})

    def post(self, request, pk):
        obj = get_object_or_404(self.model, pk=pk)
        obj.delete()
        return redirect(self.get_success_url())

    def get_success_url(self):
        raise NotImplementedError


class SiteNameRuleListView(SimpleRuleListView):
    model = SiteNameRule
    template_name = "netbox_meraki/sitenamerule_list.html"
    permission_required = "netbox_meraki.view_sitenamerule"


class SiteNameRuleCreateView(SimpleRuleFormView):
    model = SiteNameRule
    form_class = SiteNameRuleForm
    template_name = "netbox_meraki/sitenamerule_form.html"
    permission_required = "netbox_meraki.add_sitenamerule"

    def get_success_url(self):
        return "plugins:netbox_meraki:sitenamerule_list"


class SiteNameRuleUpdateView(SiteNameRuleCreateView):
    permission_required = "netbox_meraki.change_sitenamerule"


class SiteNameRuleDeleteView(SimpleRuleDeleteView):
    model = SiteNameRule
    template_name = "netbox_meraki/sitenamerule_confirm_delete.html"
    permission_required = "netbox_meraki.delete_sitenamerule"

    def get_success_url(self):
        return "plugins:netbox_meraki:sitenamerule_list"


class PrefixFilterRuleListView(SimpleRuleListView):
    model = PrefixFilterRule
    template_name = "netbox_meraki/prefixfilterrule_list.html"
    permission_required = "netbox_meraki.view_prefixfilterrule"


class PrefixFilterRuleCreateView(SimpleRuleFormView):
    model = PrefixFilterRule
    form_class = PrefixFilterRuleForm
    template_name = "netbox_meraki/prefixfilterrule_form.html"
    permission_required = "netbox_meraki.add_prefixfilterrule"

    def get_success_url(self):
        return "plugins:netbox_meraki:prefixfilterrule_list"


class PrefixFilterRuleUpdateView(PrefixFilterRuleCreateView):
    permission_required = "netbox_meraki.change_prefixfilterrule"


class PrefixFilterRuleDeleteView(SimpleRuleDeleteView):
    model = PrefixFilterRule
    template_name = "netbox_meraki/prefixfilterrule_confirm_delete.html"
    permission_required = "netbox_meraki.delete_prefixfilterrule"

    def get_success_url(self):
        return "plugins:netbox_meraki:prefixfilterrule_list"


class MerakiVLANResolutionRuleListView(SimpleRuleListView):
    model = MerakiVLANResolutionRule
    template_name = "netbox_meraki/vlanresolutionrule_list.html"
    permission_required = "netbox_meraki.view_merakivlanresolutionrule"


class MerakiVLANResolutionRuleCreateView(SimpleRuleFormView):
    model = MerakiVLANResolutionRule
    form_class = MerakiVLANResolutionRuleForm
    template_name = "netbox_meraki/vlanresolutionrule_form.html"
    permission_required = "netbox_meraki.add_merakivlanresolutionrule"

    def _organizations(self, request):
        try:
            return MerakiAPIClient().get_organizations()
        except Exception:
            logger.exception("Could not load Meraki organizations for VLAN resolution rules")
            messages.warning(request, "Could not load Meraki organizations right now.")
            return []

    def _networks(self, request, organization_id):
        if not organization_id:
            return []
        try:
            return MerakiAPIClient().get_networks(organization_id)
        except Exception:
            logger.exception("Could not load Meraki networks for VLAN resolution rules org %s", organization_id)
            messages.warning(request, "Could not load Meraki networks right now.")
            return []

    def get_success_url(self):
        return "plugins:netbox_meraki:vlanresolutionrule_list"

    def get(self, request, pk=None):
        obj = self.get_object(pk)
        organizations = self._organizations(request)
        selected_org = getattr(obj, "meraki_organization_id", "") if obj is not None else ""
        networks = self._networks(request, selected_org) if selected_org else []
        form = self.form_class(instance=obj, organizations=organizations, networks=networks)
        return render(request, self.template_name, {"form": form, "object": obj})

    def post(self, request, pk=None):
        obj = self.get_object(pk)
        organizations = self._organizations(request)
        selected_org = request.POST.get("meraki_organization_id") or getattr(obj, "meraki_organization_id", "")
        networks = self._networks(request, selected_org) if selected_org else []
        form = self.form_class(request.POST, instance=obj, organizations=organizations, networks=networks)
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())
        return render(request, self.template_name, {"form": form, "object": obj})


class MerakiVLANResolutionRuleUpdateView(MerakiVLANResolutionRuleCreateView):
    permission_required = "netbox_meraki.change_merakivlanresolutionrule"


class MerakiVLANResolutionRuleDeleteView(SimpleRuleDeleteView):
    model = MerakiVLANResolutionRule
    template_name = "netbox_meraki/vlanresolutionrule_confirm_delete.html"
    permission_required = "netbox_meraki.delete_merakivlanresolutionrule"

    def get_success_url(self):
        return "plugins:netbox_meraki:vlanresolutionrule_list"


class ReviewListView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_syncreview"

    def get(self, request):
        reviews = SyncReview.objects.select_related("sync_log").order_by("-created")
        return render(request, "netbox_meraki/review_list.html", {"reviews": reviews})


class ReviewDetailView(PermissionRequiredView):
    permission_required = "netbox_meraki.view_syncreview"

    def get(self, request, pk):
        review = get_object_or_404(SyncReview, pk=pk)
        items = review.items.all()
        counts = _review_counts(items)
        sections = _review_sections(items)
        return render(
            request,
            "netbox_meraki/review_detail.html",
            {
                "review": review,
                "counts": counts,
                "sections": sections,
                "can_apply": counts["approved"] > 0 and request.user.has_perm("netbox_meraki.review_sync"),
                "can_bulk_review": counts["pending"] > 0 and request.user.has_perm("netbox_meraki.review_sync"),
            },
        )


class ReviewItemActionView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def post(self, request, pk, item_pk):
        review = get_object_or_404(SyncReview, pk=pk)
        item = get_object_or_404(ReviewItem, pk=item_pk, review=review)
        action = request.POST.get("action")
        if action == "approve":
            item.status = "approved"
            item.error_message = ""
            item.save(update_fields=["status", "error_message"])
        elif action == "reject":
            item.status = "rejected"
            item.error_message = ""
            item.save(update_fields=["status", "error_message"])
        elif action == "apply":
            try:
                MerakiSyncService(sync_mode="auto").apply_review_item(item)
                item.status = "applied"
                item.error_message = ""
                item.save(update_fields=["status", "error_message"])
            except Exception:
                logger.exception("Failed to apply review item %s", item.pk)
                item.status = "failed"
                item.error_message = "Unable to apply this review item."
                item.save(update_fields=["status", "error_message"])
                messages.error(request, "Unable to apply that review item.")
        else:
            raise Http404
        review.mark_reviewed(request.user, review.calculate_status())
        return redirect(review.get_absolute_url())


class ReviewApplyView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def post(self, request, pk):
        review = get_object_or_404(SyncReview, pk=pk)
        review.apply_approved_items(request.user)
        messages.success(request, "Approved review items applied.")
        return redirect(review.get_absolute_url())


class ReviewBulkActionView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def post(self, request, pk):
        review = get_object_or_404(SyncReview, pk=pk)
        action = request.POST.get("action")
        item_type = (request.POST.get("item_type") or "").strip()
        items = review.items.filter(status="pending")
        if item_type:
            items = items.filter(item_type=item_type)

        if action == "approve_pending":
            updated = items.update(status="approved", error_message="")
            messages.success(request, f"Approved {updated} pending review item(s).")
        elif action == "reject_pending":
            updated = items.update(status="rejected", error_message="")
            messages.success(request, f"Rejected {updated} pending review item(s).")
        else:
            raise Http404

        review.mark_reviewed(request.user, review.calculate_status())
        return redirect(review.get_absolute_url())


class ReviewClearView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def post(self, request, pk):
        review = get_object_or_404(SyncReview.objects.select_related("sync_log"), pk=pk)
        _clear_review(review, user=request.user)
        messages.success(request, "Review cleared. The sync log has been kept for history.")
        return redirect("plugins:netbox_meraki:review_list")


class ReviewBulkClearView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def post(self, request):
        selected_ids = []
        for value in request.POST.getlist("review_ids"):
            try:
                selected_ids.append(int(value))
            except (TypeError, ValueError):
                continue

        if not selected_ids:
            messages.warning(request, "Select at least one review to clear.")
            return redirect("plugins:netbox_meraki:review_list")

        reviews = list(SyncReview.objects.select_related("sync_log").filter(pk__in=selected_ids))
        for review in reviews:
            _clear_review(review, user=request.user)

        if reviews:
            messages.success(request, f"Cleared {len(reviews)} review(s).")
        else:
            messages.warning(request, "No matching reviews were found to clear.")
        return redirect("plugins:netbox_meraki:review_list")


class ReviewItemEditView(PermissionRequiredView):
    permission_required = "netbox_meraki.review_sync"

    def get(self, request, pk, item_pk):
        review = get_object_or_404(SyncReview, pk=pk)
        item = get_object_or_404(ReviewItem, pk=item_pk, review=review)
        form = ReviewItemEditForm(review_item=item)
        return render(request, "netbox_meraki/review_item_edit.html", {"review": review, "item": item, "form": form})

    def post(self, request, pk, item_pk):
        review = get_object_or_404(SyncReview, pk=pk)
        item = get_object_or_404(ReviewItem, pk=item_pk, review=review)
        form = ReviewItemEditForm(request.POST, review_item=item)
        if form.is_valid():
            item.editable_data = form.cleaned_data["editable_data"]
            item.notes = form.cleaned_data["notes"]
            item.save(update_fields=["editable_data", "notes"])
            messages.success(request, "Review item updated.")
            return redirect(review.get_absolute_url())
        return render(request, "netbox_meraki/review_item_edit.html", {"review": review, "item": item, "form": form})

class ScheduledSyncView(PermissionRequiredView):
    permission_required = "core.view_job"

    def _organizations(self, request):
        try:
            return MerakiAPIClient().get_organizations()
        except Exception:
            logger.exception("Could not load Meraki organizations for scheduling")
            messages.warning(request, "Could not load Meraki organizations right now.")
            return []

    def get(self, request):
        schedules = _scheduled_jobs()
        form = ScheduledSyncForm(organizations=self._organizations(request), initial={"sync_mode": PluginSettings.get_settings().sync_mode})
        return render(request, "netbox_meraki/scheduled_sync.html", {"schedules": schedules, "form": form, "can_schedule": request.user.has_perm("core.add_job")})

    def post(self, request):
        if not request.user.has_perm("core.add_job"):
            raise Http404
        organizations = self._organizations(request)
        form = ScheduledSyncForm(request.POST, organizations=organizations)
        if not form.is_valid():
            schedules = _scheduled_jobs()
            return render(request, "netbox_meraki/scheduled_sync.html", {"schedules": schedules, "form": form, "can_schedule": True})
        schedule = self._create_scheduled_job(request, form)
        messages.success(request, f"Scheduled sync '{schedule.name}' created.")
        return redirect("plugins:netbox_meraki:scheduled_sync")

    def _get_schedule_timing(self, form):
        interval_value = form.cleaned_data["interval"]
        interval_minutes = None if interval_value == "0" else form.cleaned_data["custom_interval"] if interval_value == "custom" else int(interval_value)
        run_at = form.cleaned_data.get("scheduled_time") or timezone.now()
        return interval_minutes, run_at

    def _queue_schedule_job(self, schedule, user=None):
        schedule_at = schedule.next_run_at if schedule.next_run_at and schedule.next_run_at > timezone.now() else None
        job = MerakiSyncJob.enqueue_sync_job(
            user=user or schedule.created_by,
            name=schedule.name,
            sync_mode=schedule.sync_mode,
            organization_id=schedule.organization_id or None,
            network_ids=schedule.network_ids or [],
            schedule=schedule,
            schedule_at=schedule_at,
        )
        schedule.current_job = job
        schedule.save(update_fields=["current_job", "updated"])
        return job

    def _create_scheduled_job(self, request, form, schedule=None):
        network_ids = [] if form.cleaned_data.get("sync_all_networks", True) else [value.strip() for value in request.POST.getlist("network_ids") if value.strip()]
        interval_minutes, run_at = self._get_schedule_timing(form)

        if schedule is None:
            schedule = MerakiSchedule(created_by=request.user)

        old_job = schedule.current_job
        schedule.name = form.cleaned_data["name"]
        schedule.sync_mode = form.cleaned_data["sync_mode"]
        schedule.organization_id = form.cleaned_data.get("organization_id") or ""
        schedule.network_ids = network_ids
        schedule.run_at = run_at
        schedule.interval_minutes = interval_minutes
        schedule.enabled = True
        schedule.next_run_at = run_at
        schedule.save()

        if _job_is_replaceable(old_job):
            old_job.delete()
            schedule.current_job = None
            schedule.save(update_fields=["current_job", "updated"])

        if schedule.current_job is None:
            self._queue_schedule_job(schedule, user=request.user)
        return schedule


class ScheduledSyncEditView(PermissionRequiredView):
    permission_required = "core.change_job"

    def _organizations(self, request):
        try:
            return MerakiAPIClient().get_organizations()
        except Exception:
            logger.exception("Could not load Meraki organizations for schedule edit")
            messages.warning(request, "Could not load Meraki organizations right now.")
            return []

    def get(self, request, pk):
        schedule = get_object_or_404(MerakiSchedule.objects.select_related("current_job", "last_job"), pk=pk)
        organizations = self._organizations(request)
        interval_value = str(schedule.interval_minutes or "0")
        scheduled_time = schedule.next_run_at or schedule.run_at
        initial = {"name": schedule.name, "interval": interval_value if interval_value in {"0", "60", "360", "720", "1440", "10080"} else "custom", "custom_interval": None if interval_value in {"0", "60", "360", "720", "1440", "10080"} else schedule.interval_minutes, "scheduled_time": scheduled_time, "sync_mode": schedule.sync_mode or PluginSettings.get_settings().sync_mode, "organization_id": schedule.organization_id or "", "network_ids": schedule.network_ids or [], "sync_all_networks": not bool(schedule.network_ids)}
        form = ScheduledSyncForm(initial=initial, organizations=organizations)
        return render(request, "netbox_meraki/scheduled_sync_edit.html", {"schedule": schedule, "form": form, "job_kwargs": {"network_ids": schedule.network_ids or []}, "can_schedule": True})

    def post(self, request, pk):
        schedule = get_object_or_404(MerakiSchedule, pk=pk)
        form = ScheduledSyncForm(request.POST, organizations=self._organizations(request))
        if not form.is_valid():
            return render(request, "netbox_meraki/scheduled_sync_edit.html", {"schedule": schedule, "form": form, "job_kwargs": {"network_ids": schedule.network_ids or []}, "can_schedule": True})
        ScheduledSyncView()._create_scheduled_job(request, form, schedule=schedule)
        messages.success(request, "Scheduled sync updated.")
        return redirect("plugins:netbox_meraki:scheduled_sync")


class ScheduledSyncDeleteView(PermissionRequiredView):
    permission_required = "core.delete_job"

    def post(self, request, pk):
        schedule = get_object_or_404(MerakiSchedule.objects.select_related("current_job"), pk=pk)
        if schedule.current_job and _job_is_replaceable(schedule.current_job):
            schedule.current_job.delete()
        schedule.delete()
        messages.success(request, "Scheduled sync deleted.")
        return redirect("plugins:netbox_meraki:scheduled_sync")


class ScheduledSyncToggleView(PermissionRequiredView):
    permission_required = "core.change_job"

    def post(self, request, pk):
        schedule = get_object_or_404(MerakiSchedule.objects.select_related("current_job"), pk=pk)
        schedule.enabled = not schedule.enabled
        if not schedule.enabled:
            schedule.next_run_at = None
            if _job_is_replaceable(schedule.current_job):
                schedule.current_job.delete()
                schedule.current_job = None
        elif schedule.current_job is None:
            schedule.next_run_at = schedule.run_at if schedule.run_at > timezone.now() else timezone.now()
            ScheduledSyncView()._queue_schedule_job(schedule, user=request.user)
        schedule.save()
        messages.success(request, "Scheduled sync updated.")
        return redirect("plugins:netbox_meraki:scheduled_sync")
