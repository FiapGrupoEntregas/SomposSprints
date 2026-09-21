"""Mapa das células da fazenda (pydeck), usado pela aba Relevo (W2) e pela aba Risco (W3).

Os polígonos vêm prontos de `GET /api/v1/farms/{id}/terrain`; aqui só se desenha.
"""

import pydeck as pdk
import streamlit as st

CELL_LINE_COLOR = [255, 255, 255, 140]
DEFAULT_ZOOM = 13.5
TOOLTIP_STYLE = {"backgroundColor": "#263238", "color": "white", "fontSize": "12px"}


def hex_to_rgb(color: str) -> list[int]:
    """Converte `#RRGGBB` na lista [R, G, B] que o pydeck espera."""
    color = color.lstrip("#")
    return [int(color[index : index + 2], 16) for index in (0, 2, 4)]


MARKER_FILL = [255, 255, 255, 230]
MARKER_LINE = [38, 50, 56]


def render_cell_map(
    center: dict,
    rows: list[dict],
    tooltip_html: str,
    zoom: float = DEFAULT_ZOOM,
    marker: dict | None = None,
) -> None:
    """Desenha as células como polígonos coloridos.

    `rows` traz, por célula, `polygon`, `fill_color` e os campos citados em `tooltip_html`.
    `marker` é um ponto opcional desenhado por cima (o local do acidente, no replay).
    """
    layer = pdk.Layer(
        "PolygonLayer",
        data=rows,
        get_polygon="polygon",
        get_fill_color="fill_color",
        get_line_color=CELL_LINE_COLOR,
        line_width_min_pixels=1,
        stroked=True,
        filled=True,
        pickable=True,
        auto_highlight=True,
    )
    layers = [layer]
    if marker is not None:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=[{"position": [marker["lon"], marker["lat"]], **marker.get("fields", {})}],
                get_position="position",
                get_fill_color=MARKER_FILL,
                get_line_color=MARKER_LINE,
                line_width_min_pixels=3,
                stroked=True,
                get_radius=marker.get("radius", 40),
                radius_min_pixels=6,
                pickable=True,
            )
        )

    view_state = pdk.ViewState(latitude=center["lat"], longitude=center["lon"], zoom=zoom)
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view_state,
            tooltip={"html": tooltip_html, "style": TOOLTIP_STYLE},
            map_provider="carto",
            map_style="light",
        )
    )
