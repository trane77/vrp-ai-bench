"""Stable move descriptors shared by classical search and label collection."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Move:
    operator: str
    a: int
    i: int
    b: int
    j: int

    def apply(self, routes):
        result = [list(r) for r in routes]
        if self.operator == 'relocate':
            stop = result[self.a].pop(self.i)
            index = self.j - int(self.a == self.b and self.j > self.i)
            result[self.b].insert(index, stop)
        elif self.operator == 'swap':
            result[self.a][self.i], result[self.b][self.j] = result[self.b][self.j], result[self.a][self.i]
        elif self.operator == 'reverse':
            result[self.a][self.i:self.j] = reversed(result[self.a][self.i:self.j])
        else:
            raise ValueError('unknown move operator')
        return result


def compute_route_geometry(routes, locations, depot_coord):
    """Compute centroid (lon, lat) and bounding box (min_lon, min_lat, max_lon, max_lat) for each route."""
    centroids = []
    bboxes = []
    depot_tuple = tuple(depot_coord)
    for route in routes:
        if not route:
            centroids.append(depot_tuple)
            bboxes.append((depot_coord[0], depot_coord[1], depot_coord[0], depot_coord[1]))
            continue
        coords = [locations[stop]['snapped'] for stop in route if stop in locations and 'snapped' in locations[stop]]
        if not coords:
            centroids.append(depot_tuple)
            bboxes.append((depot_coord[0], depot_coord[1], depot_coord[0], depot_coord[1]))
            continue
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        centroids.append((sum(lons) / len(lons), sum(lats) / len(lats)))
        bboxes.append((min(lons), min(lats), max(lons), max(lats)))
    return centroids, bboxes


def get_spatial_neighbor_routes(routes, locations, depot_coord, max_neighbors=4):
    """Find the k closest neighbor routes for each vehicle based on centroid Euclidean distance."""
    centroids, _ = compute_route_geometry(routes, locations, depot_coord)
    num_routes = len(routes)
    neighbors = {idx: set() for idx in range(num_routes)}
    if num_routes <= 1 or max_neighbors >= num_routes - 1:
        for i in range(num_routes):
            neighbors[i] = {j for j in range(num_routes) if j != i}
        return neighbors

    for a in range(num_routes):
        distances = []
        ca = centroids[a]
        for b in range(num_routes):
            if a == b:
                continue
            cb = centroids[b]
            d = (ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2
            distances.append((d, b))
        distances.sort(key=lambda item: item[0])
        k_closest = {item[1] for item in distances[:max_neighbors]}
        neighbors[a].update(k_closest)
        for b in k_closest:
            neighbors[b].add(a)
    return neighbors


def enumerate_moves(routes, spatial_neighbors=None):
    """Enumerate relocate, swap, and reverse moves.

    If spatial_neighbors dict is provided, cross-vehicle relocates and swaps
    are pruned to vehicle pairs that are spatial neighbors. Intra-vehicle moves
    are always preserved.
    """
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for b, dest in enumerate(routes):
                if a != b and spatial_neighbors is not None and b not in spatial_neighbors.get(a, ()):
                    continue
                for j in range(len(dest) + 1):
                    if a == b and j in (i, i + 1):
                        continue
                    yield Move('relocate', a, i, b, j)
    positions = [(a, i) for a, route in enumerate(routes) for i in range(len(route))]
    for x, (a, i) in enumerate(positions):
        for b, j in positions[x + 1:]:
            if a != b and spatial_neighbors is not None and b not in spatial_neighbors.get(a, ()):
                continue
            yield Move('swap', a, i, b, j)
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for j in range(i + 2, len(route) + 1):
                yield Move('reverse', a, i, a, j)
