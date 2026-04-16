# Template tags for NetBox Meraki plugin
import json

from django import template

register = template.Library()


@register.filter
def lookup(dictionary, key):
    """
    Template filter to get a value from a dictionary by key
    Usage: {{ dict|lookup:key }}
    """
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter
def json_pretty(value):
    """
    Render Python objects as pretty JSON for readable review/debug displays.
    """
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except TypeError:
        return str(value)
