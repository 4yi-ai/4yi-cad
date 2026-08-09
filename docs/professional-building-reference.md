# Professional building generation reference

This document is the acceptance baseline for the single-building generation path.
It deliberately separates BIM information quality from viewport presentation.

## Product target

- Underspecified requests produce a measurable LOD 200 concept model.
- LOD 300 is used only when dimensions and program detail are sufficient.
- Supported first-wave typologies are residential tower, office tower, and villa.
- The AI produces a validated `4yi-cad.building/v1` specification; deterministic
  builders own geometry creation.

## Required model hierarchy

`Project > Site > Building > BuildingStorey > Element/Space`

Every primary element must declare a storey and semantic type. The minimum
element set is slab, exterior wall, window, entrance door, core, stair, and roof.
LOD 300 cases additionally require representative spaces and accurate openings.

## Geometry acceptance

- zero invalid leaf shapes;
- zero OCC check errors;
- no container Shape is counted again when its child Shapes are already counted;
- storey elevations are monotonic and match the requested count and heights;
- windows and doors remain within their host facade bounds;
- FCStd can reopen and recompute; IFC can export and re-import when available.

## Presentation acceptance

- default axonometric view, perspective where supported, shaded with edges;
- selection cleared before fit-all;
- at least three facade depth layers: primary wall, recessed glazing, and a
  projecting frame, balcony, canopy, or shading element;
- semantic material treatment for walls, concrete, glazing, metal, and roof;
- entrance and roof termination are recognizable in front and axonometric views.

## Baseline defect captured on 2026-08-09

The prompt `生成一栋楼房` produced 61 `Part::Box` primitives plus four containers.
The tower body was a single 30,000 x 18,000 x 57,600 mm box with facade fins,
floor bands, balconies, and roof boxes. It had no wall, slab, window, door,
storey, space, core, or stair semantics. Aggregate container Shapes inflated the
reported solids/faces/edges, and the document reported OCC check failures.

This artifact is a massing baseline, not an acceptable professional building
delivery.
