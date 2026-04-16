"""Forms for the hardened NetBox Meraki plugin."""

import json

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    MerakiVLANResolutionRule,
    PluginSettings,
    PrefixFilterRule,
    ReviewItem,
    SiteNameRule,
)


class MultipleCharField(forms.Field):
    def to_python(self, value):
        if not value:
            return []
        return value if isinstance(value, list) else [value]


class OrganizationScopedFormMixin:
    def populate_organization_choices(self, organizations):
        choices = [("", "All Organizations")]
        choices.extend((str(org["id"]), org.get("name", str(org["id"]))) for org in organizations)
        self.fields["organization_id"].choices = choices


class MerakiChoiceFormMixin:
    organization_empty_label = "Any Organization"
    network_empty_label = "Any Network"

    def _organization_choices(self, organizations, current_value=""):
        choices = [("", self.organization_empty_label)]
        seen = set()
        for org in organizations:
            value = str(org.get("id", ""))
            if not value:
                continue
            choices.append((value, org.get("name", value)))
            seen.add(value)
        if current_value and current_value not in seen:
            choices.append((current_value, f"Unknown organization ({current_value})"))
        return choices

    def _network_choices(self, networks, current_value=""):
        choices = [("", self.network_empty_label)]
        seen = set()
        for network in networks:
            value = str(network.get("id", ""))
            if not value:
                continue
            choices.append((value, network.get("name", value)))
            seen.add(value)
        if current_value and current_value not in seen:
            choices.append((current_value, f"Unknown network ({current_value})"))
        return choices


class SyncRequestForm(forms.Form, OrganizationScopedFormMixin):
    sync_mode = forms.ChoiceField(
        choices=[("auto", "Auto Sync"), ("review", "Sync with Review"), ("dry_run", "Dry Run")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    organization_id = forms.ChoiceField(required=False, choices=[("", "All Organizations")], widget=forms.Select(attrs={"class": "form-select", "id": "organization_id"}))
    network_ids = MultipleCharField(required=False, widget=forms.HiddenInput())
    sync_all_networks = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "sync_all_networks"}))

    def __init__(self, *args, **kwargs):
        organizations = kwargs.pop("organizations", [])
        super().__init__(*args, **kwargs)
        self.populate_organization_choices(organizations)


class ScheduledSyncForm(forms.Form, OrganizationScopedFormMixin):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"class": "form-control"}))
    interval = forms.ChoiceField(
        choices=[("0", "Run Once"), ("custom", "Custom Interval"), ("60", "Hourly"), ("360", "Every 6 Hours"), ("720", "Every 12 Hours"), ("1440", "Daily"), ("10080", "Weekly")],
        initial="1440",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    custom_interval = forms.IntegerField(required=False, min_value=5, widget=forms.NumberInput(attrs={"class": "form-control", "min": "5"}))
    scheduled_time = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}))
    sync_mode = forms.ChoiceField(
        choices=[("auto", "Auto Sync"), ("review", "Sync with Review"), ("dry_run", "Dry Run")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    organization_id = forms.ChoiceField(required=False, choices=[("", "All Organizations")], widget=forms.Select(attrs={"class": "form-select", "id": "organization_id"}))
    network_ids = MultipleCharField(required=False, widget=forms.HiddenInput())
    sync_all_networks = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "sync_all_networks_scheduled"}))

    def __init__(self, *args, **kwargs):
        organizations = kwargs.pop("organizations", [])
        super().__init__(*args, **kwargs)
        self.populate_organization_choices(organizations)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("interval") == "custom" and not cleaned_data.get("custom_interval"):
            raise ValidationError({"custom_interval": "Custom interval is required when using a custom schedule."})
        if cleaned_data.get("interval") == "0" and not cleaned_data.get("scheduled_time"):
            raise ValidationError({"scheduled_time": "A scheduled time is required for a one-time job."})
        return cleaned_data


class ReviewItemEditForm(forms.Form):
    editable_data = forms.CharField(widget=forms.Textarea(attrs={"class": "form-control", "rows": 16}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))

    def __init__(self, *args, **kwargs):
        review_item = kwargs.pop("review_item", None)
        super().__init__(*args, **kwargs)
        if review_item is not None and not self.is_bound:
            self.initial["editable_data"] = json.dumps(review_item.get_final_data(), indent=2, sort_keys=True)
            self.initial["notes"] = review_item.notes

    def clean_editable_data(self):
        try:
            return json.loads(self.cleaned_data["editable_data"])
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Editable data must be valid JSON: {exc}")


class PluginSettingsForm(forms.ModelForm):
    meraki_api_key = forms.CharField(
        required=False,
        label="Meraki API Key",
        widget=forms.PasswordInput(render_value=False, attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text="Leave blank to keep the currently stored API key.",
    )
    clear_api_key = forms.BooleanField(
        required=False,
        label="Clear stored API key",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Remove the currently stored Meraki API key.",
    )

    class Meta:
        model = PluginSettings
        fields = [
            "meraki_base_url",
            "mx_device_role",
            "ms_device_role",
            "mr_device_role",
            "mg_device_role",
            "mv_device_role",
            "mt_device_role",
            "default_device_role",
            "auto_create_device_roles",
            "sync_mode",
            "device_name_transform",
            "site_name_transform",
            "vlan_name_transform",
            "ssid_name_transform",
            "site_tags",
            "device_tags",
            "vlan_tags",
            "prefix_tags",
            "process_unmatched_sites",
            "enable_api_throttling",
            "api_requests_per_second",
            "enable_cleanup",
        ]
        widgets = {
            "meraki_base_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://api.meraki.com/api/v1"}),
            "mx_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "ms_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "mr_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "mg_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "mv_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "mt_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "default_device_role": forms.TextInput(attrs={"class": "form-control"}),
            "auto_create_device_roles": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "sync_mode": forms.Select(attrs={"class": "form-select"}),
            "device_name_transform": forms.Select(attrs={"class": "form-select"}),
            "site_name_transform": forms.Select(attrs={"class": "form-select"}),
            "vlan_name_transform": forms.Select(attrs={"class": "form-select"}),
            "ssid_name_transform": forms.Select(attrs={"class": "form-select"}),
            "site_tags": forms.TextInput(attrs={"class": "form-control"}),
            "device_tags": forms.TextInput(attrs={"class": "form-control"}),
            "vlan_tags": forms.TextInput(attrs={"class": "form-control"}),
            "prefix_tags": forms.TextInput(attrs={"class": "form-control"}),
            "process_unmatched_sites": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "enable_api_throttling": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "api_requests_per_second": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 10}),
            "enable_cleanup": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["meraki_base_url"].help_text = "Defaults to the standard Meraki Dashboard API endpoint."

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("clear_api_key"):
            cleaned_data["meraki_api_key"] = ""
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        api_key = (self.cleaned_data.get("meraki_api_key") or "").strip()
        if self.cleaned_data.get("clear_api_key"):
            instance.clear_meraki_api_key()
        elif api_key:
            instance.set_meraki_api_key(api_key)
        if commit:
            instance.save()
        return instance


class SiteNameRuleForm(forms.ModelForm):
    class Meta:
        model = SiteNameRule
        fields = ["name", "regex_pattern", "site_name_template", "priority", "enabled", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "regex_pattern": forms.TextInput(attrs={"class": "form-control"}),
            "site_name_template": forms.TextInput(attrs={"class": "form-control"}),
            "priority": forms.NumberInput(attrs={"class": "form-control"}),
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class PrefixFilterRuleForm(forms.ModelForm):
    class Meta:
        model = PrefixFilterRule
        fields = [
            "name",
            "filter_type",
            "prefix_pattern",
            "prefix_length_filter",
            "min_prefix_length",
            "max_prefix_length",
            "priority",
            "enabled",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "filter_type": forms.Select(attrs={"class": "form-select"}),
            "prefix_pattern": forms.TextInput(attrs={"class": "form-control"}),
            "prefix_length_filter": forms.Select(attrs={"class": "form-select"}),
            "min_prefix_length": forms.NumberInput(attrs={"class": "form-control"}),
            "max_prefix_length": forms.NumberInput(attrs={"class": "form-control"}),
            "priority": forms.NumberInput(attrs={"class": "form-control"}),
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class MerakiVLANResolutionRuleForm(forms.ModelForm, MerakiChoiceFormMixin):
    meraki_organization_id = forms.ChoiceField(
        required=False,
        choices=[("", "Any Organization")],
        widget=forms.Select(attrs={"class": "form-select no-ts", "id": "meraki_organization_id"}),
    )
    meraki_network_id = forms.ChoiceField(
        required=False,
        choices=[("", "Select an organization first")],
        widget=forms.Select(attrs={"class": "form-select no-ts", "id": "meraki_network_id"}),
    )

    def __init__(self, *args, **kwargs):
        organizations = kwargs.pop("organizations", [])
        networks = kwargs.pop("networks", [])
        super().__init__(*args, **kwargs)

        current_org = str(self.initial.get("meraki_organization_id") or getattr(self.instance, "meraki_organization_id", "") or "")
        current_network = str(self.initial.get("meraki_network_id") or getattr(self.instance, "meraki_network_id", "") or "")

        self.fields["meraki_organization_id"].choices = self._organization_choices(organizations, current_org)
        if current_org or networks:
            self.fields["meraki_network_id"].choices = self._network_choices(networks, current_network)
        elif current_network:
            self.fields["meraki_network_id"].choices = self._network_choices([], current_network)
        else:
            self.fields["meraki_network_id"].choices = [("", "Select an organization first")]

    def clean(self):
        cleaned_data = super().clean()
        organization_id = str(cleaned_data.get("meraki_organization_id") or "").strip()
        network_id = str(cleaned_data.get("meraki_network_id") or "").strip()
        if network_id and not organization_id:
            valid_network_values = {str(choice[0]) for choice in self.fields["meraki_network_id"].choices if choice[0]}
            if network_id not in valid_network_values or not getattr(self.instance, "meraki_network_id", ""):
                raise ValidationError({"meraki_network_id": "Select an organization before choosing a network."})
        return cleaned_data

    class Meta:
        model = MerakiVLANResolutionRule
        fields = [
            "name",
            "meraki_organization_id",
            "meraki_network_id",
            "site",
            "vlan_group",
            "priority",
            "enabled",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "site": forms.Select(attrs={"class": "form-select"}),
            "vlan_group": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.NumberInput(attrs={"class": "form-control"}),
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
