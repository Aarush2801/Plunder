MATRIX_GREEN = "#00ff41"
MATRIX_DARK = "#003b00"
MATRIX_DIM = "#005500"
MATRIX_BRIGHT = "#39ff14"
MATRIX_BG = "#0a0a0a"
WHITE = "#cccccc"
RED = "#ff0040"
AMBER = "#ffaa00"

CSS = f"""
Screen {{
    background: {MATRIX_BG};
    color: {MATRIX_GREEN};
    layers: rain main;
}}

MatrixRain {{
    layer: rain;
}}


#banner {{
    layer: main;
    color: {MATRIX_GREEN};
    text-align: center;
    padding: 0 1;
    height: 9;
    background: {MATRIX_BG};
}}

#transfers-label {{
    layer: main;
    color: {MATRIX_DIM};
    background: {MATRIX_BG};
    padding: 0 1;
    height: 1;
}}

#table-container {{
    layer: main;
    height: 10;
    border: solid {MATRIX_DIM};
    background: {MATRIX_BG};
}}

DataTable {{
    background: {MATRIX_BG};
    color: {MATRIX_GREEN};
}}

DataTable > .datatable--header {{
    background: {MATRIX_DARK};
    color: {MATRIX_BRIGHT};
}}

DataTable > .datatable--cursor {{
    background: {MATRIX_DARK};
    color: {MATRIX_BRIGHT};
}}

#bottom-row {{
    layer: main;
    height: 12;
}}

#piece-panel {{
    border: solid {MATRIX_DIM};
    background: {MATRIX_BG};
    color: {MATRIX_GREEN};
    width: 2fr;
    padding: 0 1;
}}

#throughput-panel {{
    border: solid {MATRIX_DIM};
    background: {MATRIX_BG};
    color: {MATRIX_GREEN};
    width: 1fr;
    padding: 0 1;
}}

#log-panel {{
    layer: main;
    border: solid {MATRIX_DIM};
    background: {MATRIX_BG};
    color: {MATRIX_GREEN};
    height: 8;
    overflow-y: auto;
    padding: 0 1;
}}

#keybinds {{
    layer: main;
    color: {MATRIX_DIM};
    background: {MATRIX_BG};
    height: 1;
    padding: 0 1;
    text-align: center;
}}

Input {{
    background: {MATRIX_DARK};
    color: {MATRIX_GREEN};
    border: solid {MATRIX_GREEN};
}}

ModalScreen {{
    align: center middle;
}}

#dialog {{
    background: {MATRIX_BG};
    border: solid {MATRIX_GREEN};
    padding: 1 2;
    width: 60;
    height: 10;
}}

#complete-banner {{
    background: {MATRIX_BG};
    border: double {MATRIX_BRIGHT};
    color: {MATRIX_BRIGHT};
    text-align: center;
    padding: 1 4;
    width: 60;
    height: 12;
}}
"""
