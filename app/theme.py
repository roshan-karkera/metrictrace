"""
One visual language for the whole app.

Every page imports its colours, its chart defaults and its status chips from
here, so a held series looks the same on the overview as it does on the quality
page and a reader learns the vocabulary once.

The palette is not decorative. Three rules it follows:

  1. Status colours are reserved. good, warning, serious and critical mean a
     state and are never reused as "the fourth series", so a colour on this app
     never has to be interpreted from context.
  2. Status is never carried by colour alone. Every chip and every chart legend
     entry ships an icon and a word, which is also what makes it survive a
     screenshot printed in grey.
  3. Charts are thin marks on a hairline grid. Saturated fills are for small
     marks and accents, never large blocks.

The categorical slots were checked against the white surface this app renders
on: worst adjacent pair deltaE 9.2 simulated for deuteranopia, 27.6 for normal
vision, both clear. The status four are not a categorical palette and do not
clear those gates as one, which is why nothing here asks colour to carry a
state by itself. Green and red only ever appear together on the freshness
strip, where the threshold rule separates them by position as well.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

LIGHT = {
    "surface":    "#ffffff",
    "plane":      "#f4f4f2",
    "ink":        "#0b0b0b",
    "ink_2":      "#52514e",
    "muted":      "#898781",
    "grid":       "#e1e0d9",
    "axis":       "#c3c2b7",
    "series_1":   "#2a78d6",   # blue, the default single series
    "series_2":   "#eb6834",   # orange
    "series_3":   "#1baf7a",   # aqua
    "pos":        "#2a78d6",   # diverging pole, "pushed it up"
    "neg":        "#d03b3b",   # diverging pole, "pulled it down"
}

DARK = dict(LIGHT, surface="#1a1a19", plane="#0d0d0d", ink="#ffffff",
            ink_2="#c3c2b7", grid="#2c2c2a", axis="#383835",
            series_1="#3987e5", series_2="#d95926", series_3="#199e70",
            pos="#3987e5", neg="#e66767")

# Reserved. These four mean a state, never an identity.
STATUS = {
    "good":     "#0ca30c",
    "warning":  "#fab219",
    "serious":  "#ec835a",
    "critical": "#d03b3b",
}

ICON = {"good": "OK", "warning": "!", "serious": "!!", "critical": "X"}

# The fill for "this one is fine". Saturated green on every passing cell makes a
# wall of colour in which the one failure has to compete for attention. The
# exception gets the colour; the rule gets a tint.
def quiet() -> str:
    return C()["grid"]


def mode() -> str:
    try:
        return st.context.theme.type or "light"
    except Exception:
        return "light"


def C() -> dict:
    return DARK if mode() == "dark" else LIGHT


# --------------------------------------------------------------------------
# chart defaults
# --------------------------------------------------------------------------

def chart_theme():
    c = C()
    return {
        "config": {
            "background": c["surface"],
            "view": {"stroke": "transparent", "continuousHeight": 260},
            "font": 'system-ui, -apple-system, "Segoe UI", sans-serif',
            "axis": {
                "domainColor": c["axis"], "tickColor": c["axis"],
                "gridColor": c["grid"], "gridWidth": 1, "gridDash": [],
                "labelColor": c["muted"], "titleColor": c["ink_2"],
                "labelFontSize": 11, "titleFontSize": 11,
                "titleFontWeight": "normal", "tickSize": 4, "labelPadding": 4,
            },
            "legend": {
                "labelColor": c["ink_2"], "titleColor": c["ink_2"],
                "labelFontSize": 11, "titleFontSize": 11,
                "symbolStrokeWidth": 0, "orient": "top", "direction": "horizontal",
                "titleFontWeight": "normal",
            },
            "title": {"color": c["ink"], "fontSize": 13, "fontWeight": 600,
                      "anchor": "start", "offset": 10},
            "line": {"strokeWidth": 2},
            "point": {"size": 60},
            "bar": {"cornerRadiusEnd": 4},
            "text": {"color": c["ink_2"], "fontSize": 11},
        }
    }


def install():
    """Register the theme and the page chrome. Called once per page."""
    alt.theme.register("metrictrace", enable=True)(chart_theme)
    c = C()
    st.markdown(f"""
        <style>
          .block-container {{ padding-top: 2.2rem; max-width: 1180px; }}
          div[data-testid="stMetricValue"] {{ font-size: 1.9rem; }}
          .mt-chip {{
              display: inline-block; padding: 1px 9px; margin: 0 4px 4px 0;
              border-radius: 999px; font-size: 0.78rem; font-weight: 600;
              border: 1px solid; background: transparent;
          }}
          .mt-note {{
              color: {c['ink_2']}; font-size: 0.86rem; line-height: 1.5;
              border-left: 2px solid {c['axis']}; padding: 2px 0 2px 12px;
              margin: 6px 0 14px 0;
          }}
          .mt-def {{
              background: {c['plane']}; border: 1px solid {c['grid']};
              border-radius: 8px; padding: 14px 16px; margin-bottom: 10px;
          }}
          .mt-def b {{ color: {c['ink']}; }}
          .mt-kv {{ color: {c['ink_2']}; font-size: 0.84rem; }}
        </style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# small shared pieces
# --------------------------------------------------------------------------

def chip(text: str, status: str) -> str:
    """
    status is one of the four reserved states, or "quiet" for the ordinary case.

    A chip in a status colour is a claim that something needs attention. Most
    things do not, so most chips are quiet, and the coloured ones mean what they
    say.
    """
    if status == "quiet":
        c = C()
        return (f'<span class="mt-chip" style="color:{c["ink_2"]};'
                f'border-color:{c["axis"]};font-weight:400">{text}</span>')
    col = STATUS.get(status, C()["muted"])
    return (f'<span class="mt-chip" style="color:{col};border-color:{col}">'
            f'{ICON.get(status, "")} {text}</span>')


def chips(items: list[tuple[str, str]]) -> None:
    st.markdown("".join(chip(t, s) for t, s in items), unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f'<div class="mt-note">{text}</div>', unsafe_allow_html=True)


def fmt(value, unit: str) -> str:
    """One number format for the whole app, so a figure reads the same everywhere."""
    if value is None:
        return "undefined"
    if unit == "ratio":
        return f"{value:.1%}"
    return f"{value:,.0f}"


def hairline() -> None:
    st.markdown(f'<hr style="border:none;border-top:1px solid {C()["grid"]};'
                f'margin:22px 0 14px 0">', unsafe_allow_html=True)
