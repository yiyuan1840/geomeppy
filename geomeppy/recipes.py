# Copyright (c) 2016 Jamie Bull
# =======================================================================
#  Distributed under the MIT License.
#  (See accompanying file LICENSE or copy at
#  http://opensource.org/licenses/MIT)
# =======================================================================
"""Recipes for making changes to EnergyPlus IDF files."""
import itertools
from typing import List, Tuple, Union  # noqa

from eppy.idf_msequence import Idf_MSequence  # noqa
import numpy as np

from .geom.intersect_match import getidfsurfaces
from .geom.polygons import Polygon3D
from .geom.transformations import Transformation
from .geom.vectors import Vector2D, Vector3D  # noqa

MYPY = False
if MYPY:
    from .eppy_patches import EpBunch, IDF  # noqa


def set_default_constructions(idf):
    # type: (IDF) -> None
    """Set default constructions for surfaces in the model.

    :param idf: The IDF object.

    """
    constructions = ['Project Wall', 'Project Partition', 'Project Floor',
                     'Project Flat Roof', 'Project Ceiling',
                     'Project External Window', 'Project Door']
    for construction in constructions:
        idf.newidfobject('CONSTRUCTION', construction,
                         Outside_Layer='DefaultMaterial')
    idf.newidfobject('MATERIAL', 'DefaultMaterial',
                     Roughness='Rough',
                     Thickness=0.1,
                     Conductivity=0.1,
                     Density=1000,
                     Specific_Heat=1000,
                     )

    for surface in idf.getsurfaces():
        set_default_construction(surface)
    for subsurface in idf.getsubsurfaces():
        set_default_construction(subsurface)


def set_default_construction(surface):
    # type: (EpBunch) -> None
    """Set default construction for a surface in the model.

    :param surface: A model surface.

    """
    if surface.Surface_Type.lower() == 'wall':
        if surface.Outside_Boundary_Condition.lower() == 'outdoors':
            surface.Construction_Name = 'Project Wall'
        elif surface.Outside_Boundary_Condition.lower() == 'ground':
            surface.Construction_Name = 'Project Wall'
        else:
            surface.Construction_Name = 'Project Partition'
    if surface.Surface_Type.lower() == 'floor':
        if surface.Outside_Boundary_Condition.lower() == 'ground':
            surface.Construction_Name = 'Project Floor'
        else:
            surface.Construction_Name = 'Project Floor'
    if surface.Surface_Type.lower() == 'roof':
        surface.Construction_Name = 'Project Flat Roof'
    if surface.Surface_Type.lower() == 'ceiling':
        surface.Construction_Name = 'Project Ceiling'
    if surface.Surface_Type == 'window':
        surface.Construction_Name = 'Project External Window'
    if surface.Surface_Type == 'door':
        surface.Construction_Name = 'Project Door'


def set_wwr(idf, wwr=0.2, construction=None, force=False):
    # type: (IDF, Optional[float], Optional[str], Optional[bool]) -> None
    """Set the window to wall ratio on all external walls.

    :param idf: The IDF to edit.
    :param wwr: The window to wall ratio.
    :param construction: Name of a window construction.
    :param force: True to remove all subsurfaces before setting the WWR.

    """
    try:
        ggr = idf.idfobjects['GLOBALGEOMETRYRULES'][0]
    except IndexError:
        ggr = []
    external_walls = [
        s for s in idf.idfobjects['BUILDINGSURFACE:DETAILED']
        if s.Surface_Type.lower() == 'wall' and s.Outside_Boundary_Condition.lower() == 'outdoors'
    ]
    subsurfaces = [idf.idfobjects[key.upper()] for key in idf.idd_index['ref2names']['SubSurfNames']]
    for wall in external_walls:
        # get any subsurfaces on the wall
        wall_subsurfaces = [
            ss for ss in itertools.chain(*subsurfaces)
            if ss.Building_Surface_Name == wall.Name
        ]
        if not all(_is_window(wss) for wss in wall_subsurfaces) and not force:
            raise ValueError(
                'Not all subsurfaces on wall "{name}" are windows. '
                'Use `force=True` to replace all subsurfaces.'.format(name=wall.Name))

        if wall_subsurfaces and not construction:
            constructions = list({wss.Construction_Name for wss in wall_subsurfaces if _is_window(wss)})
            if len(constructions) > 1:
                raise ValueError(
                    'Not all subsurfaces on wall "{name}" have the same construction'.format(name=wall.Name))
            construction = constructions[0]
        # remove all subsurfaces
        for ss in wall_subsurfaces:
            idf.removeidfobject(ss)
        coords = window_vertices_given_wall(wall, wwr)
        window = idf.newidfobject(
            'FENESTRATIONSURFACE:DETAILED',
            Name="%s window" % wall.Name,
            Surface_Type='Window',
            Construction_Name=construction,
            Building_Surface_Name=wall.Name,
            View_Factor_to_Ground='autocalculate',  # from the surface angle
        )
        window.setcoords(coords, ggr)


def _is_window(subsurface):
    if subsurface.key.lower() in {'window', 'fenestrationsurface:detailed'}:
        return True


def window_vertices_given_wall(wall, wwr):
    # type: (EpBunch, float) -> Polygon3D
    """Calculate window vertices given wall vertices and glazing ratio.

    For each axis:
    1) Translate the axis points so that they are centred around zero
    2) Either:
        a) Multiply the z dimension by the glazing ratio to shrink it vertically
        b) Multiply the x or y dimension by 0.995 to keep inside the surface
    3) Translate the axis points back to their original positions

    :param wall: The wall to add a window on. We expect each wall to have four vertices.
    :param wwr: Window to wall ratio.
    :returns: Window vertices bounding a vertical strip midway up the surface.

    """
    vertices = wall.coords
    average_x = sum([x for x, _y, _z in vertices]) / len(vertices)
    average_y = sum([y for _x, y, _z in vertices]) / len(vertices)
    average_z = sum([z for _x, _y, z in vertices]) / len(vertices)
    # move windows in 0.5% from the edges so they can be drawn in SketchUp
    window_points = [[
        ((x - average_x) * 0.999) + average_x,
        ((y - average_y) * 0.999) + average_y,
        ((z - average_z) * wwr) + average_z
    ]
        for x, y, z in vertices]

    return Polygon3D(window_points)


def translate_to_origin(idf):
    # type: (IDF) -> None
    """Move an IDF close to the origin so that it can be viewed in SketchUp.

    :param idf: The IDF to edit.

    """
    surfaces = getidfsurfaces(idf)
    windows = idf.idfobjects['FENESTRATIONSURFACE:DETAILED']

    min_x = min(min(Polygon3D(s.coords).xs) for s in surfaces)
    min_y = min(min(Polygon3D(s.coords).ys) for s in surfaces)

    translate(surfaces, (-min_x, -min_y))
    translate(windows, (-min_x, -min_y))


def translate(surfaces,  # type: Idf_MSequence
              vector  # type: Union[Tuple[float, float], Vector2D, Vector3D]
              ):
    # type: (...) -> None
    """Translate all surfaces by a vector.

    :param surfaces: A list of EpBunch objects.
    :param vector: Representation of a vector to translate by.

    """
    vector = Vector3D(*vector)
    for s in surfaces:
        new_coords = translate_coords(s.coords, vector)
        s.setcoords(new_coords)


def translate_coords(coords,  # type: Union[List[Tuple[float, float, float]], Polygon3D]
                     vector  # type: Union[List[float], Vector3D]
                     ):
    # type: (...) -> List[Vector3D]
    """Translate a set of coords by a direction vector.

    :param coords: A list of points.
    :param vector: Representation of a vector to translate by.
    :returns: List of translated vectors.
    """
    return [Vector3D(*v) + vector for v in coords]


def scale(surfaces, factor, axes='xy'):
    # type: (Idf_MSequence, float, Optional[str]) -> None
    """Scale all surfaces by a factor.

    :param surfaces: A list of EpBunch objects.
    :param factor: Factor to scale the surfaces by.
    :param axes: Axes to scale on. Default 'xy'.
    """
    for s in surfaces:
        new_coords = scale_coords(s.coords, factor, axes)
        s.setcoords(new_coords)


def scale_coords(coords, factor, axes):
    # type: (Union[List[Tuple[float, float, float], Polygon3D]], int, str) -> Polygon3D
    """Scale a set of coords by a factor.

    :param coords: A list of points.
    :param factor: Factor to scale the surfaces by.
    :param axes: Axes to scale on.
    :returns: A scaled polygon.
    """
    coords = Polygon3D(coords)
    vertices = []
    for coord in coords:
        x = coord[0] * factor if 'x' in axes else coord[0]
        y = coord[1] * factor if 'y' in axes else coord[1]
        z = coord[2] * factor if 'z' in axes else coord[2]
        vertices.append(Vector3D(x, y, z))
    return Polygon3D(vertices)


def rotate(surfaces, angle):
    # type: (Union[List[EpBunch], Idf_MSequence], Union[int, float]) -> None
    """Rotate all surfaces by an angle.

    :param surfaces: A list of EpBunch objects or a mutable sequence.
    :param angle : An angle in degrees.
    """
    radians = np.deg2rad(angle)
    for s in surfaces:
        new_coords = rotate_coords(s.coords, radians)
        s.setcoords(new_coords)


def rotate_coords(coords, radians):
    # type: (List[Tuple[float, float, float]], float64) -> List[Tuple[float, float, float]]
    """Rotate a set of coords by an angle in radians.

    :param coords: A list of points.
    :param radians: The angle to rotate by.
    :returns: List of Vector3D objects.
    """
    coords = Polygon3D(coords)
    rotation = Transformation()._rotation(Vector3D(0, 0, 1), radians)
    coords = rotation * coords
    return coords
