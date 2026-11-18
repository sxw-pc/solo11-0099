"""材料过磅结算采录：原始地磅读数冻结、全局匹配证明、净重票据与签收台账。

设计要点：
- ``register`` 只冻结合同原始字节，不做匹配或计价；
- ``settle`` 对每种预先约定的身份依据分别做离线完整推演，最大化合法完整票数，
  再按身份可信度、时差、ID 字典序稳定裁定，只确认事先签约的身份链；
- 票据绑定原始读数字节哈希、磅秤校准版本、匹配链与司机/收货签收；
- 已签收净重不可改写，更正只产生替代票与差额单。
"""
import json
from hashlib import sha256
from pathlib import Path

from .ingest import time
from .store import connect

RULES_VERSION = 'weighbridge-2'
MAX_GROSS = 12
MAX_TARES = 12
BLUR_BELOW_DEFAULT = 0.90
IDENTITY_CONFIDENCE_FIELD = {
    'camera_plate': 'camera_confidence',
    'rfid_trailer': 'rfid_confidence',
}

WB_SCHEMA = '''
CREATE TABLE IF NOT EXISTS weigh_case(
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id),
  source_sha256 TEXT NOT NULL, contract_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wb_amendment(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES weigh_case(id),
  revision INTEGER NOT NULL, reason TEXT NOT NULL,
  read_updates_json TEXT NOT NULL, calibration_updates_json TEXT NOT NULL,
  amended_contract_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL,
  UNIQUE(case_id, revision)
);
CREATE TABLE IF NOT EXISTS wb_derivation(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
  identity_contract TEXT NOT NULL, confirmed INTEGER NOT NULL,
  proof_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  UNIQUE(case_id, revision, identity_contract)
);
CREATE TABLE IF NOT EXISTS wb_ticket(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
  derivation_id TEXT NOT NULL REFERENCES wb_derivation(id),
  ticket_no TEXT NOT NULL, gross_read_id TEXT NOT NULL, tare_read_id TEXT NOT NULL,
  gross_kg INTEGER NOT NULL, tare_kg INTEGER NOT NULL, net_kg INTEGER NOT NULL,
  payment_cents INTEGER NOT NULL, payment_remainder_cents INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('confirmed','comparison','signed','sign_failed','superseded','replaced')),
  match_chain_json TEXT NOT NULL,
  driver TEXT, consignee TEXT, signed_at TEXT, sign_note TEXT,
  UNIQUE(case_id, revision, ticket_no)
);
CREATE TABLE IF NOT EXISTS wb_dispute(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
  identity_contract TEXT, code TEXT NOT NULL, state TEXT NOT NULL,
  subject_read_id TEXT, detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wb_print_job(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, ticket_id TEXT NOT NULL REFERENCES wb_ticket(id),
  printer_serial TEXT NOT NULL, paper_ticket_no TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('printed','acked','lost')),
  printed_at TEXT NOT NULL, ack_at TEXT, scanner_serial TEXT,
  reprint_of_id TEXT REFERENCES wb_print_job(id), note TEXT,
  UNIQUE(case_id, paper_ticket_no)
);
CREATE TABLE IF NOT EXISTS wb_revision(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
  amendment_id TEXT NOT NULL REFERENCES wb_amendment(id),
  old_ticket_id TEXT, new_ticket_id TEXT,
  delta_net_kg INTEGER NOT NULL, delta_payment_cents INTEGER NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN ('replacement','delta')),
  detail_json TEXT NOT NULL
);
'''


def ensure_schema(con):
    con.executescript(WB_SCHEMA)


def _h(obj):
    return sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# 纯匹配引擎：对可行边做完整枚举证明，离线规模至多 12 毛重 / 12 皮重
# --------------------------------------------------------------------------- #
def greedy_nearest(edges):
    """反例基准：按毛重到达顺序，逐条取时差最近且未占用的皮重。"""
    open_tares = {e['tare'] for e in edges}
    pairs = []
    for gross in sorted({e['gross'] for e in edges},
                        key=lambda g: (min(e['gross_at'] for e in edges if e['gross'] == g), g)):
        choices = [e for e in edges if e['gross'] == gross and e['tare'] in open_tares]
        if not choices:
            continue
        best = min(choices, key=lambda e: (e['delta_minutes'], e['tare']))
        open_tares.discard(best['tare'])
        pairs.append([best['gross'], best['tare']])
    return pairs


def optimal_matching(edges):
    """记忆化 DFS 穷尽所有 (毛重, 可用皮重) 组合，返回完整证明。

    裁定顺序（完全并列时稳定）：
      1. 合法完整票数最多；
      2. 身份可信度之和最高；
      3. 合同时差之和最小；
      4. 配对 ID 元组字典序。
    """
    gross_ids = sorted({e['gross'] for e in edges})
    tare_ids = sorted({e['tare'] for e in edges})
    tare_bit = {t: i for i, t in enumerate(tare_ids)}
    by_gross = {g: sorted((e for e in edges if e['gross'] == g),
                          key=lambda e: e['tare']) for g in gross_ids}
    states = 0
    memo = {}

    def dfs(i, used):
        nonlocal states
        memo_key = (i, used)
        if memo_key in memo:
            return memo[memo_key]
        states += 1
        if i == len(gross_ids):
            return 0, 0.0, 0.0, (), 1
        # 候选路径：弃权，或与每个未占用皮重配对；同评分候选先聚合匹配数
        candidates = {}
        skip = dfs(i + 1, used)
        candidates[skip[3]] = (skip[0], skip[1], skip[2], skip[4])
        for e in by_gross[gross_ids[i]]:
            bit = 1 << tare_bit[e['tare']]
            if used & bit:
                continue
            sub = dfs(i + 1, used | bit)
            pair_key = ((gross_ids[i], e['tare']),) + sub[3]
            cand = (1 + sub[0], e['confidence'] + sub[1],
                    e['delta_minutes'] + sub[2], sub[4])
            if pair_key in candidates:
                old = candidates[pair_key]
                candidates[pair_key] = (old[0], old[1], old[2], old[3] + cand[3])
            else:
                candidates[pair_key] = cand
        # 裁定：票数最多 -> 可信度最高 -> 时差最小 -> 配对元组字典序
        best_pairs, (count, conf, delta, ways) = min(
            candidates.items(),
            key=lambda kv: (-kv[1][0], -kv[1][1], kv[1][2], kv[0]))
        out = (count, conf, delta, best_pairs, ways)
        memo[memo_key] = out
        return out

    count, conf, delta, pairs, ways = dfs(0, 0)
    return {
        'pairs': [list(p) for p in pairs],
        'ticket_count': count,
        'identity_confidence_sum': round(conf, 6),
        'time_delta_minutes_sum': delta,
        'optimal_matching_count': ways,
        'states_evaluated': states,
        'tie_break_order': [
            'max_ticket_count',
            'max_identity_confidence_sum',
            'min_time_delta_sum',
            'lexicographic_pair_ids',
        ],
    }


# --------------------------------------------------------------------------- #
# 合同冻结
# --------------------------------------------------------------------------- #
def _validate_contract(contract):
    for key in ('case_id', 'plan_id', 'site_point', 'reads',
                'identity_contracts', 'max_cycle_minutes',
                'material_price_cents_per_1000kg'):
        if key not in contract:
            raise ValueError('contract_missing_' + key)
    identities = contract['identity_contracts']
    if not identities or any(i not in IDENTITY_CONFIDENCE_FIELD for i in identities):
        raise ValueError('invalid_identity_contract')
    confirmed = contract.get('confirmed_identity_contract', identities[0])
    if confirmed not in identities:
        raise ValueError('confirmed_contract_not_signed')
    reads = contract['reads']
    gross = [r for r in reads if r.get('kind') == 'gross']
    tares = [r for r in reads if r.get('kind') == 'tare']
    if len(gross) > MAX_GROSS or len(tares) > MAX_TARES:
        raise ValueError('offline_capacity_exceeded')
    if len({r['id'] for r in reads}) != len(reads):
        raise ValueError('duplicate_read_id')
    tickets = [r['ticket'] for r in reads if r.get('ticket')]
    if len(tickets) != len(set(tickets)):
        raise ValueError('duplicate_paper_ticket')
    for r in reads:
        time(r['at'])
        if not isinstance(r.get('kg'), int) or r['kg'] < 0:
            raise ValueError('invalid_weight')
    if int(contract['max_cycle_minutes']) <= 0:
        raise ValueError('invalid_time_window')
    return confirmed


def register(db_path, contract_path):
    raw = Path(contract_path).read_bytes()
    contract = json.loads(raw)
    confirmed = _validate_contract(contract)
    contract['confirmed_identity_contract'] = confirmed
    raw = json.dumps(contract, ensure_ascii=False, indent=2).encode()
    con = connect(db_path)
    ensure_schema(con)
    if not con.execute('SELECT 1 FROM point WHERE id=? AND plan_id=?',
                       (contract['site_point'], contract['plan_id'])).fetchone():
        con.close()
        raise ValueError('unknown_site_point')
    with con:
        if con.execute('SELECT 1 FROM weigh_case WHERE id=?',
                       (contract['case_id'],)).fetchone():
            con.close()
            raise ValueError('weigh_case_immutable')
        con.execute('INSERT INTO weigh_case VALUES (?,?,?,?)',
                    (contract['case_id'], contract['plan_id'],
                     sha256(raw).hexdigest(), raw.decode()))
    con.close()
    return contract['case_id']


# --------------------------------------------------------------------------- #
# 单票合法性：身份、校准、正净重、进出工地、唯一纸票、时间窗
# --------------------------------------------------------------------------- #
def _calibration_maps(contract):
    cals = {}
    for c in contract.get('calibrations', []):
        cals[(c['scale_id'], c['calibration_version'])] = c
    return cals


def _gate_maps(contract):
    return {g['read_id']: g for g in contract.get('gate_events', [])}


def _calibration_valid(read, cals, stamp):
    cal = cals.get((read.get('scale_id'), read.get('calibration_version')))
    if cal is None or cal.get('state') != 'valid':
        return False
    return time(cal['valid_from']) <= stamp < time(cal['valid_to'])


def check_pair(gross, tare, identity, contract, cals, gates):
    """返回 (可行?, 原因或None, 边信息)。一张票只使用一种身份依据。

    身份相等的常规路径之外，合同可用 ``edge_evidence`` 登记同身份依据下的
    现场旁路证据（如换挂后的闸门扫描），显式声明某条边合法；相机与 RFID
    字段仍不会在同一张票里拼接。
    """
    g_stamp = time(gross['at'])
    t_stamp = time(tare['at'])
    delta = (t_stamp - g_stamp).total_seconds() / 60.0
    override = None
    for ev in contract.get('edge_evidence', []):
        if ev.get('gross') == gross['id'] and ev.get('tare') == tare['id'] \
                and ev.get('identity_contract') == identity:
            override = ev
    info = {
        'gross': gross['id'], 'tare': tare['id'],
        'gross_at': gross['at'], 'tare_at': tare['at'],
        'delta_minutes': delta,
        'identity_contract': identity,
        'gross_identity': gross.get(identity),
        'tare_identity': tare.get(identity),
        'confidence': min(float(gross.get(IDENTITY_CONFIDENCE_FIELD[identity], 1.0)),
                          float(tare.get(IDENTITY_CONFIDENCE_FIELD[identity], 1.0))),
    }
    if override is not None:
        if not override.get('evidence_hash') or not override.get('identity_value'):
            return False, 'edge_evidence_incomplete', info
        info['edge_evidence'] = override['evidence_hash']
        info['identity_value'] = override['identity_value']
        info['confidence'] = float(override.get('confidence', info['confidence']))
    if delta < 0:
        return False, 'tare_before_gross', info
    if delta > int(contract['max_cycle_minutes']):
        return False, 'outside_contract_window', info
    g_val, t_val = gross.get(identity), tare.get(identity)
    if not g_val or not t_val:
        if override is None:
            return False, 'identity_missing', info
    if identity == 'camera_plate' and override is None:
        blur = float(contract.get('camera_blur_below', BLUR_BELOW_DEFAULT))
        if float(gross.get('camera_confidence', 1.0)) < blur or \
           float(tare.get('camera_confidence', 1.0)) < blur:
            return False, 'plate_blurred', info
    if override is None and g_val != t_val:
        return False, 'identity_mismatch', info
    if gross.get('calibration_version') != tare.get('calibration_version') or \
       gross.get('scale_id') != tare.get('scale_id'):
        return False, 'calibration_version_differs', info
    if not _calibration_valid(gross, cals, g_stamp) or \
       not _calibration_valid(tare, cals, t_stamp):
        return False, 'calibration_invalid', info
    g_gate = gates.get(gross['id'])
    t_gate = gates.get(tare['id'])
    if gross.get('gate') != 'in' or tare.get('gate') != 'out' or \
       g_gate is None or t_gate is None or \
       g_gate.get('direction') != 'in' or t_gate.get('direction') != 'out':
        return False, 'gate_record_inconsistent', info
    if not gross.get('ticket') or not tare.get('ticket'):
        return False, 'paper_ticket_missing', info
    if int(gross['kg']) <= int(tare['kg']):
        return False, 'non_positive_net_weight', info
    return True, None, info


def _read_level_issues(reads, identity, cals, gates, blur_threshold, contract):
    # 旁路证据已解释的 (读数, 身份) 组合不再报身份类争议，其余硬校验仍执行
    override_reads = set()
    for ev in contract.get('edge_evidence', []):
        if ev.get('identity_contract') == identity and ev.get('evidence_hash'):
            override_reads.add(ev['gross'])
            override_reads.add(ev['tare'])
    issues = []
    for r in reads:
        stamp = time(r['at'])
        if not r.get(identity) and r['id'] not in override_reads:
            issues.append((r['id'], 'identity_missing'))
        if identity == 'camera_plate':
            if float(r.get('camera_confidence', 1.0)) < blur_threshold:
                issues.append((r['id'], 'plate_blurred'))
        if not _calibration_valid(r, cals, stamp):
            issues.append((r['id'], 'calibration_invalid'))
        gate = gates.get(r['id'])
        expect = 'in' if r['kind'] == 'gross' else 'out'
        if r.get('gate') != expect or gate is None or gate.get('direction') != expect:
            issues.append((r['id'], 'gate_record_inconsistent'))
        if not r.get('ticket'):
            issues.append((r['id'], 'paper_ticket_missing'))
    return issues


# --------------------------------------------------------------------------- #
# 结算推演
# --------------------------------------------------------------------------- #
def _load_case(con, case_id):
    row = con.execute('SELECT * FROM weigh_case WHERE id=?', (case_id,)).fetchone()
    if row is None:
        raise ValueError('unknown_weigh_case')
    return row, json.loads(row['contract_json'])


def _payment(net_kg, price):
    total = int(net_kg) * int(price)
    return total // 1000, total % 1000


def _build_derivation(con, case_id, revision, contract, source_sha, identity,
                      confirmed, cals, gates):
    reads = contract['reads']
    read_by_id = {r['id']: r for r in reads}
    gross = sorted((r for r in reads if r['kind'] == 'gross'),
                   key=lambda r: (r['at'], r['id']))
    tares = sorted((r for r in reads if r['kind'] == 'tare'),
                   key=lambda r: (r['at'], r['id']))
    edges, rejected = [], []
    for g in gross:
        for t in tares:
            ok, reason, info = check_pair(g, t, identity, contract, cals, gates)
            if ok:
                edges.append(info)
            else:
                rejected.append({'gross': g['id'], 'tare': t['id'], 'reason': reason})
    greedy_pairs = greedy_nearest(edges)
    optimum = optimal_matching(edges)
    proof = {
        'rules_version': RULES_VERSION,
        'identity_contract': identity,
        'confirmed_identity_contract': contract.get('confirmed_identity_contract'),
        'capacity': {'max_gross': MAX_GROSS, 'max_tares': MAX_TARES,
                     'gross_count': len(gross), 'tare_count': len(tares)},
        'feasible_edges': edges,
        'rejected_pairs': rejected,
        'greedy_nearest': {'rule': 'nearest_time_first',
                           'pairs': greedy_pairs,
                           'ticket_count': len(greedy_pairs)},
        'exhaustive_search': {
            'algorithm': 'memoized_dfs_over_all_gross_and_available_tares',
            'states_evaluated': optimum['states_evaluated'],
            'max_cardinality': optimum['ticket_count'],
            'optimal_matching_count': optimum['optimal_matching_count'],
            'identity_confidence_sum': optimum['identity_confidence_sum'],
            'time_delta_minutes_sum': optimum['time_delta_minutes_sum'],
            'tie_break_order': optimum['tie_break_order'],
        },
        'selected_pairs': optimum['pairs'],
        'complete': optimum['ticket_count'] == len(gross) and len(gross) > 0,
    }
    derivation_id = f'{case_id}:r{revision}:{identity}'
    con.execute('INSERT INTO wb_derivation VALUES (?,?,?,?,?,?,?)',
                (derivation_id, case_id, revision, identity,
                 1 if identity == confirmed else 0,
                 json.dumps(proof, ensure_ascii=False, sort_keys=True), source_sha))

    paired_gross, paired_tare = set(), set()
    edge_index = {(e['gross'], e['tare']): e for e in edges}
    price = int(contract['material_price_cents_per_1000kg'])
    for g_id, t_id in optimum['pairs']:
        g, t = read_by_id[g_id], read_by_id[t_id]
        paired_gross.add(g_id)
        paired_tare.add(t_id)
        net = int(g['kg']) - int(t['kg'])
        paid, remainder = _payment(net, price)
        edge = edge_index[(g_id, t_id)]
        chain = {
            'identity_contract': identity,
            'identity_value': edge.get('identity_value', g.get(identity)),
            'edge_evidence': edge.get('edge_evidence'),
            'gross': _read_evidence(g, source_sha),
            'tare': _read_evidence(t, source_sha),
            'delta_minutes': (time(t['at']) - time(g['at'])).total_seconds() / 60.0,
            'net_kg': net,
            'price_cents_per_1000kg': price,
            'payment_cents': paid,
            'payment_remainder_cents': remainder,
            'passed_checks': ['contract_time_window', 'single_identity_chain',
                             'scale_calibration_valid', 'gate_in_out_consistent',
                             'unique_paper_ticket', 'positive_net_weight'],
            'case_source_sha256': source_sha,
            'rules_version': RULES_VERSION,
        }
        ticket_id = f'{case_id}:r{revision}:{identity}:{g_id}'
        state = 'confirmed' if identity == confirmed else 'comparison'
        con.execute('INSERT INTO wb_ticket VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ticket_id, case_id, revision, derivation_id,
                     f'NT-{case_id}-{identity}-{g_id}', g_id, t_id,
                     int(g['kg']), int(t['kg']), net, paid, remainder, state,
                     json.dumps(chain, ensure_ascii=False, sort_keys=True),
                     g.get('driver'), g.get('consignee'), None, None))

    reason_by_gross = {}
    for item in rejected:
        reason_by_gross.setdefault(item['gross'], []).append(
            {'tare': item['tare'], 'reason': item['reason']})
    for g in gross:
        if g['id'] not in paired_gross:
            _insert_dispute(con, case_id, revision, identity,
                            'unmatched_gross', g['id'],
                            {'rejection_reasons': reason_by_gross.get(g['id'], [])})
    for t in tares:
        if t['id'] not in paired_tare:
            _insert_dispute(con, case_id, revision, identity,
                            'unmatched_tare', t['id'], {})
    for read_id, code in _read_level_issues(reads, identity, cals, gates,
                                            float(contract.get('camera_blur_below',
                                                               BLUR_BELOW_DEFAULT)),
                                            contract):
        _insert_dispute(con, case_id, revision, identity, code, read_id, {})
    return proof


def _read_evidence(read, source_sha):
    return {
        'read_id': read['id'], 'at': read['at'], 'kg': read['kg'],
        'raw_paper_ticket': read.get('ticket'),
        'read_sha256': _h(read),
        'scale_id': read.get('scale_id'),
        'calibration_version': read.get('calibration_version'),
        'gate': read.get('gate'),
        'camera_plate': read.get('camera_plate'),
        'camera_confidence': read.get('camera_confidence'),
        'rfid_trailer': read.get('rfid_trailer'),
        'rfid_confidence': read.get('rfid_confidence'),
        'source_sha256': source_sha,
    }


def _insert_dispute(con, case_id, revision, identity, code, subject, detail):
    base = f'{case_id}:r{revision}:{identity or "raw"}:{code}:{subject or "x"}'
    did = base
    seq = 1
    while con.execute('SELECT 1 FROM wb_dispute WHERE id=?', (did,)).fetchone():
        seq += 1
        did = f'{base}#{seq}'
    con.execute('INSERT INTO wb_dispute VALUES (?,?,?,?,?,?,?,?)',
                (did, case_id, revision, identity, code,
                 'pending_verification', subject,
                 json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def settle(db_path, case_id):
    con = connect(db_path)
    ensure_schema(con)
    row, contract = _load_case(con, case_id)
    if con.execute('SELECT 1 FROM wb_derivation WHERE case_id=? AND revision=0',
                   (case_id,)).fetchone():
        con.close()
        return case_id
    confirmed = contract.get('confirmed_identity_contract',
                             contract['identity_contracts'][0])
    cals, gates = _calibration_maps(contract), _gate_maps(contract)
    proofs = []
    with con:
        for identity in contract['identity_contracts']:
            proofs.append(_build_derivation(con, case_id, 0, contract,
                                            row['source_sha256'], identity,
                                            confirmed, cals, gates))
    con.close()
    return case_id


# --------------------------------------------------------------------------- #
# 签收、打印、ACK、未知纸票
# --------------------------------------------------------------------------- #
def sign_ticket(db_path, ticket_id, driver, consignee, signed_at,
                success=True, note=''):
    con = connect(db_path)
    ensure_schema(con)
    row = con.execute('SELECT * FROM wb_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        con.close()
        raise ValueError('unknown_ticket')
    if row['state'] == 'comparison':
        con.close()
        raise ValueError('ticket_not_confirmed_identity_chain')
    if row['state'] in ('signed', 'superseded', 'replaced'):
        con.close()
        raise ValueError('ticket_state_allows_no_resign')
    with con:
        if success:
            con.execute('''UPDATE wb_ticket SET state='signed', driver=?, consignee=?,
                           signed_at=?, sign_note=? WHERE id=?''',
                        (driver, consignee, signed_at, note, ticket_id))
        else:
            con.execute('''UPDATE wb_ticket SET state='sign_failed', driver=?,
                           consignee=?, signed_at=?, sign_note=? WHERE id=?''',
                        (driver, consignee, signed_at, note, ticket_id))
    con.close()
    return ticket_id


def record_print(db_path, ticket_id, printer_serial, printed_at, paper_ticket_no,
                 reprint_of=None, note=''):
    con = connect(db_path)
    ensure_schema(con)
    ticket = con.execute('SELECT * FROM wb_ticket WHERE id=?', (ticket_id,)).fetchone()
    if ticket is None:
        con.close()
        raise ValueError('unknown_ticket')
    if ticket['state'] != 'signed':
        con.close()
        raise ValueError('only_signed_ticket_is_printable')
    open_job = con.execute("""SELECT 1 FROM wb_print_job WHERE ticket_id=?
                              AND state='printed'""", (ticket_id,)).fetchone()
    if open_job:
        con.close()
        raise ValueError('paper_ticket_already_in_driver_hands')
    prior_jobs = con.execute(
        'SELECT id,state FROM wb_print_job WHERE ticket_id=?', (ticket_id,)).fetchall()
    if prior_jobs and not reprint_of:
        # 司机已持有过纸票（含 ACK 丢失）：无现场扫描确认的重印链不得再出票
        con.close()
        raise ValueError('reprint_requires_scan_verified_closed_job')
    if reprint_of:
        old = con.execute('SELECT * FROM wb_print_job WHERE id=?',
                          (reprint_of,)).fetchone()
        if old is None or old['ticket_id'] != ticket_id or old['state'] not in ('acked', 'lost'):
            con.close()
            raise ValueError('reprint_requires_scan_verified_closed_job')
    if con.execute('SELECT 1 FROM wb_print_job WHERE case_id=? AND paper_ticket_no=?',
                   (ticket['case_id'], paper_ticket_no)).fetchone():
        con.close()
        raise ValueError('paper_ticket_no_duplicate')
    job_id = f'{ticket_id}:print:{_h((ticket_id, printer_serial, printed_at, paper_ticket_no))[:12]}'
    with con:
        con.execute('INSERT INTO wb_print_job VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (job_id, ticket['case_id'], ticket_id, printer_serial,
                     paper_ticket_no, 'printed', printed_at, None, None,
                     reprint_of, note))
    con.close()
    return job_id


def acknowledge_print(db_path, print_job_id, ack_at, scanner_serial):
    con = connect(db_path)
    ensure_schema(con)
    job = con.execute('SELECT * FROM wb_print_job WHERE id=?',
                      (print_job_id,)).fetchone()
    if job is None:
        con.close()
        raise ValueError('unknown_print_job')
    if job['state'] == 'acked':
        con.close()
        raise ValueError('duplicate_acknowledgement')
    if job['state'] == 'lost':
        con.close()
        raise ValueError('acknowledging_lost_print_job')
    with con:
        con.execute('''UPDATE wb_print_job SET state='acked', ack_at=?,
                       scanner_serial=? WHERE id=?''',
                    (ack_at, scanner_serial, print_job_id))
    con.close()
    return print_job_id


def mark_print_lost(db_path, print_job_id, scanner_serial, note):
    """现场扫描核对后登记 ACK 丢失，才允许重印同一票据（不产生第二张有效票）。"""
    con = connect(db_path)
    ensure_schema(con)
    job = con.execute('SELECT * FROM wb_print_job WHERE id=?',
                      (print_job_id,)).fetchone()
    if job is None:
        con.close()
        raise ValueError('unknown_print_job')
    if job['state'] != 'printed':
        con.close()
        raise ValueError('only_open_print_job_can_be_lost')
    with con:
        con.execute('''UPDATE wb_print_job SET state='lost', scanner_serial=?,
                       note=? WHERE id=?''', (scanner_serial, note, print_job_id))
    con.close()
    return print_job_id


def register_paper_ticket(db_path, case_id, paper_ticket_no, found_at, scanner_serial):
    """未知纸票：无对应打印作业，进入待核实争议单。"""
    con = connect(db_path)
    ensure_schema(con)
    _load_case(con, case_id)
    known = con.execute('SELECT 1 FROM wb_print_job WHERE case_id=? AND paper_ticket_no=?',
                        (case_id, paper_ticket_no)).fetchone()
    already_queried = con.execute(
        "SELECT 1 FROM wb_dispute WHERE case_id=? AND code='unknown_paper_ticket' "
        "AND subject_read_id=?", (case_id, paper_ticket_no)).fetchone()
    if known or already_queried:
        con.close()
        raise ValueError('paper_ticket_already_known')
    with con:
        _insert_dispute(con, case_id,
                        con.execute('SELECT COALESCE(MAX(revision),0) FROM wb_amendment WHERE case_id=?',
                                    (case_id,)).fetchone()[0],
                        None, 'unknown_paper_ticket', paper_ticket_no,
                        {'found_at': found_at, 'scanner_serial': scanner_serial})
    con.close()
    return paper_ticket_no


# --------------------------------------------------------------------------- #
# 更正：不改写已签收净重，保留原票并出替代票与差额单
# --------------------------------------------------------------------------- #
def amend(db_path, case_id, amendment_id, reason, applied_at,
          read_updates=None, calibration_updates=None):
    read_updates = read_updates or {}
    calibration_updates = calibration_updates or {}
    con = connect(db_path)
    ensure_schema(con)
    row, contract = _load_case(con, case_id)
    next_rev = con.execute(
        'SELECT COALESCE(MAX(revision)+1,1) FROM wb_amendment WHERE case_id=?',
        (case_id,)).fetchone()[0]
    amended = json.loads(json.dumps(contract))
    by_id = {r['id']: r for r in amended['reads']}
    for read_id, changes in read_updates.items():
        if read_id not in by_id:
            con.close()
            raise ValueError('unknown_read_in_amendment')
        by_id[read_id].update(changes)
    cal_key = {(c['scale_id'], c['calibration_version']): c
               for c in amended.get('calibrations', [])}
    for changes in calibration_updates:
        key = (changes['scale_id'], changes['calibration_version'])
        cal_key[key] = {**cal_key.get(key, {}), **changes}
    amended['calibrations'] = list(cal_key.values())
    amended_blob = json.dumps(amended, ensure_ascii=False, sort_keys=True).encode()
    amended_sha = sha256(amended_blob).hexdigest()
    confirmed = amended.get('confirmed_identity_contract',
                            amended['identity_contracts'][0])
    cals, gates = _calibration_maps(amended), _gate_maps(amended)
    with con:
        con.execute('INSERT INTO wb_amendment VALUES (?,?,?,?,?,?,?,?)',
                    (amendment_id, case_id, next_rev, reason,
                     json.dumps(read_updates, ensure_ascii=False, sort_keys=True),
                     json.dumps(calibration_updates, ensure_ascii=False, sort_keys=True),
                     amended_sha, applied_at))
        # 旧推演票据全部留档：未签收标记 replaced，已签收标记 superseded
        con.execute('''UPDATE wb_ticket SET state='replaced'
                       WHERE case_id=? AND revision=? AND state IN
                       ('confirmed','comparison','sign_failed')''',
                    (case_id, next_rev - 1))
        con.execute('''UPDATE wb_ticket SET state='superseded'
                       WHERE case_id=? AND revision=? AND state='signed' ''',
                    (case_id, next_rev - 1))
        for identity in amended['identity_contracts']:
            _build_derivation(con, case_id, next_rev, amended, amended_sha,
                              identity, confirmed, cals, gates)
        # 替代关系与差额单（按已确认身份链，逐旧票核对）
        old_tickets = con.execute(
            '''SELECT * FROM wb_ticket WHERE case_id=? AND revision=?
               AND derivation_id IN (SELECT id FROM wb_derivation
                 WHERE case_id=? AND revision=? AND confirmed=1)''',
            (case_id, next_rev - 1, case_id, next_rev - 1)).fetchall()
        new_tickets = {r['gross_read_id']: r for r in con.execute(
            '''SELECT * FROM wb_ticket WHERE case_id=? AND revision=?
               AND derivation_id IN (SELECT id FROM wb_derivation
                 WHERE case_id=? AND revision=? AND confirmed=1)''',
            (case_id, next_rev, case_id, next_rev)).fetchall()}
        for old in old_tickets:
            new = new_tickets.get(old['gross_read_id'])
            new_id = new['id'] if new else None
            d_net = (new['net_kg'] if new else 0) - old['net_kg']
            d_pay = (new['payment_cents'] if new else 0) - old['payment_cents']
            detail = {'reason': reason, 'old_ticket_id': old['id'],
                      'new_ticket_id': new_id,
                      'old_net_kg': old['net_kg'],
                      'new_net_kg': new['net_kg'] if new else None}
            con.execute('INSERT INTO wb_revision VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (f'{amendment_id}:{old["id"]}:repl', case_id, next_rev,
                         amendment_id, old['id'], new_id, d_net, d_pay,
                         'replacement',
                         json.dumps(detail, ensure_ascii=False, sort_keys=True)))
            con.execute('INSERT INTO wb_revision VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (f'{amendment_id}:{old["id"]}:delta', case_id, next_rev,
                         amendment_id, old['id'], new_id, d_net, d_pay,
                         'delta',
                         json.dumps({'payment_delta_cents': d_pay,
                                     'net_delta_kg': d_net},
                                    ensure_ascii=False, sort_keys=True)))
    con.close()
    return amendment_id


# --------------------------------------------------------------------------- #
# JSON / Markdown 地磅单
# --------------------------------------------------------------------------- #
def report(db_path, case_id, out_path):
    con = connect(db_path)
    ensure_schema(con)
    _load_case(con, case_id)
    data = {
        'case_id': case_id,
        'rules_version': RULES_VERSION,
        'weigh_case': dict(con.execute(
            'SELECT id,plan_id,source_sha256 FROM weigh_case WHERE id=?',
            (case_id,)).fetchone()),
        'amendments': [dict(r) for r in con.execute(
            'SELECT * FROM wb_amendment WHERE case_id=? ORDER BY revision',
            (case_id,))],
        'derivations': [dict(r) for r in con.execute(
            'SELECT * FROM wb_derivation WHERE case_id=? ORDER BY revision,identity_contract',
            (case_id,))],
        'tickets': [_ticket_row(r) for r in con.execute(
            'SELECT * FROM wb_ticket WHERE case_id=? ORDER BY revision,id',
            (case_id,))],
        'disputes': [dict(r) for r in con.execute(
            'SELECT * FROM wb_dispute WHERE case_id=? ORDER BY revision,id',
            (case_id,))],
        'print_jobs': [dict(r) for r in con.execute(
            'SELECT * FROM wb_print_job WHERE case_id=? ORDER BY id',
            (case_id,))],
        'revisions': [dict(r) for r in con.execute(
            'SELECT * FROM wb_revision WHERE case_id=? ORDER BY revision,id',
            (case_id,))],
    }
    for d in data['derivations']:
        d['proof_json'] = json.loads(d['proof_json'])
    con.close()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding='utf-8')
    out.with_suffix('.md').write_text(_markdown(data), encoding='utf-8')
    return out


def _ticket_row(row):
    d = dict(row)
    d['match_chain'] = json.loads(d.pop('match_chain_json'))
    return d


def _markdown(data):
    lines = [f'# 地磅材料净重结算单 {data["case_id"]}', '',
             f'- 规则版本：{data["rules_version"]}',
             f'- 原始合同字节哈希：`{data["weigh_case"]["source_sha256"]}`', '']
    for t in data['tickets']:
        chain = t['match_chain']
        lines += [
            f'## 票 {t["ticket_no"]}（{t["state"]}，修订 r{t["revision"]}）',
            f'- 身份依据：{chain["identity_contract"]} = `{chain["identity_value"]}`',
            f'- 毛重来源：{chain["gross"]["read_id"]} {chain["gross"]["kg"]} kg '
            f'@ {chain["gross"]["at"]}，纸票 {chain["gross"]["raw_paper_ticket"]}，'
            f'校准 {chain["gross"]["calibration_version"]}，读数哈希 `{chain["gross"]["read_sha256"][:16]}…`',
            f'- 皮重来源：{chain["tare"]["read_id"]} {chain["tare"]["kg"]} kg '
            f'@ {chain["tare"]["at"]}，纸票 {chain["tare"]["raw_paper_ticket"]}，'
            f'校准 {chain["tare"]["calibration_version"]}，读数哈希 `{chain["tare"]["read_sha256"][:16]}…`',
            f'- 时差：{chain["delta_minutes"]} 分钟；净重：{chain["net_kg"]} kg',
            f'- 应付：{chain["payment_cents"]} 分'
            + (f'（余 {chain["payment_remainder_cents"]} 分不足计价单位）'
               if chain['payment_remainder_cents'] else ''),
            f'- 签收：{t["driver"] or "未签收"} / {t["consignee"] or "—"}'
            + (f' @ {t["signed_at"]}' if t['signed_at'] else ''),
            f'- 校验：{"、".join(chain["passed_checks"])}', '']
    lines.append('## 逐票推演证明')
    for d in data['derivations']:
        p = d['proof_json']
        tag = '已确认' if d['confirmed'] else '比较证据（不签收）'
        search = p['exhaustive_search']
        lines += [
            f'- 修订 r{d["revision"]} 身份链 `{d["identity_contract"]}`（{tag}）：'
            f'穷尽 {search["states_evaluated"]} 个记忆化状态，最大合法票数 {search["max_cardinality"]}，'
            f'最优匹配 {search["optimal_matching_count"]} 组，选中 {p["selected_pairs"]}；'
            f'最近优先贪心仅得 {p["greedy_nearest"]["pairs"]}',
        ]
    lines.append('')
    lines.append('## 争议单')
    for d in data['disputes']:
        lines.append(f'- [{d["state"]}] r{d["revision"]} {d["identity_contract"] or "raw"} '
                     f'{d["code"]}：{d["subject_read_id"] or d["id"]} {d["detail_json"]}')
    if data['revisions']:
        lines += ['', '## 更正与差额单（原票保留，不重写已签收净重）']
        for r in data['revisions']:
            lines.append(f'- r{r["revision"]} {r["relation"]}: {r["old_ticket_id"]} -> '
                         f'{r["new_ticket_id"]}，净重差 {r["delta_net_kg"]} kg，'
                         f'付款差 {r["delta_payment_cents"]} 分')
    lines.append('')
    return '\n'.join(lines)
