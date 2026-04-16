"""API views for the hardened NetBox Meraki plugin."""

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from netbox_meraki.jobs import MerakiSyncJob
from netbox_meraki.models import SyncLog
from .serializers import SyncLogSerializer


class SyncLogPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if view.action == "trigger_sync":
            return request.user.has_perm("netbox_meraki.run_sync")
        return request.user.has_perm("netbox_meraki.view_synclog")


class CancelSyncPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_perm("netbox_meraki.cancel_sync"))

    def has_object_permission(self, request, view, obj):
        return request.user.has_perm("netbox_meraki.cancel_sync")


class SyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SyncLog.objects.all().order_by("-timestamp")
    serializer_class = SyncLogSerializer
    permission_classes = [SyncLogPermission]

    @action(detail=False, methods=["post"])
    def trigger_sync(self, request):
        sync_mode = request.data.get("sync_mode", "review")
        organization_id = request.data.get("organization_id") or None
        network_ids = request.data.get("network_ids") or []
        if sync_mode not in {"auto", "review", "dry_run"}:
            return Response({"detail": "Invalid sync_mode."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(network_ids, list):
            return Response({"detail": "network_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        network_ids = [str(network_id).strip() for network_id in network_ids if str(network_id).strip()]
        sync_log = SyncLog.objects.create(status="queued", message="Sync queued", sync_mode=sync_mode)
        job = MerakiSyncJob.enqueue_sync_job(
            user=request.user,
            name=MerakiSyncJob.JOB_NAME,
            sync_log_id=sync_log.pk,
            sync_mode=sync_mode,
            organization_id=organization_id,
            network_ids=network_ids,
        )
        return Response({"job_id": job.pk, "sync_log_id": sync_log.pk}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        sync_log = self.get_object()
        return Response(
            {
                "id": sync_log.id,
                "status": sync_log.status,
                "message": sync_log.message,
                "current_operation": sync_log.current_operation,
                "progress_percent": sync_log.progress_percent,
                "progress_logs": sync_log.progress_logs,
                "cancel_requested": sync_log.cancel_requested,
                "organizations_synced": sync_log.organizations_synced,
                "networks_synced": sync_log.networks_synced,
                "devices_synced": sync_log.devices_synced,
                "vlans_synced": sync_log.vlans_synced,
                "prefixes_synced": sync_log.prefixes_synced,
                "ssids_synced": sync_log.ssids_synced,
            }
        )

    @action(detail=True, methods=["post"], permission_classes=[CancelSyncPermission])
    def cancel(self, request, pk=None):
        sync_log = self.get_object()
        sync_log.request_cancel()
        return Response({"message": "Cancellation requested", "cancel_requested": True})
