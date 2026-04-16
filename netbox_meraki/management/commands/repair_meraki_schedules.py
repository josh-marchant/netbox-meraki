"""Audit and repair legacy Meraki schedules after upgrading to the hardened fork."""

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Job

from netbox_meraki.jobs import MerakiSyncJob
from netbox_meraki.models import MerakiSchedule, ScheduledJobTracker


ACTIVE_STATUSES = {"pending", "scheduled", "running"}


def normalize_network_ids(value):
    if isinstance(value, str):
        return [value] if value else []
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(item) for item in list(value) if str(item)]


def extract_legacy_schedule_fields(job):
    data = job.data or {}
    meraki_data = data.get("meraki") or {}
    return {
        "name": meraki_data.get("schedule_name") or job.name,
        "sync_mode": meraki_data.get("sync_mode") or data.get("sync_mode") or "review",
        "organization_id": meraki_data.get("organization_id") or data.get("organization_id") or "",
        "network_ids": normalize_network_ids(meraki_data.get("network_ids") or data.get("network_ids")),
    }


def has_explicit_schedule_metadata(job):
    return bool(((job.data or {}).get("meraki") or {}).get("scheduled"))


def legacy_job_reason(job, tracked_active_job_ids, tracked_job_ids):
    data = job.data or {}
    meraki_data = data.get("meraki") or {}
    if has_explicit_schedule_metadata(job):
        return "deterministic", "explicit Meraki schedule metadata"
    if job.pk in tracked_active_job_ids:
        return "deterministic", "legacy tracker points to an active job"
    if job.pk in tracked_job_ids:
        return "ambiguous", "legacy tracker exists but does not point to an active job"
    if meraki_data:
        return "ambiguous", "Meraki job metadata exists without an explicit schedule marker"
    return None, None


def referenced_job_ids():
    referenced = set(
        MerakiSchedule.objects.exclude(current_job=None).values_list("current_job_id", flat=True)
    )
    referenced.update(
        MerakiSchedule.objects.exclude(last_job=None).values_list("last_job_id", flat=True)
    )
    return referenced


def build_schedule_payload(job):
    extracted = extract_legacy_schedule_fields(job)
    interval_minutes = job.interval or (job.data or {}).get("interval") or None
    run_at = job.scheduled or job.created
    is_active = job.status in ACTIVE_STATUSES
    explicit_plugin_owned = has_explicit_schedule_metadata(job)
    current_job = job if is_active else None
    last_job = job if explicit_plugin_owned and not is_active else None
    next_run_at = None
    if current_job is not None:
        next_run_at = run_at
    elif last_job is not None and interval_minutes:
        next_run_at = (last_job.scheduled or last_job.created or run_at) + timedelta(minutes=interval_minutes)
    return {
        "name": extracted["name"],
        "sync_mode": extracted["sync_mode"],
        "organization_id": extracted["organization_id"],
        "network_ids": extracted["network_ids"],
        "run_at": run_at,
        "interval_minutes": interval_minutes,
        "enabled": True if is_active else bool(interval_minutes),
        "next_run_at": next_run_at,
        "created_by": job.user,
        "current_job": current_job,
        "last_job": last_job,
    }


def schedule_signature(schedule):
    return (
        schedule.name,
        schedule.sync_mode,
        schedule.organization_id or "",
        tuple(sorted(str(value) for value in (schedule.network_ids or []))),
        schedule.interval_minutes or 0,
        schedule.created_by_id or 0,
    )


class Command(BaseCommand):
    help = "Audit Meraki schedule recovery state and explicitly repair selected legacy schedules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--import-job",
            dest="import_job_id",
            type=int,
            help="Create exactly one MerakiSchedule from the selected legacy NetBox job.",
        )
        parser.add_argument(
            "--requeue-schedule",
            dest="requeue_schedule_id",
            type=int,
            help="Queue a one-shot NetBox job for an enabled MerakiSchedule that has no active current job.",
        )

    def handle(self, *args, **options):
        import_job_id = options.get("import_job_id")
        requeue_schedule_id = options.get("requeue_schedule_id")
        if import_job_id and requeue_schedule_id:
            raise CommandError("Use only one repair action at a time.")

        tracked_job_ids = set(ScheduledJobTracker.objects.values_list("netbox_job_id", flat=True))
        tracked_active_job_ids = set(
            Job.objects.filter(pk__in=tracked_job_ids, status__in=ACTIVE_STATUSES).values_list("pk", flat=True)
        )

        if import_job_id:
            schedule = self._import_job(import_job_id, tracked_active_job_ids, tracked_job_ids)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported legacy job #{import_job_id} into Meraki schedule #{schedule.pk}."
                )
            )
            return

        if requeue_schedule_id:
            job = self._requeue_schedule(requeue_schedule_id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued NetBox job #{job.pk} for Meraki schedule #{requeue_schedule_id}."
                )
            )
            return

        self._audit(tracked_active_job_ids, tracked_job_ids)

    def _import_job(self, job_id, tracked_active_job_ids, tracked_job_ids):
        job = Job.objects.filter(pk=job_id).first()
        if job is None:
            raise CommandError(f"Job #{job_id} was not found.")

        if job.pk in referenced_job_ids():
            raise CommandError(f"Job #{job_id} is already linked to a Meraki schedule.")

        candidate_kind, reason = legacy_job_reason(job, tracked_active_job_ids, tracked_job_ids)
        if candidate_kind is None:
            raise CommandError(f"Job #{job_id} does not look like a legacy Meraki schedule.")

        payload = build_schedule_payload(job)
        schedule = MerakiSchedule.objects.create(**payload)

        meraki_payload = dict((job.data or {}).get("meraki") or {})
        meraki_payload.update(
            {
                "sync_mode": schedule.sync_mode,
                "organization_id": schedule.organization_id,
                "network_ids": schedule.network_ids,
                "scheduled": True,
                "schedule_id": schedule.pk,
                "schedule_name": schedule.name,
            }
        )
        payload_data = dict(job.data or {})
        payload_data["meraki"] = meraki_payload
        job.data = payload_data
        job.interval = None
        job.save(update_fields=["data", "interval"])

        self.stdout.write(f"Imported job #{job.pk} ({reason}).")
        return schedule

    def _requeue_schedule(self, schedule_id):
        schedule = MerakiSchedule.objects.select_related("current_job", "last_job", "created_by").filter(pk=schedule_id).first()
        if schedule is None:
            raise CommandError(f"Meraki schedule #{schedule_id} was not found.")
        if not schedule.enabled:
            raise CommandError(f"Meraki schedule #{schedule_id} is disabled.")
        if schedule.current_job is not None and schedule.current_job.status in ACTIVE_STATUSES:
            raise CommandError(f"Meraki schedule #{schedule_id} already has an active NetBox job.")

        if schedule.current_job is not None and schedule.last_job_id != schedule.current_job_id:
            schedule.last_job = schedule.current_job
        schedule.current_job = None

        schedule_at = schedule.next_run_at
        if schedule_at is None and schedule.interval_minutes and schedule.last_job is not None:
            base_time = schedule.last_job.scheduled or schedule.last_job.created
            if base_time is not None:
                schedule_at = base_time + timedelta(minutes=schedule.interval_minutes)
        if schedule_at is None:
            schedule_at = schedule.run_at
        if schedule_at is not None and schedule_at <= timezone.now():
            schedule_at = timezone.now() + timedelta(minutes=1)

        new_job = MerakiSyncJob.enqueue_sync_job(
            user=schedule.created_by,
            name=schedule.name,
            sync_mode=schedule.sync_mode,
            organization_id=schedule.organization_id or None,
            network_ids=schedule.network_ids or [],
            schedule=schedule,
            schedule_at=schedule_at,
        )
        schedule.current_job = new_job
        schedule.next_run_at = schedule_at or schedule.next_run_at
        schedule.save(update_fields=["current_job", "last_job", "next_run_at", "updated"])
        return new_job

    def _audit(self, tracked_active_job_ids, tracked_job_ids):
        linked_job_ids = referenced_job_ids()
        deterministic_jobs = []
        ambiguous_jobs = []
        for job in Job.objects.all().order_by("created"):
            if job.pk in linked_job_ids:
                continue
            candidate_kind, reason = legacy_job_reason(job, tracked_active_job_ids, tracked_job_ids)
            if candidate_kind == "deterministic":
                deterministic_jobs.append((job, reason))
            elif candidate_kind == "ambiguous":
                ambiguous_jobs.append((job, reason))

        orphaned_schedules = [
            schedule
            for schedule in MerakiSchedule.objects.select_related("current_job", "last_job").order_by("name", "pk")
            if schedule.enabled and not (schedule.current_job is not None and schedule.current_job.status in ACTIVE_STATUSES)
        ]

        duplicate_groups = defaultdict(list)
        for schedule in MerakiSchedule.objects.select_related("current_job", "last_job", "created_by").order_by("name", "pk"):
            duplicate_groups[schedule_signature(schedule)].append(schedule)
        duplicate_groups = {
            signature: schedules for signature, schedules in duplicate_groups.items() if len(schedules) > 1
        }

        self.stdout.write("Deterministic legacy jobs that can be imported:")
        if deterministic_jobs:
            for job, reason in deterministic_jobs:
                self.stdout.write(f"  - Job #{job.pk}: {self._describe_job(job)} [{reason}]")
        else:
            self.stdout.write("  None")

        self.stdout.write("")
        self.stdout.write("Ambiguous legacy jobs that require manual selection:")
        if ambiguous_jobs:
            for job, reason in ambiguous_jobs:
                self.stdout.write(f"  - Job #{job.pk}: {self._describe_job(job)} [{reason}]")
        else:
            self.stdout.write("  None")

        self.stdout.write("")
        self.stdout.write("Enabled Meraki schedules missing an active current job:")
        if orphaned_schedules:
            for schedule in orphaned_schedules:
                self.stdout.write(f"  - Schedule #{schedule.pk}: {self._describe_schedule(schedule)}")
        else:
            self.stdout.write("  None")

        self.stdout.write("")
        self.stdout.write("Duplicate Meraki schedule groups by stable signature:")
        if duplicate_groups:
            for signature, schedules in duplicate_groups.items():
                self.stdout.write(f"  - Signature {signature}:")
                for schedule in schedules:
                    self.stdout.write(f"      Schedule #{schedule.pk}: {self._describe_schedule(schedule)}")
        else:
            self.stdout.write("  None")

    def _describe_job(self, job):
        fields = extract_legacy_schedule_fields(job)
        scope = f"org={fields['organization_id'] or '*'} networks={fields['network_ids'] or '*'}"
        return (
            f"name='{job.name}' status={job.status} interval={job.interval or '-'} "
            f"run_at={job.scheduled or job.created} {scope}"
        )

    def _describe_schedule(self, schedule):
        current_status = schedule.current_job.status if schedule.current_job is not None else "-"
        return (
            f"name='{schedule.name}' current_job={schedule.current_job_id or '-'} "
            f"current_status={current_status} next_run_at={schedule.next_run_at or '-'} "
            f"interval={schedule.interval_minutes or '-'} org={schedule.organization_id or '*'} "
            f"networks={schedule.network_ids or '*'}"
        )
