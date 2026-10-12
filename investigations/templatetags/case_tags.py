from django import template

register = template.Library()


@register.filter
def humanize_key(value):
    return str(value).replace("_", " ").title()
