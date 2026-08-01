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


def add_box(name, label, x, y, z, length, width, height, color, transparency=0):
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    set_style(obj, color, transparency)
    objects.append(obj)
    return obj


def add_shape(name, label, shape, color, transparency=0):
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    set_style(obj, color, transparency)
    objects.append(obj)
    return obj


def box_shape(x, y, z, length, width, height):
    return Part.makeBox(length, width, height, FreeCAD.Vector(x, y, z))


def add_compound(name, label, specs, color, transparency=0):
    shapes = [box_shape(*spec) for spec in specs]
    return add_shape(name, label, Part.makeCompound(shapes), color, transparency)


def add_polygon_prism(name, label, points, z, height, color, transparency=0):
    vectors = [FreeCAD.Vector(x, y, z) for x, y in points]
    vectors.append(vectors[0])
    face = Part.Face(Part.makePolygon(vectors))
    return add_shape(name, label, face.extrude(FreeCAD.Vector(0, 0, height)), color, transparency)


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
    )


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
    )
    add_shape(
        f"Villa_{index}_Roof",
        f"别墅 {index} 坡屋顶",
        gable_roof_shape(x - 700, y - 700, 4200, 9400, 9600, 2300),
        (0.48, 0.54, 0.60),
        0,
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
)
add_compound(
    "Setback_Control_Lines",
    "退界控制线 8m",
    [
        (SETBACK, SETBACK, 0, PLOT_SIZE - 2 * SETBACK, 240, 80),
        (SETBACK, PLOT_SIZE - SETBACK - 240, 0, PLOT_SIZE - 2 * SETBACK, 240, 80),
        (SETBACK, SETBACK, 0, 240, PLOT_SIZE - 2 * SETBACK, 80),
        (PLOT_SIZE - SETBACK - 240, SETBACK, 0, 240, PLOT_SIZE - 2 * SETBACK, 80),
    ],
    (0.50, 0.66, 0.80),
    35,
)
add_box("North_Axis_Marker", "北向坐标轴", 92000, 74500, 0, 900, 15500, 120, (0.18, 0.29, 0.47), 0)
add_box("Elevation_Datum_Bench", "标高基准 0.000", 4600, 90000, 0, 16000, 900, 120, (0.45, 0.49, 0.56), 0)
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
add_box("Boundary_Wall_South_West", "围墙南西段", 0, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8)
add_box("Boundary_Wall_South_East", "围墙南东段", 58500, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8)
add_box("Boundary_Wall_North", "围墙北", 0, 99600, 0, 100000, 400, 3300, (0.48, 0.53, 0.59), 8)
add_box("Boundary_Wall_West", "围墙西", 0, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8)
add_box("Boundary_Wall_East", "围墙东", 99600, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8)

# Entrance, arrival court, roads, and fire access.
add_box("Main_Entrance_Gate", "主入口大门门廊", 43000, 700, 4200, 14000, 2200, 1100, (0.77, 0.79, 0.82), 0)
add_compound(
    "Main_Entrance_Gate_Columns",
    "主入口大门立柱",
    [(42800, 700, 0, 1700, 1700, 5600), (55500, 700, 0, 1700, 1700, 5600)],
    (0.84, 0.62, 0.34),
    0,
)
add_box("Guard_Booth", "门卫岗亭", 59000, 4300, 0, 4200, 3100, 3400, (0.84, 0.62, 0.34), 0)
add_box("Entrance_Dropoff_Court", "入口落客区", 41000, 23500, 0, 18000, 7600, 120, (0.35, 0.38, 0.43), 8)
add_box("Main_Road_N_S", "主入口车行道路", 45500, 500, 0, 9000, 28500, 140, (0.30, 0.35, 0.41), 4)
add_box("Fire_Road_South", "消防环路南段", 10000, 22000, 0, 80000, ROAD_WIDTH, 140, (0.30, 0.35, 0.41), 4)
add_box("Fire_Road_North", "消防环路北段", 10000, 76000, 0, 80000, ROAD_WIDTH, 140, (0.30, 0.35, 0.41), 4)
add_box("Fire_Road_West", "消防环路西段", 10000, 22000, 0, ROAD_WIDTH, 60000, 140, (0.30, 0.35, 0.41), 4)
add_box("Fire_Road_East", "消防环路东段", 84000, 22000, 0, ROAD_WIDTH, 60000, 140, (0.30, 0.35, 0.41), 4)
add_box("Pedestrian_Garden_Walk", "景观步道主轴", 49200, 31000, 0, 1800, 43000, 100, (0.54, 0.58, 0.62), 10)
add_box("Fire_Ladder_Access", "消防登高面", 17500, 52000, 0, 65500, 8200, 90, (0.42, 0.47, 0.54), 20)
add_box("Fire_Turning_Radius", "消防转弯半径示意", 45500, 25200, 0, 9000, 9000, 90, (0.42, 0.47, 0.54), 26)

# Underground parking and service access.
add_box("Underground_Garage_Outline", "地下车库轮廓", 17000, 11500, -3200, 66000, 51000, 180, (0.38, 0.44, 0.52), 58)
add_box("Basement_Ramp", "地库坡道", 69200, 5200, 0, 9000, 15500, 320, (0.34, 0.39, 0.46), 8)
add_box("Visitor_Parking_Bay", "访客停车位", 55200, 8500, 0, 11800, 4800, 110, (0.34, 0.39, 0.46), 12)

# Residential program: villas in the south garden and two towers in the north.
for villa_index, villa_x in enumerate((12000, 34000, 56000, 78000), start=1):
    add_villa(villa_index, villa_x, 33000)

add_tower(1, 18000, 61500, 66000, "高层A")
add_tower(2, 62000, 61500, 72000, "高层B")

# Clubhouse and landscape amenity core.
add_box("Clubhouse_Amenity_Body", "高档会所主体", 66500, 44500, 0, 15000, 11000, 6200, (0.84, 0.61, 0.34), 0)
add_shape(
    "Roof_Cap",
    "屋顶盖板 Roof cap",
    gable_roof_shape(65400, 43500, 6200, 17200, 13000, 2800),
    (0.50, 0.55, 0.62),
    0,
)
add_box("Clubhouse_Terrace", "会所观湖露台", 64000, 40700, 0, 20000, 3200, 160, (0.76, 0.67, 0.52), 5)
add_polygon_prism(
    "Water_Artificial_Lake",
    "人工湖水景 Water lake",
    organic_lake_points(47200, 48500, 16800, 10500),
    0,
    100,
    (0.22, 0.70, 0.92),
    46,
)
add_box("Lake_Bridge_Walk", "湖中景观桥", 41600, 48400, 80, 12500, 1700, 130, (0.48, 0.55, 0.60), 4)
add_polygon_prism(
    "Central_Green_Lawn",
    "中心绿地草坪",
    [(24500, 43700), (35500, 37000), (56000, 39200), (62000, 51600), (46200, 61200), (27000, 57000)],
    0,
    80,
    (0.50, 0.74, 0.45),
    24,
)
add_box("Children_Playground", "儿童游乐区", 73500, 27000, 0, 11000, 8800, 120, (0.94, 0.66, 0.30), 6)
add_compound(
    "Children_Play_Equipment",
    "儿童游乐设施",
    [(75500, 29200, 120, 1800, 900, 900), (79200, 30000, 120, 2200, 1100, 1200)],
    (0.88, 0.47, 0.25),
    0,
)

doc.recompute()
result = objects
