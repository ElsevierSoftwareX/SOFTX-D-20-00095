# -*- coding: utf-8 -*-

"""
This module contains functions to provide geometrical descriptions of a label image.
Each labelled region is turned to a surface. This allows mesh generators to work on
the geometry instead of an image, thereby creating good quality meshes.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import collections
from skimage.segmentation import find_boundaries
from skimage.morphology import skeletonize
from skimage.color import label2rgb
from skan import Skeleton

from .utils import toggle, index_list, non_unique


def build_skeleton(label_image, connectivity=1, detect_boundaries=True):
    """Builds skeleton connectivity of a label image.

    A single-pixel wide network is created, separating the labelled image regions. The resulting
    network contains information about how the regions are connected.

    Parameters
    ----------
    label_image : 2D ndarray with signed integer entries
        Label image, representing a segmented image.
    connectivity : {1,2}, optional
        A connectivity of 1 (default) means pixels sharing an edge will be considered neighbors.
        A connectivity of 2 means pixels sharing a corner will be considered neighbors.
    detect_boundaries : bool, optional
        When True, the image boundaries will be treated as part of the skeleton. This allows
        identifying boundary regions in the `skeleton2regions` function. The default is True.

    Returns
    -------
    skeleton_network : Skeleton
        Geometrical and topological information about the skeleton network of the input image.

    See Also
    --------
    skan.Skeleton

    """
    # 2D image, given as a numpy array is expected
    if type(label_image) != np.ndarray:
        raise Exception('Label image must be a numpy array (ndarray).')
    image_size = np.shape(label_image)
    if len(image_size) != 2:
        raise Exception('A 2D array is expected.')
    if not issubclass(label_image.dtype.type, np.signedinteger):
        raise Exception('Matrix entries must be positive integers.')

    # Surround the image with an outer region
    if detect_boundaries:
        label_image = np.pad(label_image, pad_width=1, mode='constant', constant_values=-1)

    # Find the boundaries of the label image and then extract its skeleton
    boundaries = find_boundaries(label_image, connectivity=connectivity)
    skeleton = skeletonize(boundaries)

    # Build the skeleton network using `skan`
    skeleton_network = Skeleton(skeleton, source_image=label_image, keep_images=True)
    return skeleton_network


def skeleton2regions(skeleton_network):
    """Determines the regions bounded by a skeleton network.

    This function can be perceived as an intermediate step between a skeleton network and
    completely geometrical representation of the regions. That is, it keeps the key topological
    information required to create a fully geometrical description, but it also contains
    coordinates of the region boundaries. The outputs of this function can be used to build
    different region representations.

    Parameters
    ----------
    skeleton_network : Skeleton
        Geometrical and topological information about the skeleton network of a label image.

    Returns
    -------
    region_branches : dict
        For each region it contains the branch indices that bound that region.
    branch_coordinates : list
        Coordinates of the points on each branch.
    branch_regions : dict
        For each region it contains the neighboring regions.
        This auxiliary data is not essential as it can be restored from `region_branches`.
        However, it is computed as temporary data needed for `region_branches`.

    See Also
    --------
    build_skeleton

    """
    if not isinstance(skeleton_network, Skeleton):
        raise Exception('Skeleton object is expected.')
    # Extract branch-junction connectivities and the coordinates of the junctions
    S = skeleton_network
    image_size = np.shape(S.source_image)
    endpoints_src = S.paths.indices[S.paths.indptr[:-1]]
    endpoints_dst = S.paths.indices[S.paths.indptr[1:] - 1]
    branch_junctions = np.transpose(np.vstack((endpoints_src, endpoints_dst)))
    junctions = np.unique([endpoints_src, endpoints_dst])
    junction_coordinates = S.coordinates[junctions, :]

    # Find which regions are incident to a junction
    junction_regions = {key: None for key in junctions}
    region_junctions = {}
    # TODO: Simplify the for-loop by using e.g. enumerate or
    #  https://discuss.codecademy.com/t/loop-two-variables-simultaneously-in-python-3/261808/2
    for i in range(len(junctions)):
        # Snap junction to the nearest image coordinate
        junction_coord = np.round(junction_coordinates[i, :]).astype(np.uint32)
        # Look-around for the neighboring pixels (be careful on the image boundaries)
        neighbor_idx = np.s_[
                       max(junction_coord[0] - 2, 0):min(junction_coord[0] + 3, image_size[0]),
                       max(junction_coord[1] - 2, 0):min(junction_coord[1] + 3, image_size[1])]
        neighbors = S.source_image[neighbor_idx]
        neighboring_regions = np.unique(neighbors)
        # Save junction-region and the region-junction connectivities
        # TODO: perhaps no need for the region-junction connectivities
        junction_regions[junctions[i]] = neighboring_regions
        for region in neighboring_regions:
            if region not in region_junctions:
                region_junctions[region] = [junctions[i]]
            else:
                region_junctions[region].append(junctions[i])

    # Determine which regions neighbor a branch
    branch_regions = {}
    for i, branch in enumerate(branch_junctions):
        neighboring_regions = np.intersect1d(junction_regions[branch[0]],
                                             junction_regions[branch[1]])
        branch_regions[i] = neighboring_regions

    # For each region, find the branches that bound it
    region_branches = {}
    for branch, regions in branch_regions.items():
        for region in regions:
            if region not in region_branches:
                region_branches[region] = [branch]
            else:
                region_branches[region].append(branch)

    # Return outputs
    branch_coordinates = [S.path_coordinates(i) for i in range(S.n_paths)]
    return region_branches, branch_coordinates, branch_regions


def polygon_orientation(polygon):
    """Determines whether a polygon is oriented clockwise or counterclockwise.

    Parameters
    ----------
    polygon : list
        Each element of the list denotes a vertex of the polygon and in turn is another list of two
        elements: the x and y coordinates of a vertex.

    Returns
    -------
    orientation : {'cw', 'ccw'}
        'cw': clockwise, 'ccw': counterclockwise orientation

    Notes
    -----
    The formula to determine the orientation is from https://stackoverflow.com/a/1165943/4892892.
    For simple polygons (polygons that admit a well-defined interior), a faster algorithm exits, see
    https://en.wikipedia.org/wiki/Curve_orientation#Orientation_of_a_simple_polygon.

    Examples
    --------
    >>> polygon = [[5, 0], [6, 4], [4, 5], [1, 5], [1, 0]]
    >>> polygon_orientation(polygon)
    'ccw'

    """
    n_vertex = len(polygon)
    edge_sum = 0
    for idx, vertex in enumerate(polygon):
        next_vertex = polygon[(idx + 1) % n_vertex]  # allow indexing past the last vertex
        edge_sum += (next_vertex[0] - vertex[0]) * (next_vertex[1] + vertex[1])
    if edge_sum > 0:
        orientation = 'cw'
    else:
        orientation = 'ccw'
    return orientation


def segments2polygon(segments, orientation='ccw'):
    """Interlaces connecting line segments so that they form a polygon.

    This function assumes that you already know that a set of line segments form the boundary of
    a polygon, but you want to know the ordering. As convenience, the resulting polygon is also
    determined, either in clockwise or in counterclockwise orientation. A line segment is given
    by its two end points but it can also contain intermediate points in between. The points on
    the segments are assumed to be ordered. If certain segments are not used in forming a
    polygon, they are excluded from the output lists.

    Parameters
    ----------
    segments : list
        Each element of the list gives N>=2 points on the line segment, ordered from one end
        point to the other. If N=2, the two end points are meant. The points are provided as an Nx2
        ndarray, the first column giving the x, the second column giving the y coordinates of the
        points.
    orientation : {'cw', 'ccw'}, optional
        Clockwise ('cw') or counterclockwise ('ccw') orientation of the polygon.
        The default is 'ccw'.

    Returns
    -------
    order : list
        Order of the segments so that they form a polygon.
    is_swapped : list
        A list of bool with True value if the orientation of the corresponding segment had to be
        swapped to form the polygon.
    polygon : ndarray
        The resulting polygon, given as an Mx2 ndarray, where M is the number of unique points on
        the polygon (i.e. only one end point is kept for two connecting segments).

    Examples
    --------
    >>> import numpy as np
    >>> segments = [np.array([[1, 1], [1.5, 2], [2, 3]]), np.array([[1, 1], [-1, 2]]),
    ... np.array([[1.5, -3], [2, 3]]), np.array([[1.5, -3], [-1, 2]])]
    >>> order, redirected, polygon = segments2polygon(segments, orientation='cw')
    >>> order
    [0, 2, 3, 1]
    >>> redirected
    [False, True, True, False]
    >>> polygon
    array([[ 1. ,  1. ],
           [ 1.5,  2. ],
           [ 2. ,  3. ],
           [ 1.5, -3. ],
           [-1. ,  2. ]])

    """
    # The path is the collection of consecutively added segments. When there are no more segments
    # to add, the path becomes the polygon. First, we identify the segments that form the polygon.
    n_segment = len(segments)
    redirected = [False for i in range(n_segment)]
    # Start with an arbitrary segment, the first one
    last_segment = 0  # index of the lastly added segment to the path
    order = [0]
    first_vertex = segments[last_segment][0, :]
    last_vertex = segments[last_segment][-1, :]
    # Visit all vertices of the would-be polygon and find the chain of connecting segments
    for vertex in range(n_segment - 1):
        if np.allclose(last_vertex, first_vertex):  # one complete cycle is finished
            break
        # Search for segments connecting to the last vertex of the path
        for segment_index, segment in enumerate(segments):
            if segment_index == last_segment:  # exclude the last segment of the path
                continue
            # Check if the first or second end point of the segment connects to the last vertex
            first_endpoint = segment[0, :]
            second_endpoint = segment[-1, :]
            vertex_connects_to_first_endpoint = np.allclose(first_endpoint, last_vertex)
            vertex_connects_to_second_endpoint = np.allclose(second_endpoint, last_vertex)
            if vertex_connects_to_first_endpoint or vertex_connects_to_second_endpoint:
                order.append(segment_index)
                last_segment = segment_index
            if vertex_connects_to_first_endpoint:
                last_vertex = second_endpoint
                break
            elif vertex_connects_to_second_endpoint:
                last_vertex = first_endpoint
                redirected[segment_index] = True  # change the orientation of the connecting segment
                break
            # TODO: add checks against edge cases

    # Create the polygon from the segments
    polygon = []
    for segment_index in order:
        segment = segments[segment_index].copy()
        if redirected[segment_index]:
            segment = np.flipud(segment)
        polygon.append(segment[0:-1])
    polygon = np.vstack(polygon)

    # Handle the orientation of the polygon
    if polygon_orientation(polygon) != orientation:
        order.reverse()
        redirected = toggle(redirected)
        polygon = np.flipud(polygon)

    return order, redirected, polygon


def polygonize(label_image, connectivity=1, detect_boundaries=True, orientation='ccw', close=False):
    """Polygon representation of a label image.

    Parameters
    ----------
    label_image : 2D ndarray with signed integer entries
        Label image, representing a segmented image.
    connectivity : {1,2}, optional
        A connectivity of 1 (default) means pixels sharing an edge will be considered neighbors.
        A connectivity of 2 means pixels sharing a corner will be considered neighbors.
    detect_boundaries : bool, optional
        When True, the image boundaries will be treated as part of the skeleton. This allows
        identifying boundary regions in the `skeleton2regions` function. The default is True.
    orientation : {'cw', 'ccw'}, optional
        Clockwise ('cw') or counterclockwise ('ccw') orientation of the polygons.
        The default is 'ccw'.
    close : bool, optional
        When True, one vertex in the polygons is repeated to indicate that the polygons are
        indeed closed. The default is False.

    Returns
    -------
    polygons : dict
        The keys in the dictionary correspond to the labels of the input image, while the values
        are ndarray objects with two columns, the x and y coordinates of the polygons.

    See Also
    --------
    skeleton2regions
    segments2polygon

    Examples
    --------
    >>> test_image = np.array([
    ...   [1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [1, 1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
    ...   [2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]],
    ...  dtype=np.int8)
    >>> polygons = polygonize(test_image, connectivity=1)

    """
    polygons = {}
    # Build the skeleton network from the label image
    S = build_skeleton(label_image, connectivity, detect_boundaries)
    # Identify the regions from the skeleton
    region_branches, branch_coordinates, _ = skeleton2regions(S)
    # Represent each region as a polygon
    for region, branches in region_branches.items():
        if region == -1:  # artificial outer region
            continue
        points_on_boundary = index_list(branch_coordinates, branches)
        _, _, poly = segments2polygon(points_on_boundary, orientation)
        if close:
            poly = np.vstack((poly, poly[0, :]))
        polygons[region] = poly
    # overlay_skeleton_networkx(S.graph, S.coordinates, image=label_image)
    plt.show()
    return polygons


def plot_polygon(vertices, **kwargs):
    """Plots a polygon.

    Parameters
    ----------
    vertices : ndarray
        2D ndarray of size Nx2, with each row designating a vertex and the two columns
        giving the x and y coordinates of the vertices, respectively.
    **kwargs : Line2D properties, optional
        Keyword arguments accepted by matplotlib.pyplot.plot

    Returns
    -------
    None

    See Also
    --------
    matplotlib.pyplot.plot

    Examples
    --------
    >>> plot_polygon(np.array([[1, 1], [2, 3], [1.5, -3], [-1, 2]]), marker='o');  plt.show()

    """
    # Close the polygon (repeat one vertex) if not yet closed
    first_vertex = vertices[0, :]
    last_vertex = vertices[-1, :]
    closed = np.allclose(first_vertex, last_vertex)
    if not closed:
        vertices = np.vstack((vertices, first_vertex))
    plt.plot(vertices[:, 0], vertices[:, 1], **kwargs)


def overlay_regions(label_image, polygons, axes=None):
    """Plots a label image, and overlays polygonal regions over it.

    Parameters
    ----------
    label_image : 2D ndarray with signed integer entries
        Label image, representing a segmented image.
    polygons : dict
        The keys in the dictionary correspond to the labels of the input image, while the values
        are ndarray objects with two columns, the x and y coordinates of the polygons.
        This format is respected by the output of the `polygonize` function.
    axes : matplotlib.axes.Axes, optional
        An Axes object on which to draw. If None, a new one is created.

    Returns
    -------
    axes : matplotlib.axes.Axes
        The Axes object on which the plot is drawn.

    See Also
    --------
    polygonize
    matplotlib.collections.LineCollection

    """
    # TODO: support an option to give the number of identified regions as a title
    if axes is None:
        _, axes = plt.subplots()
    # Extract the polygons from the dictionary and convert them to a list to ease the plotting.
    # At the same time, swap the x and y coordinates so that the polygons are expressed in the
    # same coordinate system as the label image.
    polygons = [polygons[i][:, ::-1] for i in polygons.keys()]
    # Plot the polygons efficiently
    axes.add_collection(collections.LineCollection(polygons, colors='black'))
    # Plot the label image, with each color corresponding to a different region
    random_colors = np.random.random((len(polygons), 3))
    plt.imshow(label2rgb(label_image, colors=random_colors))
    # Axis settings
    axes.set_aspect('equal')
    axes.set_axis_off()
    plt.tight_layout()
    return axes


if __name__ == "__main__":
    import doctest
    doctest.testmod(verbose=True)