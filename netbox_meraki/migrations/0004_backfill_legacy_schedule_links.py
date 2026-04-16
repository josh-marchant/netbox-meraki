from django.db import migrations
from datetime import timedelta


def backfill_legacy_schedule_links(apps, schema_editor):
    MerakiSchedule = apps.get_model("netbox_meraki", "MerakiSchedule")
    for schedule in MerakiSchedule.objects.select_related("last_job").filter(
        interval_minutes__isnull=False,
        next_run_at__isnull=True,
        last_job__isnull=False,
    ):
        base_time = schedule.last_job.scheduled or schedule.last_job.created
        if base_time is None:
            continue
        schedule.next_run_at = base_time + timedelta(minutes=schedule.interval_minutes)
        schedule.save(update_fields=["next_run_at", "updated"])


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_meraki", "0003_merakischedule_and_fixups"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_schedule_links, migrations.RunPython.noop),
    ]
