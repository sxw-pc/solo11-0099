import json
from pathlib import Path

import pytest

from sitewalk.__main__ import init
from sitewalk.store import connect
from sitewalk.weighbridge import (
    register, settle, sign_ticket, record_print, acknowledge_print,
    mark_print_lost, register_paper_ticket, amend, report,
    greedy_nearest, optimal_matching, check_pair,
    _calibration_maps, _gate_maps, RULES_VERSION,
)

FIXTURES = Path(__file__).parents[1] / 'fixtures'
CASE = 'bridge-A-2026-04-08'
CONFIRMED_TICKET = f'{CASE}:r0:camera_plate:G1'


def _seed(tmp_path, contract=None, name='contract.json'):
    db = tmp_path / 'site.db'
    init(db)
    path = tmp_path / name
    payload = json.loads((FIXTURES / 'weighbridge-contract.json').read_text(encoding='utf-8')) \
        if contract is None else contract
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    register(db, path)
    return db, payload


def _trap_contract(identity='camera_plate'):
    """同构四读数合同；相机链一对一、RFID 链上 G1 拥有两个同拖挂皮重。"""
    base = {
        'case_id': 'trap-case', 'plan_id': 'p1', 'site_point': 'A',
        'weight_unit': 'kilograms',
        'confirmed_identity_contract': identity,
        'identity_contracts': [identity],
        'max_cycle_minutes': 30,
        'material_price_cents_per_1000kg': 1000,
        'calibrations': [{
            'scale_id': 'WB-1', 'calibration_version': 'cal-1',
            'valid_from': '2026-01-01T00:00:00+08:00',
            'valid_to': '2027-01-01T00:00:00+08:00', 'state': 'valid',
            'evidence_hash': 'sha256:syn'}],
        'gate_events': [
            {'read_id': 'G1', 'direction': 'in', 'at': '2026-04-08T09:00:00+08:00'},
            {'read_id': 'G2', 'direction': 'in', 'at': '2026-04-08T09:05:00+08:00'},
            {'read_id': 'T1', 'direction': 'out', 'at': '2026-04-08T09:10:00+08:00'},
            {'read_id': 'T2', 'direction': 'out', 'at': '2026-04-08T09:20:00+08:00'}],
    }

    def read(rid, kind, at, kg):
        return {'id': rid, 'kind': kind, 'at': at, 'kg': kg,
                'camera_plate': None, 'camera_confidence': 0.99,
                'rfid_trailer': None, 'ticket': 'P-' + rid,
                'scale_id': 'WB-1', 'calibration_version': 'cal-1',
                'gate': 'in' if kind == 'gross' else 'out',
                'driver': 'd' + rid, 'consignee': 'c' + rid}

    base['reads'] = [
        read('G1', 'gross', '2026-04-08T09:00:00+08:00', 14000),
        read('G2', 'gross', '2026-04-08T09:05:00+08:00', 13000),
        read('T1', 'tare', '2026-04-08T09:10:00+08:00', 5000),
        read('T2', 'tare', '2026-04-08T09:20:00+08:00', 6000),
    ]
    return base


def _rfid_trap_contract():
    """RFID 身份链上的真实陷阱。

    车头在 T1 称重前更换拖挂：T1 设备读到 G1 的挂 TRL-A，而随车返回的
    G2 挂 TRL-B 由现场扫描旁路证据 ``edge_evidence`` 记录，使 G2->T1 同属
    RFID 身份依据下的合法边（不引入相机字段，不混用身份）。于是
    G1 可配 T1/T2、G2 只可配 T1，T1 时差更近，完整复刻 matching-trap。
    """
    c = _trap_contract('rfid_trailer')
    c['case_id'] = 'rfid-trap'
    rfid = {'G1': 'TRL-A', 'G2': 'TRL-B', 'T1': 'TRL-A', 'T2': 'TRL-A'}
    for r in c['reads']:
        r['rfid_trailer'] = rfid[r['id']]
    c['edge_evidence'] = [
        {'gross': 'G2', 'tare': 'T1', 'identity_contract': 'rfid_trailer',
         'identity_value': 'TRL-B', 'confidence': 0.93,
         'reason': 't1_gate_scan_returned_trailer_tag',
         'evidence_hash': 'sha256:gate-scan-synthetic'}]
    return c


# --------------------------------------------------------------------------- #
# 原始采录与反例输入
# --------------------------------------------------------------------------- #
def test_weighbridge_case_and_matching_trap(tmp_path):
    db, _ = _seed(tmp_path)
    trap = json.loads((FIXTURES / 'matching-trap.json').read_text(encoding='utf-8'))
    assert len(trap['nearest_first_result']) == 1
    assert len(trap['complete_matching']) == 2
    con = connect(db)
    assert con.execute('SELECT source_sha256 FROM weigh_case').fetchone()[0]
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0] == 0
    con.close()


def test_register_freezes_raw_bytes_and_revalidates_point(tmp_path):
    db = tmp_path / 's.db'
    init(db)
    bad = json.loads((FIXTURES / 'weighbridge-contract.json').read_text())
    bad['site_point'] = 'Z'
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps(bad), encoding='utf-8')
    with pytest.raises(ValueError, match='unknown_site_point'):
        register(db, p)
    register(db, FIXTURES / 'weighbridge-contract.json')
    con = connect(db)
    row = con.execute('SELECT contract_json FROM weigh_case').fetchone()
    assert json.loads(row[0])['reads'][0]['kg'] == 14000
    con.close()


def test_register_rejects_unknown_confirmed_contract(tmp_path):
    db, payload = _seed(tmp_path)
    payload['confirmed_identity_contract'] = 'fingerprint'
    p = tmp_path / 'c2.json'
    p.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='confirmed_contract_not_signed'):
        register(db, p)


def test_register_rejects_capacity_and_duplicate_ticket(tmp_path):
    db, payload = _seed(tmp_path)
    payload['reads'] = payload['reads'] + [
        dict(payload['reads'][0], id=f'G{i}', ticket=f'RAW-G{i}') for i in range(12)]
    p = tmp_path / 'cap.json'
    p.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='offline_capacity_exceeded'):
        register(db, p)
    dup = json.loads((FIXTURES / 'weighbridge-contract.json').read_text())
    dup['reads'][1]['ticket'] = 'RAW-G1'
    p2 = tmp_path / 'dup.json'
    p2.write_text(json.dumps(dup), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate_paper_ticket'):
        register(db, p2)


# --------------------------------------------------------------------------- #
# 纯引擎：贪心反例、完整证明、并列稳定
# --------------------------------------------------------------------------- #
def _trap_edges():
    return [
        {'gross': 'G1', 'tare': 'T1', 'gross_at': 0, 'tare_at': 10,
         'delta_minutes': 10, 'confidence': 1.0},
        {'gross': 'G1', 'tare': 'T2', 'gross_at': 0, 'tare_at': 20,
         'delta_minutes': 20, 'confidence': 1.0},
        {'gross': 'G2', 'tare': 'T1', 'gross_at': 5, 'tare_at': 10,
         'delta_minutes': 5, 'confidence': 1.0},
    ]


def test_greedy_nearest_falls_into_trap():
    assert greedy_nearest(_trap_edges()) == [['G1', 'T1']]


def test_optimal_matching_proves_global_completion():
    result = optimal_matching(_trap_edges())
    assert result['pairs'] == [['G1', 'T2'], ['G2', 'T1']]
    assert result['ticket_count'] == 2
    assert result['optimal_matching_count'] == 1
    assert result['states_evaluated'] >= result['ticket_count']


def test_optimal_matching_breaks_ties_deterministically():
    # 完全同构的两条边：可信度/时差全并列，字典序稳定选 T1
    edges = [
        {'gross': 'G1', 'tare': 'T1', 'gross_at': 0, 'tare_at': 10,
         'delta_minutes': 10, 'confidence': 0.9},
        {'gross': 'G1', 'tare': 'T2', 'gross_at': 0, 'tare_at': 10,
         'delta_minutes': 10, 'confidence': 0.9},
    ]
    first = optimal_matching(edges)
    second = optimal_matching(edges)
    assert first['pairs'] == [['G1', 'T1']]
    assert first == second
    # 完全并列时由配对 ID 字典序稳定裁定，输出唯一确定，不随运行抖动
    assert first['optimal_matching_count'] == 1
    assert first['tie_break_order'][-1] == 'lexicographic_pair_ids'


def test_confidence_outranks_time_delta():
    edges = [
        {'gross': 'G1', 'tare': 'T1', 'gross_at': 0, 'tare_at': 10,
         'delta_minutes': 10, 'confidence': 0.80},
        {'gross': 'G1', 'tare': 'T2', 'gross_at': 0, 'tare_at': 12,
         'delta_minutes': 12, 'confidence': 0.99},
    ]
    assert optimal_matching(edges)['pairs'] == [['G1', 'T2']]


def test_engine_handles_full_12x12_offline_capacity():
    edges = [
        {'gross': f'G{i}', 'tare': f'T{j}', 'gross_at': i, 'tare_at': 12 + j,
         'delta_minutes': 12 + j - i, 'confidence': 1.0}
        for i in range(12) for j in range(12)]
    result = optimal_matching(edges)
    assert result['ticket_count'] == 12
    assert result['states_evaluated'] <= 13 * 4096


def test_end_to_end_global_greedy_trap_rfid(tmp_path):
    db = tmp_path / 's.db'
    init(db)
    p = tmp_path / 'rfid-trap.json'
    p.write_text(json.dumps(_rfid_trap_contract()), encoding='utf-8')
    register(db, p)
    settle(db, 'rfid-trap')
    con = connect(db)
    proof = json.loads(con.execute(
        "SELECT proof_json FROM wb_derivation WHERE identity_contract='rfid_trailer'").fetchone()[0])
    assert proof['greedy_nearest']['pairs'] == [['G1', 'T1']]
    assert proof['selected_pairs'] == [['G1', 'T2'], ['G2', 'T1']]
    assert proof['complete'] is True
    con.close()


# --------------------------------------------------------------------------- #
# 双身份合同：各自完整推演，只确认签约链，票内不混用
# --------------------------------------------------------------------------- #
def test_two_identity_chains_both_complete_but_only_one_confirmed(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    con = connect(db)
    chains = {r[0]: (r[1], json.loads(r[2])) for r in con.execute(
        'SELECT identity_contract,confirmed,proof_json FROM wb_derivation')}
    assert set(chains) == {'camera_plate', 'rfid_trailer'}
    assert chains['camera_plate'][0] == 1
    assert chains['rfid_trailer'][0] == 0
    assert chains['camera_plate'][1]['selected_pairs'] == [['G1', 'T1'], ['G2', 'T2']]
    assert chains['rfid_trailer'][1]['selected_pairs'] == [['G1', 'T2'], ['G2', 'T1']]
    con.close()


def test_identity_chains_shift_payment_by_one_ton_each(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    con = connect(db)
    rows = {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
        'SELECT d.identity_contract,t.gross_read_id,t.net_kg,t.payment_cents '
        'FROM wb_ticket t JOIN wb_derivation d ON t.derivation_id=d.id')}
    assert rows[('camera_plate', 'G1')] == (9000, 9000)
    assert rows[('camera_plate', 'G2')] == (7000, 7000)
    assert rows[('rfid_trailer', 'G1')] == (8000, 8000)
    assert rows[('rfid_trailer', 'G2')] == (8000, 8000)
    totals = con.execute(
        "SELECT d.identity_contract,SUM(t.net_kg),SUM(t.payment_cents) "
        "FROM wb_ticket t JOIN wb_derivation d ON t.derivation_id=d.id GROUP BY 1").fetchall()
    assert {tuple(r) for r in totals} == {('camera_plate', 16000, 16000),
                                           ('rfid_trailer', 16000, 16000)}
    con.close()


def test_ticket_chain_uses_single_identity_and_binds_raw_bytes(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    con = connect(db)
    for row in con.execute('SELECT match_chain_json FROM wb_ticket'):
        chain = json.loads(row[0])
        used = chain['identity_contract']
        other = 'rfid_trailer' if used == 'camera_plate' else 'camera_plate'
        assert chain['gross'][used] and chain['tare'][used]
        # 另一身份字段只作为原始读数留档，不参与链上相等性
        assert chain['identity_value'] == chain['gross'][used] == chain['tare'][used]
        assert chain['gross']['read_sha256'] and chain['tare']['read_sha256']
        assert chain['gross']['calibration_version'] == chain['tare']['calibration_version']
        assert chain['case_source_sha256']
        assert other in chain['gross']  # 原始字段保留但未用于配对
    con.close()


def test_comparison_chain_cannot_be_signed(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    with pytest.raises(ValueError, match='ticket_not_confirmed_identity_chain'):
        sign_ticket(db, f'{CASE}:r0:rfid_trailer:G1', 'd', 'c',
                    '2026-04-08T10:00:00+08:00')


# --------------------------------------------------------------------------- #
# 时间窗边界、负净重、缺失身份、校准、闸门、纸票
# --------------------------------------------------------------------------- #
def _pair_env(contract):
    return _calibration_maps(contract), _gate_maps(contract)


def test_time_window_boundary_inclusive_and_exclusive(tmp_path):
    db, payload = _seed(tmp_path)
    cals, gates = _pair_env(payload)
    g = next(r for r in payload['reads'] if r['id'] == 'G1')
    t = next(r for r in payload['reads'] if r['id'] == 'T1')
    t['at'] = '2026-04-08T09:30:00+08:00'  # 恰好 30 分钟
    gates['T1']['at'] = t['at']
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert ok and reason is None
    t['at'] = '2026-04-08T09:30:01+08:00'
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert not ok and reason == 'outside_contract_window'
    t['at'] = '2026-04-08T08:59:00+08:00'
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert not ok and reason == 'tare_before_gross'


def test_negative_and_zero_net_weight_are_disputes(tmp_path):
    db, payload = _seed(tmp_path)
    cals, gates = _pair_env(payload)
    g = next(r for r in payload['reads'] if r['id'] == 'G1')
    t = next(r for r in payload['reads'] if r['id'] == 'T1')
    t['kg'] = g['kg']
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert reason == 'non_positive_net_weight' and not ok
    t['kg'] = g['kg'] + 1
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert reason == 'non_positive_net_weight'


def test_missing_identity_and_blurred_plate(tmp_path):
    db, payload = _seed(tmp_path)
    cals, gates = _pair_env(payload)
    g = dict(next(r for r in payload['reads'] if r['id'] == 'G1'))
    t = dict(next(r for r in payload['reads'] if r['id'] == 'T1'))
    g2 = dict(g); g2['camera_plate'] = None
    ok, reason, _ = check_pair(g2, t, 'camera_plate', payload, cals, gates)
    assert reason == 'identity_missing'
    g3 = dict(g); g3['camera_confidence'] = 0.40
    ok, reason, _ = check_pair(g3, t, 'camera_plate', payload, cals, gates)
    assert reason == 'plate_blurred'
    t_blur = dict(t); t_blur['camera_confidence'] = 0.40
    ok, reason, _ = check_pair(g, t_blur, 'camera_plate', payload, cals, gates)
    assert reason == 'plate_blurred'
    # 模糊相机不影响 RFID 链合法性（两读数的拖挂 RFID 一致）
    g_rfid = dict(g3); g_rfid['rfid_trailer'] = t['rfid_trailer']
    ok, reason, _ = check_pair(g_rfid, t, 'rfid_trailer', payload, cals, gates)
    assert ok


def test_calibration_mismatch_and_expiry(tmp_path):
    db, payload = _seed(tmp_path)
    cals, gates = _pair_env(payload)
    g = dict(next(r for r in payload['reads'] if r['id'] == 'G1'))
    t = dict(next(r for r in payload['reads'] if r['id'] == 'T1'))
    t['calibration_version'] = 'cal-old'
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert reason == 'calibration_version_differs'
    payload['calibrations'][0]['state'] = 'revoked'
    ok, reason, _ = check_pair(g,
                               next(r for r in payload['reads'] if r['id'] == 'T1'),
                               'camera_plate', payload, cals, gates)
    assert reason == 'calibration_invalid'


def test_gate_record_must_show_in_then_out(tmp_path):
    db, payload = _seed(tmp_path)
    cals, gates = _pair_env(payload)
    g = next(r for r in payload['reads'] if r['id'] == 'G1')
    t = dict(next(r for r in payload['reads'] if r['id'] == 'T1'))
    t['gate'] = 'in'
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert reason == 'gate_record_inconsistent'
    del gates['T1']
    ok, reason, _ = check_pair(g, t, 'camera_plate', payload, cals, gates)
    assert reason == 'gate_record_inconsistent'


def test_missing_rfid_identity_blocks_tickets_and_opens_disputes(tmp_path):
    contract = _trap_contract('rfid_trailer')
    contract['case_id'] = 'rfid-missing'
    for r in contract['reads']:
        r['rfid_trailer'] = None  # 全部缺拖挂 RFID
    db = tmp_path / 's.db'
    init(db)
    p = tmp_path / 'c.json'
    p.write_text(json.dumps(contract), encoding='utf-8')
    register(db, p)
    settle(db, 'rfid-missing')
    con = connect(db)
    assert con.execute('SELECT count(*) FROM wb_ticket').fetchone()[0] == 0
    subjects = {r[0] for r in con.execute(
        "SELECT subject_read_id FROM wb_dispute WHERE code='identity_missing'")}
    assert subjects == {'G1', 'G2', 'T1', 'T2'}
    assert con.execute(
        "SELECT count(*) FROM wb_dispute WHERE code='unmatched_gross'").fetchone()[0] == 2
    con.close()


def test_time_window_boundary_settles_at_limit_but_not_beyond(tmp_path):
    # 恰好 30 分钟边界仍出票；超出后该边被拒并产生争议
    contract = _trap_contract('camera_plate')
    contract['case_id'] = 'window-edge'
    plates = {'G1': 'PA', 'G2': 'PB', 'T1': 'PA', 'T2': 'PB'}
    for r in contract['reads']:
        r['camera_plate'] = plates[r['id']]
    at = {r['id']: r for r in contract['reads']}
    at['T1']['at'] = '2026-04-08T09:30:00+08:00'  # G1 09:00 -> 恰 30 分钟
    contract['gate_events'] = [
        {'read_id': 'G1', 'direction': 'in', 'at': '2026-04-08T09:00:00+08:00'},
        {'read_id': 'G2', 'direction': 'in', 'at': '2026-04-08T09:05:00+08:00'},
        {'read_id': 'T1', 'direction': 'out', 'at': '2026-04-08T09:30:00+08:00'},
        {'read_id': 'T2', 'direction': 'out', 'at': '2026-04-08T09:20:00+08:00'}]
    db = tmp_path / 's.db'
    init(db)
    p = tmp_path / 'c.json'
    p.write_text(json.dumps(contract), encoding='utf-8')
    register(db, p)
    settle(db, 'window-edge')
    con = connect(db)
    proof = json.loads(con.execute(
        "SELECT proof_json FROM wb_derivation").fetchone()[0])
    assert ['G1', 'T1'] in proof['selected_pairs']
    assert all(r['reason'] != 'outside_contract_window'
               for r in proof['rejected_pairs'] if r['tare'] == 'T1')
    con.close()
    # 越过边界一秒
    at['T1']['at'] = '2026-04-08T09:30:01+08:00'
    contract['gate_events'][2]['at'] = at['T1']['at']
    db2 = tmp_path / 's2.db'
    init(db2)
    p2 = tmp_path / 'c2.json'
    p2.write_text(json.dumps(contract), encoding='utf-8')
    register(db2, p2)
    settle(db2, 'window-edge')
    con2 = connect(db2)
    proof2 = json.loads(con2.execute(
        "SELECT proof_json FROM wb_derivation").fetchone()[0])
    assert any(r['gross'] == 'G1' and r['tare'] == 'T1'
               and r['reason'] == 'outside_contract_window'
               for r in proof2['rejected_pairs'])
    con2.close()


def test_unmatched_reads_become_disputes(tmp_path):
    # 独立数据库：_seed 已注册原合同，register 不可变不能覆盖
    db = tmp_path / 'u.db'
    init(db)
    payload = json.loads((FIXTURES / 'weighbridge-contract.json').read_text(encoding='utf-8'))
    # G2 的相机牌改成无任何皮重匹配
    for r in payload['reads']:
        if r['id'] == 'G2':
            r['camera_plate'] = 'PLATE-GHOST'
    p = tmp_path / 'c.json'
    p.write_text(json.dumps(payload), encoding='utf-8')
    register(db, p)
    settle(db, CASE)
    con = connect(db)
    disputes = con.execute(
        "SELECT code,subject_read_id FROM wb_dispute WHERE identity_contract='camera_plate'").fetchall()
    codes = {d[0] for d in disputes}
    assert 'unmatched_gross' in codes
    assert any(d[1] == 'G2' for d in disputes)
    con.close()


def test_two_gross_competing_one_tare_resolves_without_sharing(tmp_path):
    contract = _rfid_trap_contract()
    # 去掉 T2：G1/G2 争 T1，皮重不可共享，只有一车能出票，另一车进争议
    contract['reads'] = [r for r in contract['reads'] if r['id'] != 'T2']
    contract['gate_events'] = [g for g in contract['gate_events'] if g['read_id'] != 'T2']
    db = tmp_path / 's.db'
    init(db)
    p = tmp_path / 'one-tare.json'
    p.write_text(json.dumps(contract), encoding='utf-8')
    register(db, p)
    settle(db, 'rfid-trap')
    con = connect(db)
    tickets = con.execute('SELECT gross_read_id FROM wb_ticket').fetchall()
    assert len(tickets) == 1  # T1 只配一次
    assert con.execute("SELECT count(*) FROM wb_dispute WHERE code='unmatched_gross'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM wb_dispute WHERE code='unmatched_tare'").fetchone()[0] == 0
    con.close()


# --------------------------------------------------------------------------- #
# 签收、打印、ACK 丢失、重印、未知纸票、重复回执
# --------------------------------------------------------------------------- #
def _signed_ticket(db):
    settle(db, CASE)
    sign_ticket(db, CONFIRMED_TICKET, '司机甲', '收货方华北',
                '2026-04-08T10:00:00+08:00')
    return CONFIRMED_TICKET


def test_sign_flow_locks_ticket_and_blocks_resign(tmp_path):
    db, _ = _seed(tmp_path)
    _signed_ticket(db)
    with pytest.raises(ValueError, match='ticket_state_allows_no_resign'):
        sign_ticket(db, CONFIRMED_TICKET, 'x', 'y', '2026-04-08T10:05:00+08:00')
    con = connect(db)
    row = con.execute('SELECT state,driver,consignee,signed_at FROM wb_ticket WHERE id=?',
                      (CONFIRMED_TICKET,)).fetchone()
    assert tuple(row) == ('signed', '司机甲', '收货方华北', '2026-04-08T10:00:00+08:00')
    con.close()


def test_sign_failure_is_retryable(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    sign_ticket(db, CONFIRMED_TICKET, '司机甲', '收货方华北',
                '2026-04-08T10:00:00+08:00', success=False, note='司机离场')
    con = connect(db)
    assert con.execute('SELECT state FROM wb_ticket').fetchone()[0] in (
        'sign_failed', 'confirmed')
    con.close()
    # 失败后可重新签收
    sign_ticket(db, CONFIRMED_TICKET, '司机甲', '收货方华北',
                '2026-04-08T10:02:00+08:00')
    assert connect(db).execute('SELECT state FROM wb_ticket WHERE id=?',
                               (CONFIRMED_TICKET,)).fetchone()[0] == 'signed'


def test_print_ack_lost_and_reprint_never_creates_second_valid_ticket(tmp_path):
    db, _ = _seed(tmp_path)
    _signed_ticket(db)
    job1 = record_print(db, CONFIRMED_TICKET, 'PRN-7',
                        '2026-04-08T10:01:00+08:00', 'PAPER-001')
    # 纸票在司机手上，禁止再出一张
    with pytest.raises(ValueError, match='paper_ticket_already_in_driver_hands'):
        record_print(db, CONFIRMED_TICKET, 'PRN-7',
                     '2026-04-08T10:02:00+08:00', 'PAPER-002')
    # 现场扫描核对 ACK 丢失
    mark_print_lost(db, job1, 'SCN-2', '中心 ACK 丢失，现场未见回执')
    with pytest.raises(ValueError, match='reprint_requires'):
        record_print(db, CONFIRMED_TICKET, 'PRN-7',
                     '2026-04-08T10:03:00+08:00', 'PAPER-003')  # 未挂原作业
    job2 = record_print(db, CONFIRMED_TICKET, 'PRN-7',
                        '2026-04-08T10:04:00+08:00', 'PAPER-003', reprint_of=job1)
    acknowledge_print(db, job2, '2026-04-08T10:05:00+08:00', 'SCN-2')
    # 重复回执被拒
    with pytest.raises(ValueError, match='duplicate_acknowledgement'):
        acknowledge_print(db, job2, '2026-04-08T10:06:00+08:00', 'SCN-2')
    con = connect(db)
    states = {r[0]: r[1] for r in con.execute('SELECT id,state FROM wb_print_job')}
    assert states[job1] == 'lost' and states[job2] == 'acked'
    assert con.execute('SELECT count(*) FROM wb_ticket WHERE state="signed"').fetchone()[0] == 1
    con.close()


def test_unknown_paper_ticket_enters_verification(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    register_paper_ticket(db, CASE, 'PAPER-UNKNOWN',
                          '2026-04-08T11:00:00+08:00', 'SCN-9')
    con = connect(db)
    row = con.execute("SELECT code,state FROM wb_dispute WHERE code='unknown_paper_ticket'").fetchone()
    assert tuple(row) == ('unknown_paper_ticket', 'pending_verification')
    with pytest.raises(ValueError, match='paper_ticket_already_known'):
        register_paper_ticket(db, CASE, 'PAPER-UNKNOWN',
                              '2026-04-08T11:05:00+08:00', 'SCN-9')
    con.close()


def test_paper_ticket_number_is_unique_per_case(tmp_path):
    db, _ = _seed(tmp_path)
    _signed_ticket(db)
    record_print(db, CONFIRMED_TICKET, 'PRN-7',
                 '2026-04-08T10:01:00+08:00', 'PAPER-001')
    other = f'{CASE}:r0:camera_plate:G2'
    sign_ticket(db, other, '司机乙', '收货方华东', '2026-04-08T10:02:00+08:00')
    with pytest.raises(ValueError, match='paper_ticket_no_duplicate'):
        record_print(db, other, 'PRN-7',
                     '2026-04-08T10:03:00+08:00', 'PAPER-001')


# --------------------------------------------------------------------------- #
# 更正：原票保留、替代关系、差额单
# --------------------------------------------------------------------------- #
def test_amend_tare_keeps_signed_ticket_and_emits_delta(tmp_path):
    db, _ = _seed(tmp_path)
    _signed_ticket(db)
    amend(db, CASE, 'amend-1', '皮重 T1 更正为 4000kg',
          '2026-04-09T09:00:00+08:00',
          read_updates={'T1': {'kg': 4000}})
    con = connect(db)
    old = con.execute('SELECT * FROM wb_ticket WHERE id=?', (CONFIRMED_TICKET,)).fetchone()
    assert old['state'] == 'superseded'
    assert old['net_kg'] == 9000  # 已签收净重未被重写
    new = con.execute(
        "SELECT * FROM wb_ticket WHERE revision=1 AND gross_read_id='G1' AND state='confirmed'").fetchone()
    assert new['net_kg'] == 10000 and new['payment_cents'] == 10000
    repl = con.execute("SELECT * FROM wb_revision WHERE relation='replacement'").fetchone()
    delta = con.execute("SELECT * FROM wb_revision WHERE relation='delta'").fetchone()
    assert repl['old_ticket_id'] == old['id'] and repl['new_ticket_id'] == new['id']
    assert delta['delta_net_kg'] == 1000 and delta['delta_payment_cents'] == 1000
    # 新票需重新签收，旧票仍可审计
    assert new['state'] == 'confirmed'
    assert con.execute('SELECT count(*) FROM wb_ticket').fetchone()[0] >= 5
    con.close()


def test_amend_trailer_swap_rebuilds_chains_and_keeps_evidence(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    before = connect(db).execute(
        "SELECT proof_json FROM wb_derivation WHERE revision=0 AND identity_contract='rfid_trailer'").fetchone()[0]
    amend(db, CASE, 'amend-swap', '车头更换拖挂，G1/G2 的 RFID 交换',
          '2026-04-09T10:00:00+08:00',
          read_updates={
              'G1': {'rfid_trailer': 'TRL-1'},
              'G2': {'rfid_trailer': 'TRL-2'}})
    con = connect(db)
    after = json.loads(con.execute(
        "SELECT proof_json FROM wb_derivation WHERE revision=1 AND identity_contract='rfid_trailer'").fetchone()[0])
    assert json.loads(before)['selected_pairs'] != after['selected_pairs']
    assert after['selected_pairs'] == [['G1', 'T1'], ['G2', 'T2']]
    assert con.execute('SELECT count(*) FROM wb_amendment').fetchone()[0] == 1
    assert con.execute('SELECT count(*) FROM wb_derivation WHERE revision=0').fetchone()[0] == 2
    con.close()


def test_amend_calibration_revision_does_not_touch_signed_net(tmp_path):
    db, _ = _seed(tmp_path)
    _signed_ticket(db)
    amend(db, CASE, 'amend-cal', '磅秤 WB-1 启用新校准版本',
          '2026-04-09T11:00:00+08:00',
          read_updates={r: {'calibration_version': 'cal-2026Q2-1'}
                        for r in ('G1', 'G2', 'T1', 'T2')},
          calibration_updates=[{
              'scale_id': 'WB-1', 'calibration_version': 'cal-2026Q2-1',
              'valid_from': '2026-04-01T00:00:00+08:00',
              'valid_to': '2027-04-01T00:00:00+08:00',
              'state': 'valid', 'evidence_hash': 'sha256:cal-q2'}])
    con = connect(db)
    assert con.execute(
        "SELECT net_kg FROM wb_ticket WHERE id=? AND state='superseded'",
        (CONFIRMED_TICKET,)).fetchone()[0] == 9000
    chain = json.loads(con.execute(
        "SELECT match_chain_json FROM wb_ticket WHERE revision=1 AND gross_read_id='G1'").fetchone()[0])
    assert chain['gross']['calibration_version'] == 'cal-2026Q2-1'
    con.close()


# --------------------------------------------------------------------------- #
# 幂等、确定性、报告与既有巡检覆盖不变
# --------------------------------------------------------------------------- #
def test_settle_is_idempotent_and_deterministic(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    settle(db, CASE)
    con = connect(db)
    assert con.execute('SELECT count(*) FROM wb_derivation').fetchone()[0] == 2
    assert con.execute('SELECT count(*) FROM wb_ticket').fetchone()[0] == 4
    con.close()
    out1 = report(db, CASE, tmp_path / 'r1.json')
    out2 = report(db, CASE, tmp_path / 'r2.json')
    assert out1.read_bytes() == out2.read_bytes()
    assert out1.with_suffix('.md').read_text().startswith('# 地磅材料净重结算单')


def test_report_contains_per_ticket_evidence_not_just_totals(tmp_path):
    db, _ = _seed(tmp_path)
    settle(db, CASE)
    out = report(db, CASE, tmp_path / 'weigh.json')
    data = json.loads(out.read_text(encoding='utf-8'))
    assert len(data['tickets']) == 4
    for t in data['tickets']:
        assert t['match_chain']['gross']['read_sha256']
        assert t['match_chain']['tare']['read_sha256']
        assert t['match_chain']['passed_checks']
    md = out.with_suffix('.md').read_text(encoding='utf-8')
    assert '逐票推演证明' in md and '争议单' in md
    assert RULES_VERSION in md


def test_inspection_coverage_and_earthwork_remain_unchanged(tmp_path):
    from sitewalk.__main__ import demo
    path = demo(tmp_path / 'inspection')
    report_data = json.loads(path.read_text(encoding='utf-8'))
    assert report_data['decision'][0]['result'] == '覆盖'
    # 巡检库不携带任何过磅表即视为未受影响
    con = connect(tmp_path / 'inspection' / 'sitewalk.db')
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'wb_ticket' not in tables
    con.close()
