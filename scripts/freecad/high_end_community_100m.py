import math

import FreeCAD
import Part


PLOT_SIZE = 100000
SETBACK = 8000
ROAD_WIDTH = 6000
TOWER_SPACING_MIN = 12000

doc = FreeCAD.newDocument("HighEndCommunity100mReference")
objects = []


def set_style(obj, color, transparency=0):
    try:
        obj.ViewObject.ShapeColor = color
        obj.ViewObject.LineColor = tuple(max(0.0, channel * 0.45) for channel in color)
        obj.ViewObject.Transparency = transparency
    except Exception:
        pass


def set_semantic_role(obj, role):
    if not role:
        return
    try:
        if not hasattr(obj, "SemanticRole"):
            obj.addProperty("App::PropertyString", "SemanticRole", "Planning", "Semantic role")
        obj.SemanticRole = role
    except Exception:
        pass


def add_box(name, label, x, y, z, length, width, height, color, transparency=0, role=""):
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    set_style(obj, color, transparency)
    set_semantic_role(obj, role)
    objects.append(obj)
    return obj


def add_shape(name, label, shape, color, transparency=0, role=""):
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    set_style(obj, color, transparency)
    set_semantic_role(obj, role)
    objects.append(obj)
    return obj


def box_shape(x, y, z, length, width, height):
    return Part.makeBox(length, width, height, FreeCAD.Vector(x, y, z))


def cylinder_shape(cx, cy, z, radius, height):
    return Part.makeCylinder(radius, height, FreeCAD.Vector(cx, cy, z))


def add_compound_shapes(name, label, shapes, color, transparency=0, role=""):
    return add_shape(name, label, Part.makeCompound([shape for shape in shapes if shape]), color, transparency, role)


def add_cylinder(name, label, cx, cy, z, radius, height, color, transparency=0, role=""):
    return add_shape(name, label, cylinder_shape(cx, cy, z, radius, height), color, transparency, role)


def add_compound(name, label, specs, color, transparency=0, role=""):
    shapes = [box_shape(*spec) for spec in specs]
    return add_compound_shapes(name, label, shapes, color, transparency, role)


def add_polygon_prism(name, label, points, z, height, color, transparency=0, role=""):
    vectors = [FreeCAD.Vector(x, y, z) for x, y in points]
    vectors.append(vectors[0])
    face = Part.Face(Part.makePolygon(vectors))
    return add_shape(name, label, face.extrude(FreeCAD.Vector(0, 0, height)), color, transparency, role)


def organic_lake_points(cx, cy, rx, ry, count=28):
    points = []
    for index in range(count):
        angle = math.tau * index / count
        modifier = 1.0 + 0.10 * math.sin(angle * 3.0) + 0.06 * math.cos(angle * 5.0)
        points.append((cx + math.cos(angle) * rx * modifier, cy + math.sin(angle) * ry * modifier))
    return points


def gable_roof_shape(x, y, z, length, width, height):
    vectors = [
        FreeCAD.Vector(x, y, z),
        FreeCAD.Vector(x + length, y, z),
        FreeCAD.Vector(x + length / 2, y, z + height),
        FreeCAD.Vector(x, y, z),
    ]
    return Part.Face(Part.makePolygon(vectors)).extrude(FreeCAD.Vector(0, width, 0))


def add_tower(index, x, y, height, label):
    add_box(
        f"HighRise_Tower_{index}_Body",
        f"{label} 高层住宅塔楼主体",
        x,
        y,
        0,
        13000,
        15000,
        height,
        (0.70, 0.76, 0.83),
        10,
        "residential building",
    )
    add_box(
        f"HighRise_Tower_{index}_Lobby_Podium",
        f"{label} 大堂裙楼",
        x - 1800,
        y - 1700,
        0,
        16600,
        18400,
        5200,
        (0.78, 0.68, 0.54),
        0,
        "building articulation",
    )
    add_compound(
        f"HighRise_Tower_{index}_Floor_Bands",
        f"{label} 横向层带",
        [
            (x - 250, y - 250, z, 13500, 15500, 220)
            for z in range(12000, int(height), 12000)
        ],
        (0.54, 0.60, 0.68),
        8,
        "building articulation",
    )
    add_box(
        f"HighRise_Tower_{index}_Roof_Cap",
        f"{label} 屋顶机房",
        x + 2400,
        y + 3000,
        height,
        8200,
        9000,
        2800,
        (0.60, 0.65, 0.72),
        0,
        "building articulation",
    )
    add_tower_facade_detail(index, x, y, height)


def add_villa(index, x, y):
    add_box(
        f"Villa_{index}_Body",
        f"别墅 {index} 住宅主体",
        x,
        y,
        0,
        8000,
        8200,
        4200,
        (0.76, 0.72, 0.64),
        0,
        "residential building",
    )
    add_shape(
        f"Villa_{index}_Roof",
        f"别墅 {index} 坡屋顶",
        gable_roof_shape(x - 700, y - 700, 4200, 9400, 9600, 2300),
        (0.48, 0.54, 0.60),
        0,
        "building articulation",
    )
    add_box(
        f"Private_Garden_{index}",
        f"私家庭院绿地 {index}",
        x - 1600,
        y - 1700,
        0,
        11200,
        11600,
        70,
        (0.50, 0.72, 0.45),
        35,
        "landscape open space",
    )


def add_villa_courtyard_detail():
    specs = []
    for villa_x in (12000, 34000, 56000, 78000):
        villa_y = 33000
        specs.extend(
            [
                (villa_x - 1500, villa_y - 1560, 80, 10900, 180, 520),
                (villa_x - 1500, villa_y + 9860, 80, 10900, 180, 520),
                (villa_x - 1500, villa_y - 1220, 80, 180, 10600, 520),
                (villa_x + 9220, villa_y - 1220, 80, 180, 10600, 520),
                (villa_x + 1850, villa_y - 5200, 90, 3400, 1500, 90),
            ]
        )
    add_compound(
        "Private_Courtyard_Details",
        "私家庭院矮墙与铺装",
        specs,
        (0.62, 0.68, 0.58),
        14,
        "site detail",
    )


def add_tower_facade_detail(index, x, y, height):
    facade_height = max(12000, height - 9000)
    specs = []
    for offset in (2100, 5200, 8300, 11400):
        specs.append((x - 260, y + offset, 5400, 180, 360, facade_height))
        specs.append((x + 13080, y + offset, 5400, 180, 360, facade_height))
    for offset in (2500, 6200, 9900):
        specs.append((x + offset, y - 320, 5800, 420, 180, facade_height - 1800))
        specs.append((x + offset, y + 15140, 5800, 420, 180, facade_height - 1800))
    add_compound(
        f"HighRise_Tower_{index}_Facade_Fins",
        f"高层 {index} 立面竖向格栅与阳台线",
        specs,
        (0.36, 0.43, 0.51),
        18,
        "building articulation",
    )


def add_clubhouse_detail():
    add_shape(
        "Clubhouse_Roof_Cap",
        "会所坡屋顶盖板",
        gable_roof_shape(65400, 43500, 6200, 17200, 13000, 2800),
        (0.50, 0.55, 0.62),
        0,
        "building articulation",
    )
    add_box("Clubhouse_Terrace", "会所礼仪露台", 64000, 40700, 0, 20000, 3200, 160, (0.76, 0.67, 0.52), 5, "public amenity")
    add_compound_shapes(
        "Clubhouse_Colonnade",
        "会所礼仪柱廊与廊架",
        [cylinder_shape(cx, 42100, 160, 360, 3600) for cx in (65400, 68400, 71400, 74400, 77400, 80400)]
        + [box_shape(65000 + index * 3600, 41700, 3900, 2400, 420, 260) for index in range(5)],
        (0.78, 0.68, 0.53),
        0,
        "public amenity",
    )


def add_lake_and_landscape_detail():
    add_compound(
        "Lake_Edge_Promenade",
        "人工湖岸步道与亲水平台",
        [
            (29400, 41400, 120, 11200, 1300, 120),
            (53600, 41400, 120, 9600, 1300, 120),
            (30200, 58600, 120, 12600, 1300, 120),
            (52200, 57500, 120, 10300, 1300, 120),
            (29000, 43200, 120, 1300, 11200, 120),
            (61800, 44300, 120, 1300, 9500, 120),
            (33600, 46800, 140, 5200, 2400, 120),
            (55800, 49800, 140, 4600, 2200, 120),
        ],
        (0.58, 0.62, 0.66),
        12,
        "traffic network landscape open space",
    )
    tree_shapes = []
    for cx, cy in (
        (32600, 39800),
        (36200, 37600),
        (41200, 36500),
        (45800, 38200),
        (54800, 60600),
        (58600, 57500),
        (60800, 62200),
        (63200, 54800),
        (62800, 36500),
        (67600, 35800),
        (70400, 38600),
        (72000, 42000),
    ):
        tree_shapes.append(cylinder_shape(cx, cy, 0, 950, 900))
    add_compound_shapes(
        "Landscape_Tree_Groves",
        "树阵与宅间林荫景观",
        tree_shapes,
        (0.30, 0.57, 0.34),
        12,
        "landscape open space",
    )


def add_entrance_detail():
    add_compound(
        "Entrance_Paving_Markings",
        "入口铺装标线人行路径与车道控制",
        [
            (43100, 8800, 150, 2400, 260, 70),
            (46900, 8800, 150, 2400, 260, 70),
            (50700, 8800, 150, 2400, 260, 70),
            (54500, 8800, 150, 2400, 260, 70),
            (41200, 21600, 150, 17600, 220, 70),
            (41200, 30400, 150, 17600, 220, 70),
        ],
        (0.86, 0.88, 0.84),
        0,
        "entrance system traffic network",
    )


# Plot, redline, planning controls, and datum references.
add_box(
    "Plot_Boundary_100x100m",
    "地块红线 100m x 100m",
    0,
    0,
    -150,
    PLOT_SIZE,
    PLOT_SIZE,
    150,
    (0.78, 0.88, 0.72),
    48,
    "plot boundary",
)
add_compound(
    "Setback_Control_Lines",
    "退界控制线 8m",
    [
        (SETBACK + 480, SETBACK, 0, PLOT_SIZE - 2 * SETBACK - 960, 240, 80),
        (SETBACK + 480, PLOT_SIZE - SETBACK - 240, 0, PLOT_SIZE - 2 * SETBACK - 960, 240, 80),
        (SETBACK, SETBACK + 480, 0, 240, PLOT_SIZE - 2 * SETBACK - 960, 80),
        (PLOT_SIZE - SETBACK - 240, SETBACK + 480, 0, 240, PLOT_SIZE - 2 * SETBACK - 960, 80),
    ],
    (0.50, 0.66, 0.80),
    35,
    "setback control",
)
add_box("North_Axis_Marker", "北向坐标轴", 92000, 74500, 0, 900, 15500, 120, (0.18, 0.29, 0.47), 0, "north axis")
add_box("Elevation_Datum_Bench", "标高基准 0.000", 4600, 90000, 0, 16000, 900, 120, (0.45, 0.49, 0.56), 0, "elevation datum")
metrics = add_box(
    "PlanningMetrics_Panel",
    "规划指标 FAR 1.85 建筑密度 12.8% 绿地率 34.5%",
    2500,
    87000,
    0,
    18000,
    9000,
    120,
    (0.95, 0.94, 0.86),
    8,
    "planning metrics",
)
try:
    metrics.addProperty("App::PropertyString", "FloorAreaRatio", "Planning", "FAR")
    metrics.addProperty("App::PropertyString", "BuildingDensity", "Planning", "Building density")
    metrics.addProperty("App::PropertyString", "GreenRatio", "Planning", "Green ratio")
    metrics.addProperty("App::PropertyString", "TowerSpacingMinimum", "Planning", "Tower spacing")
    metrics.FloorAreaRatio = "1.85"
    metrics.BuildingDensity = "0.128"
    metrics.GreenRatio = "0.345"
    metrics.TowerSpacingMinimum = str(TOWER_SPACING_MIN)
except Exception:
    pass

# Perimeter wall with a controlled south entrance opening.
add_box("Boundary_Wall_South_West", "围墙南西段", 0, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8, "boundary wall")
add_box("Boundary_Wall_South_East", "围墙南东段", 58500, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8, "boundary wall")
add_box("Boundary_Wall_North", "围墙北", 0, 99600, 0, 100000, 400, 3300, (0.48, 0.53, 0.59), 8, "boundary wall")
add_box("Boundary_Wall_West", "围墙西", 0, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8, "boundary wall")
add_box("Boundary_Wall_East", "围墙东", 99600, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8, "boundary wall")

# Entrance, arrival court, roads, and fire access.
add_box("Main_Entrance_Gate", "主入口大门门廊", 43000, 700, 4200, 14000, 2200, 1100, (0.77, 0.79, 0.82), 0, "entrance system")
add_compound(
    "Main_Entrance_Gate_Columns",
    "主入口大门立柱",
    [(42800, 700, 0, 1700, 1700, 5600), (55500, 700, 0, 1700, 1700, 5600)],
    (0.84, 0.62, 0.34),
    0,
    "entrance system",
)
add_box("Guard_Booth", "门卫岗亭", 59000, 4300, 0, 4200, 3100, 3400, (0.84, 0.62, 0.34), 0, "entrance system")
add_box("Entrance_Dropoff_Court", "入口落客区", 41000, 23500, 0, 18000, 7600, 120, (0.35, 0.38, 0.43), 8, "entrance system traffic network")
add_entrance_detail()
add_box("Main_Road_N_S", "主入口车行道路", 45500, 500, 0, 9000, 28500, 140, (0.30, 0.35, 0.41), 4, "traffic network")
add_box("Fire_Road_South", "消防环路南段", 10000, 22000, 0, 80000, ROAD_WIDTH, 140, (0.30, 0.35, 0.41), 4, "fire access traffic network")
add_box("Fire_Road_North", "消防环路北段", 10000, 76000, 0, 80000, ROAD_WIDTH, 140, (0.30, 0.35, 0.41), 4, "fire access traffic network")
add_box("Fire_Road_West", "消防环路西段", 10000, 22000, 0, ROAD_WIDTH, 60000, 140, (0.30, 0.35, 0.41), 4, "fire access traffic network")
add_box("Fire_Road_East", "消防环路东段", 84000, 22000, 0, ROAD_WIDTH, 60000, 140, (0.30, 0.35, 0.41), 4, "fire access traffic network")
add_box("Pedestrian_Main_Spine", "人行主轴步道", 49200, 31000, 0, 1800, 43000, 100, (0.54, 0.58, 0.62), 10, "traffic network")
add_box("Fire_Ladder_Access", "消防登高面", 17500, 52000, 0, 65500, 8200, 90, (0.42, 0.47, 0.54), 20, "fire access")
add_cylinder("Fire_Turning_Radius", "消防转弯半径示意", 50000, 29700, 0, 4500, 90, (0.42, 0.47, 0.54), 26, "fire access")

# Underground parking and service access.
add_box("Underground_Garage_Outline", "地下车库轮廓", 17000, 11500, -3200, 66000, 51000, 180, (0.38, 0.44, 0.52), 58, "parking underground")
add_box("Basement_Ramp", "地库坡道", 69200, 5200, 0, 9000, 15500, 320, (0.34, 0.39, 0.46), 8, "parking underground vertical design")
add_box("Visitor_Parking_Bay", "访客停车位", 55200, 8500, 0, 11800, 4800, 110, (0.34, 0.39, 0.46), 12, "parking underground")

# Residential program: villas in the south garden and two towers in the north.
for villa_index, villa_x in enumerate((12000, 34000, 56000, 78000), start=1):
    add_villa(villa_index, villa_x, 33000)
add_villa_courtyard_detail()

add_tower(1, 18000, 61500, 66000, "高层A")
add_tower(2, 62000, 61500, 72000, "高层B")

# Clubhouse and landscape amenity core.
add_box("Clubhouse_Amenity_Body", "高档会所主体", 66500, 44500, 0, 15000, 11000, 6200, (0.84, 0.61, 0.34), 0, "public amenity")
add_clubhouse_detail()
add_polygon_prism(
    "Water_Artificial_Lake",
    "人工湖水景 Water lake",
    organic_lake_points(47200, 48500, 16800, 10500),
    0,
    100,
    (0.22, 0.70, 0.92),
    46,
    "landscape open space",
)
add_box("Lake_Bridge_Walk", "湖中景观桥", 41600, 48400, 80, 12500, 1700, 130, (0.48, 0.55, 0.60), 4, "traffic network")
add_lake_and_landscape_detail()
add_polygon_prism(
    "Central_Green_Lawn",
    "中心绿地草坪",
    [(24500, 43700), (35500, 37000), (56000, 39200), (62000, 51600), (46200, 61200), (27000, 57000)],
    0,
    80,
    (0.50, 0.74, 0.45),
    24,
    "landscape open space",
)
add_box("Children_Playground", "儿童游乐区", 73500, 27000, 0, 11000, 8800, 120, (0.94, 0.66, 0.30), 6, "landscape open space")
add_compound(
    "Children_Play_Equipment",
    "儿童游乐设施",
    [(75500, 29200, 120, 1800, 900, 900), (79200, 30000, 120, 2200, 1100, 1200)],
    (0.88, 0.47, 0.25),
    0,
    "landscape open space",
)

doc.recompute()
result = objects
