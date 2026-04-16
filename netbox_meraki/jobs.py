"""Background jobs for NetBox Meraki plugin."""

from datetime import timedelta

from django.utils import timezone

from netbox.jobs import JobRunner

from .models import MerakiSchedule, PluginSettings, SyncLog
from .sync_service import MerakiSyncService


class MerakiSyncJob(JobRunner):
    JOB_NAME = "Meraki Dashboard Sync"

    class Meta:
        name = "Meraki Dashboard Sync"
        description = "Synchronize Meraki inventory into NetBox"

    def run(self, sync_log_id=None, sync_mode=None, organization_id=None, network_ids=None, schedule_id=None, **kwargs):
        meraki_data = dict((self.job.data or {}).get("meraki") or {})
        sync_mode = sync_mode or meraki_data.get("sync_mode") or PluginSettings.get_settings().sync_mode
        organization_id = organization_id or meraki_data.get("organization_id") or None
        network_ids = network_ids or meraki_data.get("network_ids") or None
        schedule_id = schedule_id or meraki_data.get("schedule_id") or None

        if sync_log_id:
            sync_log = SyncLog.objects.get(pk=sync_log_id)
        else:
            sync_log = SyncLog.objects.create(
                status="queued",
                message="Scheduled sync queued",
                sync_mode=sync_mode,
            )

        sync_log.status = "running"
        sync_log.message = "Synchronization started"
        sync_log.sync_mode = sync_mode
        sync_log.save(update_fields=["status", "message", "sync_mode"])

        self.logger.info("Starting Meraki sync: mode=%s organization=%s networks=%s", sync_mode, organization_id, network_ids)

        try:
            service = MerakiSyncService(sync_mode=sync_mode, job=self.job)
            service.sync_all(sync_log=sync_log, organization_id=organization_id, network_ids=network_ids)
        finally:
            if schedule_id:
                self._finalize_schedule(schedule_id)

        self.logger.info("Meraki sync finished with status=%s", sync_log.status)
        return sync_log.message

    @classmethod
    def attach_meraki_data(cls, job, meraki_data):
        payload = dict(job.data or {})
        payload["meraki"] = meraki_data
        job.data = payload
        job.save(update_fields=["data"])
        return job

    @classmethod
    def enqueue_sync_job(
        cls,
        *,
        user=None,
        name=None,
        sync_log_id=None,
        sync_mode=None,
        organization_id=None,
        network_ids=None,
        schedule=None,
        schedule_at=None,
    ):
        cleaned_network_ids = [str(network_id).strip() for network_id in (network_ids or []) if str(network_id).strip()]
        enqueue_kwargs = {
            "name": name or cls.JOB_NAME,
            "user": user,
            "sync_mode": sync_mode,
        }
        if sync_log_id:
            enqueue_kwargs["sync_log_id"] = sync_log_id
        if organization_id:
            enqueue_kwargs["organization_id"] = organization_id
        if cleaned_network_ids:
            enqueue_kwargs["network_ids"] = cleaned_network_ids
        if schedule is not None:
            enqueue_kwargs["schedule_id"] = schedule.pk
        if schedule_at is not None and schedule_at > timezone.now():
            enqueue_kwargs["schedule_at"] = schedule_at

        job = cls.enqueue(**enqueue_kwargs)
        cls.attach_meraki_data(
            job,
            {
                "sync_mode": sync_mode or "",
                "organization_id": organization_id or "",
                "network_ids": cleaned_network_ids,
                "scheduled": schedule is not None,
                "schedule_id": schedule.pk if schedule is not None else None,
                "schedule_name": schedule.name if schedule is not None else "",
            },
        )
        return job

    def _finalize_schedule(self, schedule_id):
        schedule = MerakiSchedule.objects.filter(pk=schedule_id).select_related("current_job", "created_by").first()
        if schedule is None:
            return

        schedule.last_job = self.job
        current_job_matches = schedule.current_job_id == self.job.pk

        if not current_job_matches:
            schedule.save(update_fields=["last_job", "updated"])
            return

        schedule.current_job = None
        if not schedule.enabled:
            schedule.next_run_at = None
            schedule.save(update_fields=["current_job", "last_job", "next_run_at", "updated"])
            return

        if schedule.interval_minutes:
            reference_time = self.job.scheduled or schedule.next_run_at or timezone.now()
            next_run_at = max(
                reference_time + timedelta(minutes=schedule.interval_minutes),
                timezone.now() + timedelta(minutes=1),
            )
            next_job = self.enqueue_sync_job(
                user=schedule.created_by or self.job.user,
                name=schedule.name,
                sync_mode=schedule.sync_mode,
                organization_id=schedule.organization_id or None,
                network_ids=schedule.network_ids or [],
                schedule=schedule,
                schedule_at=next_run_at,
            )
            schedule.current_job = next_job
            schedule.next_run_at = next_run_at
            schedule.save(update_fields=["current_job", "last_job", "next_run_at", "updated"])
            return

        schedule.enabled = False
        schedule.next_run_at = None
        schedule.save(update_fields=["enabled", "current_job", "last_job", "next_run_at", "updated"])


jobs = [MerakiSyncJob]
