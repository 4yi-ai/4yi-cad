"""Prompt-side planning helpers for site/community FreeCAD layouts."""

from __future__ import annotations

import re


SITE_LAYOUT_PROMPT_RE = re.compile(
    r"\b(site|community|campus|neighbou?rhood|master\s*plan|residential\s*layout|"
    r"high[-\s]*end\s*community|villa|clubhouse|playground|artificial\s*lake)\b|"
    r"小区|社区|园区|地块|场地|总图|别墅|高层|会所|儿童|游乐|人工湖|景观|消防|地库"
)


SITE_LAYOUT_PLANNER_MESSAGE = """Site-layout component plan for this FreeCAD request:
- Use a reusable master-plan structure before detailed massing: plot/redline,
  setback controls, north axis, elevation datum, planning metrics, perimeter
  wall, entrance gate/guard/drop-off, connected roads/pedestrian paths, fire
  loop/ladder access/turning marker, underground garage/ramp/parking, villas,
  high-rise towers, clubhouse, artificial lake, children playground, and green
  landscape.
- For a 100 m x 100 m plot, model dimensions in millimetres. Keep ordinary
  components within the redline and on the site datum. Maintain tower spacing
  around 12000 mm or more.
- Use template-like helper functions in the generated FreeCAD script for:
  add_plot_controls, add_perimeter_wall, add_entrance_sequence, add_road_fire_loop,
  add_parking_basement, add_villa_cluster, add_highrise_towers, add_clubhouse,
  add_artificial_lake, add_children_playground, and add_planning_metrics.
- Use role-rich names and labels so the site-layout audit can validate the result:
  Plot/Redline, Setback, NorthAxis, ElevationDatum, BoundaryWall, Entrance/Gate,
  Road/Path, Fire, Parking/Garage/Basement/Ramp, Villa/ResidentialTower,
  Clubhouse/Amenity, Water/Lake, Playground/Green, PlanningMetrics/FAR.
- The first pass must target document_summary.site_layout.status == "pass";
  do not return a merely decorative or partial model."""


def is_site_layout_prompt(prompt: str) -> bool:
    return bool(SITE_LAYOUT_PROMPT_RE.search(prompt or ""))


def augment_prompt_with_site_layout_plan(prompt: str) -> str:
    if not is_site_layout_prompt(prompt):
        return prompt
    return f"{SITE_LAYOUT_PLANNER_MESSAGE}\n\nUser request:\n{prompt}"
