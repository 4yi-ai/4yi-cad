"""Offscreen preview rendering.

Runs INSIDE the sandbox subprocess (never the main event loop). Renders a mesh
to a PNG using PyVista offscreen (VTK). Requires xvfb/mesa in the image; preview
is best-effort — if rendering fails the worker still returns STEP/STL.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def render_png(mesh_path: str, size: tuple[int, int] = (640, 480)) -> bytes:
    import pyvista as pv

    # A display is provided by the `xvfb-run` wrapper (see worker.render_preview_isolated),
    # so mesa can do software OpenGL rendering headless.
    pv.OFF_SCREEN = True

    mesh = pv.read(mesh_path)
    plotter = pv.Plotter(off_screen=True, window_size=list(size))
    plotter.add_mesh(mesh, color="#b0c4de", show_edges=True, edge_color="#33415c")
    plotter.view_isometric()
    plotter.background_color = "white"

    out = Path(tempfile.mkdtemp()) / "preview.png"
    plotter.screenshot(str(out))
    plotter.close()
    return out.read_bytes()


if __name__ == "__main__":
    # Invoked (under xvfb-run) as: python -m app.cad.preview <mesh_path>
    # Writes base64-encoded PNG to stdout; a non-zero exit / crash => no preview.
    import base64
    import sys

    png = render_png(sys.argv[1])
    sys.stdout.write(base64.b64encode(png).decode("ascii"))
