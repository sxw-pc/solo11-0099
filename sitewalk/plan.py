import json
from hashlib import sha256

from .ingest import time
from .store import connect

CONTRACTS = ('fixed_order', 'dependency')
_CONTRACT_ALIASES = {
    'fixed_order': 'fixed_order',
    'fixed-order': 'fixed_order',
    'fixed': 'fixed_order',
    'order': 'fixed_order',
    'ord': 'fixed_order',
    'dependency': 'dependency',
    'dependencies': 'dependency',
    'dependency_route': 'dependency',
    'graph': 'dependency',
}
_EDGE_KEYS = ('prerequisites', 'predecessors', 'dependencies', 'edges', 'dependency_graph', 'precedence')
_POINT_EDGE_KEYS = ('after', 'requires', 'require', 'prerequisite', 'depends_on', 'dependencies', 'predecessors')
_EDGE_BEFORE_KEYS = ('before', 'from', 'prerequisite', 'requires', 'predecessor', 'source')
_EDGE_AFTER_KEYS = ('after', 'to', 'point', 'successor', 'target')


def canonical(value):
    """Stable bytes for hashing and evidence comparison."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return sha256(canonical(value)).hexdigest()


def _contract(plan):
    value = plan.get('route_contract', plan.get('contract'))
    if value is None:
        route = plan.get('route')
        value = route.get('contract') if isinstance(route, dict) else route
    if value is None:
        value = 'fixed_order'
    key = str(value).strip().lower()
    if key not in _CONTRACT_ALIASES:
        raise ValueError('invalid_route_contract')
    return _CONTRACT_ALIASES[key]


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _edge(entry):
    """Accept pairs and the common before/after object spellings."""
    if isinstance(entry, (list, tuple)):
        if len(entry) != 2:
            raise ValueError('invalid_prerequisite')
        return entry[0], entry[1]
    if isinstance(entry, dict):
        before = next((entry[k] for k in _EDGE_BEFORE_KEYS if k in entry), None)
        after = next((entry[k] for k in _EDGE_AFTER_KEYS if k in entry), None)
        if before is None or after is None:
            raise ValueError('invalid_prerequisite')
        return before, after
    raise ValueError('invalid_prerequisite')


def _flatten_edges(value, into):
    if isinstance(value, dict):
        # A single {before: ..., after: ...} object.
        if any(k in value for k in _EDGE_BEFORE_KEYS) and any(k in value for k in _EDGE_AFTER_KEYS):
            into.append(_edge(value))
            return
        # Otherwise treat it as an adjacency map: A -> [B, C].
        for before, after in value.items():
            for target in _as_list(after):
                into.append((before, target))
        return
    if isinstance(value, (list, tuple)):
        for entry in value:
            if isinstance(entry, (list, tuple, dict)):
                into.append(_edge(entry))
            else:
                raise ValueError('invalid_prerequisite')
        return
    raise ValueError('invalid_prerequisite')


def _edges(data, points, known):
    raw = []
    plan = data.get('plan') or {}
    sources = [data, plan]
    if isinstance(plan.get('route'), dict):
        sources.append(plan['route'])
    for source in sources:
        for key in _EDGE_KEYS:
            if key in source:
                _flatten_edges(source[key], raw)
    # Point-local spelling: point.after = ["A"] means A -> point.
    for point in points:
        for key in _POINT_EDGE_KEYS:
            if key in point:
                for before in _as_list(point[key]):
                    raw.append((before, point['id']))
    # Validate references and normalise to text before deduplicating.
    edges = set()
    for before, after in raw:
        if not isinstance(before, str) or not isinstance(after, str):
            raise ValueError('invalid_prerequisite')
        before, after = before.strip(), after.strip()
        if not before or not after:
            raise ValueError('invalid_prerequisite')
        if before not in known or after not in known:
            raise ValueError('unknown_prerequisite_point')
        edges.add((before, after))
    return sorted(edges)


def _check_cycle(edges):
    adjacency = {}
    for before, after in edges:
        adjacency.setdefault(before, set()).add(after)
        adjacency.setdefault(after, set())
    state = {}

    def visit(node):
        state[node] = 1
        for target in sorted(adjacency.get(node, ())):
            if state.get(target) == 1:
                raise ValueError('dependency_cycle')
            if state.get(target, 0) == 0:
                visit(target)
        state[node] = 2

    for node in sorted(adjacency):
        if state.get(node, 0) == 0:
            visit(node)


def _normalise_point(point):
    try:
        pid = str(point['id'])
        order = point.get('ord', point.get('order'))
        latitude = float(point['lat'])
        longitude = float(point['lon'])
        radius = float(point['radius_m'])
        dwell = int(point['min_dwell_s'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid_point_rule') from exc
    if not pid:
        raise ValueError('invalid_point_rule')
    if order is None:
        raise ValueError('invalid_point_rule')
    try:
        order = int(order)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_point_rule') from exc
    if radius <= 0 or dwell < 0:
        raise ValueError('invalid_point_rule')
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('invalid_point_rule')
    return {
        'id': pid,
        'lat': latitude,
        'lon': longitude,
        'radius_m': radius,
        'min_dwell_s': dwell,
        'ord': order,
    }


def plan_fingerprint(plan_id, version, valid_from, valid_to, route_contract, points, edges):
    """Digest of the frozen route contract: it changes if any route bit changes."""
    payload = {
        'plan': {
            'id': plan_id,
            'version': version,
            'valid_from': valid_from,
            'valid_to': valid_to,
            'route_contract': route_contract,
        },
        'points': sorted(
            [
                {
                    'id': p['id'],
                    'lat': p['lat'],
                    'lon': p['lon'],
                    'radius_m': p['radius_m'],
                    'min_dwell_s': p['min_dwell_s'],
                    'ord': p['ord'],
                }
                for p in points
            ],
            key=lambda p: p['id'],
        ),
        'prerequisites': sorted([list(edge) for edge in edges]),
    }
    return digest(payload)


def load_plan(path, db_path):
    """Load and validate an immutable plan, its points and prerequisite graph.

    Both route contracts are accepted.  A fixed_order plan additionally states
    that the actual arrival order must equal ord; a dependency plan only makes
    the prerequisite edges binding.  Cycle, dangling-edge and duplicate-point
    errors reject the whole plan before anything is written.
    """
    data = json.loads(open(path, encoding='utf-8').read())
    plan = data['plan']
    plan_id = str(plan['id'])
    try:
        version = int(plan['version'])
        valid_from = plan['valid_from']
        valid_to = plan['valid_to']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid_plan_version') from exc
    if time(valid_from) >= time(valid_to):
        raise ValueError('invalid_plan_window')
    route_contract = _contract(plan)

    points = [_normalise_point(point) for point in data['points']]
    if len({p['id'] for p in points}) != len(points):
        raise ValueError('duplicate_point')
    if len({p['ord'] for p in points}) != len(points):
        raise ValueError('duplicate_order')
    known = {p['id'] for p in points}
    edges = _edges(data, points, known)
    _check_cycle(edges)
    if route_contract == 'fixed_order':
        # A fixed-order plan is already fully ordered by ord; an edge that
        # contradicts it can never be satisfied, so reject it at load time
        # instead of silently preferring one source of truth.
        order = {p['id']: p['ord'] for p in points}
        if any(order[before] >= order[after] for before, after in edges):
            raise ValueError('prerequisite_conflicts_with_order')
    fingerprint = plan_fingerprint(plan_id, version, valid_from, valid_to, route_contract, points, edges)

    con = connect(db_path)
    try:
        with con:
            old = con.execute('SELECT id FROM plan WHERE id=?', (plan_id,)).fetchone()
            if old:
                raise ValueError('plan_immutable')
            con.execute(
                'INSERT INTO plan(id,version,valid_from,valid_to,route_contract,digest) VALUES (?,?,?,?,?,?)',
                (plan_id, version, valid_from, valid_to, route_contract, fingerprint),
            )
            con.execute(
                'INSERT INTO batch(id,plan_id,state) VALUES (?,?,?)',
                (data.get('batch_id', plan_id + '-batch'), plan_id, 'ready'),
            )
            for point in points:
                con.execute(
                    'INSERT INTO point(id,plan_id,lat,lon,radius_m,min_dwell_s,ord) VALUES (?,?,?,?,?,?,?)',
                    (point['id'], plan_id, point['lat'], point['lon'], point['radius_m'],
                     point['min_dwell_s'], point['ord']),
                )
            for before, after in edges:
                con.execute(
                    'INSERT INTO prerequisite(plan_id,before,after) VALUES (?,?,?)',
                    (plan_id, before, after),
                )
    finally:
        con.close()
    return len(points)


def stored_plan(con, plan_id):
    plan = con.execute('SELECT * FROM plan WHERE id=?', (plan_id,)).fetchone()
    if not plan:
        return None
    points = con.execute('SELECT * FROM point WHERE plan_id=? ORDER BY ord,id', (plan_id,)).fetchall()
    edges = con.execute(
        'SELECT before,after FROM prerequisite WHERE plan_id=? ORDER BY before,after', (plan_id,)
    ).fetchall()
    return plan, points, edges


def verify_plan(db_path, plan_id=None):
    """Recompute each frozen plan digest; returns {plan_id: bool}."""
    con = connect(db_path)
    try:
        rows = con.execute('SELECT id FROM plan ORDER BY id').fetchall()
        checked = {}
        for row in rows:
            if plan_id is not None and row['id'] != plan_id:
                continue
            plan, points, edges = stored_plan(con, row['id'])
            if not plan['digest']:
                checked[row['id']] = False
                continue
            actual = plan_fingerprint(
                plan['id'], plan['version'], plan['valid_from'], plan['valid_to'],
                plan['route_contract'],
                [dict(p) for p in points],
                [(e['before'], e['after']) for e in edges],
            )
            checked[row['id']] = actual == plan['digest']
        return checked
    finally:
        con.close()
