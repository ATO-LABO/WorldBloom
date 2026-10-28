"""Shared UI building blocks. Content slots accept trusted renderer HTML only."""
import html
import re


def _e(value):
    return html.escape(str(value), quote=True)


def attributes(values):
    parts = []
    for key, value in (values or {}).items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", key) or key.startswith("on"):
            raise ValueError("Invalid UI attribute")
        if value is False or value is None:
            continue
        parts.append(key if value is True else f'{key}="{_e(value)}"')
    return (" " + " ".join(parts)) if parts else ""


def heading(title, description="", *, class_name="", title_extra="", actions=""):
    description_html = f'<p>{_e(description)}</p>' if description else ""
    return (f'<header class="ui-heading {_e(class_name)}"><div><h1>{_e(title)}{title_extra}</h1>'
            f'{description_html}</div>{actions}</header>')


def button(label, *, kind="secondary", href=None, attrs=None, class_name=""):
    if kind not in ("secondary", "advance", "confirm"):
        raise ValueError("Unknown UI button kind")
    values = dict(attrs or {})
    if "class" in values or "href" in values:
        raise ValueError("Use the named class_name/href arguments")
    values["class"] = f"ui-button ui-button--{kind} {class_name}".strip()
    if href is not None:
        # These controls navigate within the app, never execute URL schemes.
        if not isinstance(href, str) or not href.startswith("/") or href.startswith("//"):
            raise ValueError("UI button links must be local app paths")
        return f'<a{attributes({**values, "href":href})}>{_e(label)}</a>'
    values.setdefault("type", "button")
    return f'<button{attributes(values)}>{_e(label)}</button>'


def footer(content, *, class_name=""):
    return f'<footer class="ui-footer {_e(class_name)}">{content}</footer>'


def navigation(content, *, label, class_name=""):
    return f'<nav class="ui-nav {_e(class_name)}" aria-label="{_e(label)}">{content}</nav>'


def field(label, control, *, required=False, class_name=""):
    badge = '<small class="ui-required">必須</small>' if required else ""
    return f'<label class="ui-field {_e(class_name)}"><span>{_e(label)}{badge}</span>{control}</label>'
