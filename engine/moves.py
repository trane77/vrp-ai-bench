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


def enumerate_moves(routes):
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for b, dest in enumerate(routes):
                for j in range(len(dest) + 1):
                    if a == b and j in (i, i + 1):
                        continue
                    yield Move('relocate', a, i, b, j)
    positions = [(a, i) for a, route in enumerate(routes) for i in range(len(route))]
    for x, (a, i) in enumerate(positions):
        for b, j in positions[x + 1:]:
            yield Move('swap', a, i, b, j)
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for j in range(i + 2, len(route) + 1):
                yield Move('reverse', a, i, a, j)
