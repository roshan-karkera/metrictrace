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
            "padding": {"left": 14, "right": 16, "top": 12, "bottom": 10},
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


# The page behind the cards. Every chart is validated against the white (or
# near black) surface, so the surface stays exactly as it was and only sits on
# a tinted page as a card. Tinting the page and leaving the marks on their
# checked surface is what lets the background have some colour at all.
PAGE = {
    "light": {
        "page":   "linear-gradient(165deg, #e9f0fa 0%, #f3f5f8 40%, #f5f4f0 100%)",
        "glow":   "rgba(42, 120, 214, 0.13)",
        "side":   "linear-gradient(180deg, #ffffff 0%, #f1f5fb 100%)",
        "shadow": "0 1px 2px rgba(16, 24, 40, 0.06), 0 6px 16px rgba(16, 24, 40, 0.05)",
        "lift":   "0 2px 4px rgba(16, 24, 40, 0.08), 0 12px 24px rgba(16, 24, 40, 0.09)",
    },
    "dark": {
        "page":   "linear-gradient(165deg, #0a1120 0%, #0d0d0d 45%, #0d0d0d 100%)",
        "glow":   "rgba(57, 135, 229, 0.16)",
        "side":   "linear-gradient(180deg, #131313 0%, #0d0d0d 100%)",
        "shadow": "0 1px 2px rgba(0, 0, 0, 0.55), 0 6px 16px rgba(0, 0, 0, 0.35)",
        "lift":   "0 2px 4px rgba(0, 0, 0, 0.6), 0 12px 24px rgba(0, 0, 0, 0.5)",
    },
}

_CSS = """
<style>
  /* page background: a tint with a soft glow, never a flat slab */
  .stApp {
      background: radial-gradient(1100px 480px at 8% -8%, __GLOW__, transparent 62%),
                  __PAGE__;
      background-attachment: fixed;
  }
  [data-testid="stHeader"] { background: transparent; }
  [data-testid="stSidebar"] { background: __SIDE__; }
  .block-container { padding-top: 2.2rem; max-width: 1180px; }

  /* headings */
  .block-container h1 { font-weight: 700; letter-spacing: -0.02em; }
  .block-container h1::after {
      content: ""; display: block; width: 52px; height: 4px; border-radius: 4px;
      margin-top: 10px;
      background: linear-gradient(90deg, __S1__, __S3__);
  }
  .block-container h2, .block-container h3 { letter-spacing: -0.01em; }

  /* cards: anything that carries a number, a chart, or a fold */
  [data-testid="stMetric"], [data-testid="stExpander"],
  [data-testid="stVegaLiteChart"], [data-testid="stDataFrame"] {
      background: __SURFACE__; border: 1px solid __GRID__; border-radius: 12px;
      box-shadow: __SHADOW__;
  }
  [data-testid="stMetric"] {
      padding: 14px 16px; transition: transform .15s ease, box-shadow .15s ease;
  }
  [data-testid="stMetric"]:hover { transform: translateY(-2px); box-shadow: __LIFT__; }
  [data-testid="stMetricValue"] { font-size: 1.9rem; font-weight: 700; }
  [data-testid="stMetricLabel"] { color: __INK2__; }
  [data-testid="stVegaLiteChart"], [data-testid="stDataFrame"] {
      overflow: hidden; box-sizing: border-box;
  }
  [data-testid="stExpander"] details { border: none; }
  [data-testid="stAlert"] { border-radius: 12px; }

  /* controls */
  div[data-baseweb="select"] > div, div[data-baseweb="input"],
  div[data-baseweb="base-input"] { background: __SURFACE__; border-radius: 10px; }
  .stButton > button {
      border-radius: 10px; transition: transform .12s ease, box-shadow .12s ease,
                                        border-color .12s ease;
  }
  .stButton > button:hover {
      transform: translateY(-1px); box-shadow: __SHADOW__; border-color: __S1__;
  }

  /* the app's own pieces */
  .mt-chip {
      display: inline-block; padding: 1px 9px; margin: 0 4px 4px 0;
      border-radius: 999px; font-size: 0.78rem; font-weight: 600;
      border: 1px solid; background: transparent;
  }
  .mt-note {
      color: __INK2__; font-size: 0.86rem; line-height: 1.5;
      border-left: 2px solid __AXIS__; padding: 2px 0 2px 12px; margin: 6px 0 14px 0;
  }
  .mt-def {
      background: __SURFACE__; border: 1px solid __GRID__;
      border-left: 3px solid __S1__; border-radius: 12px;
      padding: 14px 16px; margin-bottom: 10px; box-shadow: __SHADOW__;
  }
  .mt-def b { color: __INK__; }
  .mt-kv { color: __INK2__; font-size: 0.84rem; }
  .mt-hero {
      background: __SURFACE__; border: 1px solid __GRID__; border-radius: 16px;
      padding: 20px 24px; margin: 4px 0 18px 0; box-shadow: __SHADOW__;
      background-image: radial-gradient(520px 160px at 100% 0%, __GLOW__, transparent 70%);
  }
  .mt-hero p { margin: 0 0 12px 0; color: __INK__; font-size: 1.02rem; line-height: 1.55; }
  .mt-pill {
      display: inline-block; margin: 0 8px 4px 0; padding: 4px 12px; font-size: 0.82rem;
      font-weight: 600; color: __INK__; border: 1px solid __GRID__; border-radius: 999px;
      background: __PLANE__;
  }
  .mt-pill::before {
      content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
      background: __S1__; margin-right: 8px; vertical-align: 1px;
  }

  /* small motion, and none for anyone who asked for less */
  @keyframes mt-in { from { opacity: 0; transform: translateY(6px); }
                     to   { opacity: 1; transform: none; } }
  .block-container { animation: mt-in .3s ease-out; }
  @media (prefers-reduced-motion: reduce) {
      .block-container, [data-testid="stMetric"], .stButton > button { animation: none; transition: none; }
  }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: __AXIS__; border-radius: 8px; }
</style>
"""


def install():
    """Register the theme and the page chrome. Called once per page."""
    alt.theme.register("metrictrace", enable=True)(chart_theme)
    c, p = C(), PAGE[mode()]
    tokens = {
        "PAGE": p["page"], "GLOW": p["glow"], "SIDE": p["side"],
        "SHADOW": p["shadow"], "LIFT": p["lift"],
        "SURFACE": c["surface"], "PLANE": c["plane"], "GRID": c["grid"],
        "AXIS": c["axis"], "INK": c["ink"], "INK2": c["ink_2"],
        "S1": c["series_1"], "S3": c["series_3"],
    }
    css = _CSS
    for key, value in tokens.items():
        css = css.replace(f"__{key}__", value)
    st.markdown(css, unsafe_allow_html=True)


def hero(text: str, pills: list[str]) -> None:
    """A single lead card for a landing page: one sentence and its named parts."""
    body = "".join(f'<span class="mt-pill">{p}</span>' for p in pills)
    st.markdown(f'<div class="mt-hero"><p>{text}</p>{body}</div>',
                unsafe_allow_html=True)


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
