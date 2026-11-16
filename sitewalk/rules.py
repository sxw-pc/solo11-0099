import json
import math
from datetime import timedelta
from hashlib import sha256

from .ingest import time
from .plan import CONTRACTS, digest, stored_plan
from .store import connect

RULES_VERSION = '2'
MAX_TRACK_GAP = timedelta(minutes=10)
# Issue codes owned by a single evaluation run.  They are cleared before each
# evaluation so repeated runs over the same input stay stable.
_CLEARED_CODES = (
    'reused_photo', 'missing_item', 'route_order', 'route_edge', 'route_missing',
    'insufficient_dwell', 'track_gap', 'plan_time', 'plan_version', 'unresolved_problem',
)


def distance(a, b, c, d):
    dy = (a - c) * 111_000
    dx = (b - d) * 111_000 * math.cos(math.radians((a + c) / 2))
    return math.hypot(dx, dy)


def location(track, stamp, max_gap=MAX_TRACK_GAP):
    """Linear interpolation between the surrounding track samples.

    A gap wider than max_gap, or a stamp outside the track, is unknown; the
    caller must not invent a coordinate in that case.
    """
    before = [r for r in track if time(r['ts']) <= stamp]
    after = [r for r in track if time(r['ts']) >= stamp]
    if not before or not after:
        return None
    a = before[-1]
    b = after[0]
    ta = time(a['ts'])
    tb = time(b['ts'])
    if tb - ta > max_gap:
        return None
    fraction = 0 if ta == tb else (stamp - ta) / (tb - ta)
    return a['lat'] + (b['lat'] - a['lat']) * fraction, a['lon'] + (b['lon'] - a['lon']) * fraction


def _point_evidence(con, point, plan, track, photos, reused):
    """Collect the per-point evidence and every reason it is not covered."""
    attached = sorted(
        [p for p in photos if p['point_id'] == point['id']],
        key=lambda p: (time(p['ts']), p['id']),
    )
    problems = set()
    coordinates = []
    valid = []
    for photo in attached:
        stamp = time(photo['ts'])
        if stamp < time(plan['valid_from']) or stamp >= time(plan['valid_to']):
            problems.add('plan_time')
            continue
        declared = photo.get('plan_version')
        if declared is not None:
            try:
                mismatch = int(declared) != int(plan['version'])
            except (TypeError, ValueError):
                mismatch = True
            if mismatch:
                problems.add('plan_version')
                continue
        loc = location(track, stamp)
        if loc is None:
            problems.add('track_gap')
            continue
        coordinates.append(list(loc))
        if distance(*loc, point['lat'], point['lon']) <= point['radius_m']:
            valid.append(photo)

    visits = sorted(
        [r for r in track
         if distance(r['lat'], r['lon'], point['lat'], point['lon']) <= point['radius_m']],
        key=lambda r: (time(r['ts']), r['id']),
    )
    # Visits no wider than the allowed gap form one continuous presence; a
    # longer absence splits the track into separate visits.
    stints = []
    for row in visits:
        if stints and time(row['ts']) - time(stints[-1][-1]['ts']) <= MAX_TRACK_GAP:
            stints[-1].append(row)
        else:
            stints.append([row])
    track_interval = None
    if visits:
        track_interval = [visits[0]['ts'], visits[-1]['ts']]

    if not valid:
        problems.add('insufficient_dwell')
    elif point['min_dwell_s'] > 0:
        dwell = max(
            (time(stint[-1]['ts']) - time(stint[0]['ts'])).total_seconds()
            for stint in stints
        ) if stints else 0
        if dwell < point['min_dwell_s']:
            problems.add('insufficient_dwell')
    # Disappearing from the radius and coming back after a long hole is a gap,
    # not one long dwell; a route contract cannot paper over it.
    if len(stints) > 1:
        problems.add('track_gap')

    for item in con.execute('SELECT * FROM item WHERE point_id=? ORDER BY id', (point['id'],)):
        if not item['note'] and not any(p['sha256'] == item['photo_hash'] for p in attached):
            problems.add('missing_item')
    if con.execute(
        "SELECT 1 FROM problem WHERE point_id=? AND state!='closed'", (point['id'],)
    ).fetchone():
        problems.add('unresolved_problem')
    if any(p['sha256'] in reused for p in attached):
        problems.add('reused_photo')

    # Arrival order is the order checks were evidenced: the first photo whose
    # interpolated position is inside the radius.  The track interval reports
    # separately when the receiver was physically inside the radius.
    arrival = valid[0]['ts'] if valid else None
    evidence = {
        'point': point['id'],
        'plan': plan['id'],
        'plan_version': int(plan['version']),
        'plan_digest': plan['digest'],
        'photos': [
            {'id': p['id'], 'ts': p['ts'], 'sha256': p['sha256'], 'plan_version': p.get('plan_version')}
            for p in attached
        ],
        'coordinates': coordinates,
        'arrival': arrival,
        'track_interval': track_interval,
        'issues': sorted(problems),
    }
    return {
        'attached': attached,
        'coordinates': coordinates,
        'valid': valid,
        'arrival': arrival,
        'track_interval': track_interval,
        'problems': problems,
        'evidence': evidence,
        'hash': digest(evidence),
    }


def _route_verdict(contract, points, edges, evidence):
    """Evaluate one route contract over per-point arrival evidence.

    fixed_order: actual arrival order must equal ord exactly; inverted pairs
    are reported as violated ordering constraints.
    dependency: every prerequisite A->B must have A arrive before B; points
    without dependencies may arrive in any physical order.

    In both contracts every required point must be covered: a relaxed route
    contract never converts a missed point into coverage.
    """
    arrivals = [
        (evidence[p['id']]['arrival'], p['ord'], p['id'])
        for p in points
        if evidence[p['id']]['arrival'] is not None
    ]
    arrivals.sort(key=lambda row: (time(row[0]), row[1], row[2]))
    arrival_order = [row[2] for row in arrivals]
    times = {pid: evidence[pid]['arrival'] for pid in arrival_order}
    uncovered = sorted(p['id'] for p in points if evidence[p['id']]['problems'])

    if contract == 'fixed_order':
        expected = [p['id'] for p in sorted(points, key=lambda p: (p['ord'], p['id']))]
        violated = [
            [before, after]
            for index, before in enumerate(expected)
            for after in expected[index + 1:]
            if before in times and after in times
            and time(times[after]) < time(times[before])
        ]
        accepted = not uncovered and arrival_order == expected
    elif contract == 'dependency':
        # "A before B" must be proven: an equal timestamp cannot demonstrate
        # that the prerequisite check completed first.
        violated = [
            [before, after]
            for before, after in edges
            if before in times and after in times
            and time(times[after]) <= time(times[before])
        ]
        accepted = not uncovered and len(arrival_order) == len(points)
    else:
        raise ValueError('invalid_route_contract')

    # Points absent from the arrival order or without usable evidence are
    # coverage gaps: a relaxed route contract never accepts a missed point.
    absent = sorted(p['id'] for p in points if p['id'] not in times)
    missing = sorted(set(uncovered) | set(absent))
    if missing or violated:
        accepted = False
    return {
        'contract': contract,
        'arrival_order': arrival_order,
        'violated_edges': violated,
        'missing_points': missing,
        'absent_points': absent,
        'accepted': accepted,
        'result': '覆盖' if accepted else '待复核',
    }


def _route_hash(plan, verdict, evidence):
    payload = {
        'plan': plan['id'],
        'plan_version': int(plan['version']),
        'plan_digest': plan['digest'],
        'contract': verdict['contract'],
        'arrival_order': verdict['arrival_order'],
        'violated_edges': verdict['violated_edges'],
        'missing_points': verdict['missing_points'],
        'points': {pid: evidence[pid]['hash'] for pid in sorted(evidence)},
    }
    return digest(payload)


def evaluate(db_path, route_contract=None):
    """Verify a run and return {point_id: result}.

    route_contract overrides the plan-declared contract for the per-point
    decisions; route_check always records both contracts so the two valid
    route contracts can be compared on the same evidence.
    """
    if route_contract is not None and route_contract not in CONTRACTS:
        raise ValueError('invalid_route_contract')
    con = connect(db_path)
    results = {}
    try:
        with con:
            con.execute('DELETE FROM decision')
            con.executemany(
                'DELETE FROM issue WHERE code=?', [(code,) for code in _CLEARED_CODES]
            )
            track = [dict(r) for r in con.execute('SELECT * FROM track ORDER BY ts,id')]
            photos = [dict(r) for r in con.execute('SELECT * FROM photo ORDER BY ts,id')]
            source_digest = sha256(
                '|'.join(r['sha256'] for r in con.execute('SELECT sha256 FROM source ORDER BY sha256')).encode()
            ).hexdigest()
            run_id = sha256((RULES_VERSION + ':' + source_digest).encode()).hexdigest()
            con.execute('INSERT OR IGNORE INTO run VALUES (?,?,?)', (run_id, RULES_VERSION, source_digest))
            for obj_kind, records in [('photo', photos), ('track', track)]:
                for obj in records:
                    subject = obj['point_id'] if obj_kind == 'photo' else '__route__'
                    eid = sha256(f"{obj_kind}:{obj['source_id']}:{obj['id']}".encode()).hexdigest()
                    con.execute(
                        'INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?,?)',
                        (eid, run_id, subject, obj['source_id'], obj_kind, obj['id'],
                         json.dumps(obj, ensure_ascii=False, sort_keys=True)),
                    )

            reused = {
                p['sha256'] for p in photos
                if len({(r['point_id'], r['ts'][:10]) for r in photos if r['sha256'] == p['sha256']}) > 1
            }
            for h in sorted(reused):
                con.execute('INSERT INTO issue(code,detail) VALUES (?,?)', ('reused_photo', h))

            points = [dict(r) for r in con.execute('SELECT * FROM point ORDER BY ord,id')]
            plans = {r['id']: dict(r) for r in con.execute('SELECT * FROM plan ORDER BY id')}
            evidence = {}
            for point in points:
                plan = plans[point['plan_id']]
                evidence[point['id']] = _point_evidence(con, point, plan, track, photos, reused)
                for code in evidence[point['id']]['problems']:
                    con.execute('INSERT INTO issue(code,detail) VALUES (?,?)', (code, point['id']))

            route_problems = {p['id']: set() for p in points}
            active_verdict = {}
            active_contract = {}
            for plan_id, plan in plans.items():
                plan_points = [p for p in points if p['plan_id'] == plan_id]
                if not plan_points:
                    continue
                _, _, edge_rows = stored_plan(con, plan_id)
                edges = [(row['before'], row['after']) for row in edge_rows]
                plan_evidence = {p['id']: evidence[p['id']] for p in plan_points}
                verdicts = {}
                for contract in CONTRACTS:
                    verdict = _route_verdict(contract, plan_points, edges, plan_evidence)
                    verdicts[contract] = verdict
                    con.execute(
                        'INSERT OR REPLACE INTO route_check VALUES (?,?,?,?,?,?,?,?,?)',
                        (plan_id, run_id, contract,
                         json.dumps(verdict['arrival_order'], ensure_ascii=False),
                         json.dumps(verdict['violated_edges'], ensure_ascii=False),
                         json.dumps(verdict['missing_points'], ensure_ascii=False),
                         verdict['result'], int(verdict['accepted']),
                         _route_hash(plan, verdict, plan_evidence)),
                    )
                active = route_contract or plan['route_contract']
                verdict = verdicts[active]
                active_verdict[plan_id] = verdict
                active_contract[plan_id] = active
                # A predecessor inverted relative to the declared contract is
                # reported under route_edge; fixed_order additionally uses
                # route_order for every pair that contradicts ord.
                code = 'route_edge' if active == 'dependency' else 'route_order'
                for edge in verdict['violated_edges']:
                    con.execute(
                        'INSERT INTO issue(code,detail) VALUES (?,?)',
                        (code, json.dumps(edge, ensure_ascii=False)),
                    )
                    for pid in edge:
                        route_problems[pid].add(code)
                for pid in verdict['missing_points']:
                    route_problems[pid].add('route_missing')
                    con.execute('INSERT INTO issue(code,detail) VALUES (?,?)', ('route_missing', pid))

            for point in points:
                item = evidence[point['id']]
                plan = plans[point['plan_id']]
                problems = sorted(item['problems'] | route_problems[point['id']])
                result = '待复核' if problems else '覆盖'
                point_evidence = dict(item['evidence'])
                point_evidence['issues'] = problems
                plan_verdict = active_verdict.get(point['plan_id'], {'arrival_order': [], 'violated_edges': []})
                con.execute(
                    'INSERT INTO decision(point_id,result,position,evidence,rules_version,run_id,'
                    'plan_version,route_contract,arrival_order,violated_edges,track_interval,evidence_hash) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (point['id'], result, json.dumps(item['coordinates'], ensure_ascii=False),
                     json.dumps(point_evidence, ensure_ascii=False, sort_keys=True),
                     RULES_VERSION, run_id, int(plan['version']), active_contract[point['plan_id']],
                     json.dumps(plan_verdict['arrival_order'], ensure_ascii=False),
                     json.dumps(plan_verdict['violated_edges'], ensure_ascii=False),
                     json.dumps(item['track_interval'], ensure_ascii=False), digest(point_evidence)),
                )
                results[point['id']] = result
    finally:
        con.close()
    return results


def route_report(db_path):
    """Return the route verdicts of the current run with their audit fields."""
    con = connect(db_path)
    try:
        current = con.execute('SELECT run_id FROM decision LIMIT 1').fetchone()
        if current:
            rows = con.execute(
                'SELECT * FROM route_check WHERE run_id=? ORDER BY plan_id,contract',
                (current['run_id'],),
            ).fetchall()
        else:
            rows = con.execute('SELECT * FROM route_check ORDER BY plan_id,contract').fetchall()
        report = []
        for row in rows:
            report.append({
                'plan_id': row['plan_id'],
                'run_id': row['run_id'],
                'contract': row['contract'],
                'arrival_order': json.loads(row['arrival_order']),
                'violated_edges': json.loads(row['violated_edges']),
                'missing_points': json.loads(row['missing_points']),
                'result': row['result'],
                'accepted': bool(row['accepted']),
                'evidence_hash': row['evidence_hash'],
            })
        return report
    finally:
        con.close()


def advance_problem(db_path, problem_id, new_state, evidence):
    allowed = {'found': 'assigned', 'assigned': 'rechecked', 'rechecked': 'closed'}
    con = connect(db_path)
    with con:
        old = con.execute('SELECT state FROM problem WHERE id=?', (problem_id,)).fetchone()
        if not old or allowed.get(old['state']) != new_state or not evidence:
            raise ValueError('illegal_problem_transition')
        con.execute('UPDATE problem SET state=?,evidence=? WHERE id=?', (new_state, evidence, problem_id))
    con.close()
