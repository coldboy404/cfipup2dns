import errno
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from test_runner import runner


def good(ms=100):
    return {'attempts': 3, 'successes': 3, 'total_ms': ms}


class SelectionTests(unittest.TestCase):
    def test_five_to_ten_backfills_without_replacing_existing_members(self):
        old = [f'192.0.2.{i}' for i in range(1, 6)]
        candidates = [{'ip': f'192.0.2.{i}'} for i in range(1, 11)]
        checks = {row['ip']: good(10 if row['ip'] in old else 500) for row in candidates}
        with patch.object(runner, 'TOP_N', 10):
            desired, reason, _, changed = runner.decide_switch('a', 'A', old, candidates,
                checks, {'last_switch_at': 99}, 100)
        self.assertEqual(len(desired), 10)
        self.assertEqual(desired[:5], old)
        self.assertTrue(changed)
        self.assertIn('expanded', reason)

    def test_expand_bypasses_cooldown_and_benefit_only_verified_candidates(self):
        for previous in ({}, {'last_switch_at': 99}):
            with self.subTest(previous=previous), patch.object(runner, 'TOP_N', 3):
                checks = {'old': good(10), 'slow': good(500), 'fast': good(20),
                          'partial': {'attempts': 3, 'successes': 2, 'total_ms': 1}}
                desired, reason, _, changed = runner.decide_switch('a', 'A', ['old'],
                    [{'ip': ip} for ip in ['partial', 'slow', 'fast']], checks, previous, 100)
                self.assertEqual(set(desired), {'old', 'slow', 'fast'})
                self.assertTrue(changed)
                self.assertIn('expanded', reason)

    def test_expand_keeps_uncertain_and_failure_wait_incumbents(self):
        for old_check in ({'attempts': 3, 'successes': 1}, {'attempts': 3, 'successes': 0}, {}):
            with self.subTest(old_check=old_check), patch.object(runner, 'TOP_N', 2):
                desired, reason, _, changed = runner.decide_switch('a', 'A', ['old'],
                    [{'ip': 'new'}], {'old': old_check, 'new': good()}, {}, 100)
                self.assertEqual(desired, ['old', 'new'])
                self.assertTrue(changed)
                self.assertIn('expanded', reason)

    def test_shrink_changes_membership_but_never_drops_protected_old_ips(self):
        cases = [({'a': good(), 'b': good(), 'c': good()}, ['a', 'b'], True, 'shrunk'),
                 ({'a': good(), 'b': {}, 'c': {'attempts': 3, 'successes': 0}}, ['b', 'c'], True, 'shrunk'),
                 ({'a': {}, 'b': {}, 'c': {}}, ['a', 'b', 'c'], False, 'deferred')]
        for checks, expected, changed, reason_part in cases:
            with self.subTest(checks=checks), patch.object(runner, 'TOP_N', 2):
                result = runner.decide_switch('a', 'A', ['a', 'b', 'c'], [], checks, {}, 100)
                self.assertEqual(result[0], expected)
                self.assertEqual(result[3], changed)
                self.assertIn(reason_part, result[1])

    def test_download_failure_bytes_do_not_rank_or_pass_speed_filter(self):
        rows = [dict(ip='192.0.2.1', ok=True, score_ms=1, download_ok=False, download_mbps=999),
                dict(ip='192.0.2.2', ok=True, score_ms=2, download_ok=True, download_mbps=10),
                dict(ip='192.0.2.3', ok=True, score_ms=3),
                dict(ip='192.0.2.4', ok=True, score_ms=4, download_mbps=5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            mode, result = runner.parse_json_lines(path)
        self.assertEqual(mode, 'download_mbps')
        self.assertEqual([row['ip'] for row in result], ['192.0.2.2', '192.0.2.4', '192.0.2.1', '192.0.2.3'])
        failed = next(row for row in result if row['ip'] == '192.0.2.1')
        self.assertFalse(failed['download_ok'])
        self.assertFalse(failed.get('download_mbps'))
        self.assertNotIn('download_ok', result[-1])
        with patch.object(runner, 'MIN_DOWNLOAD_MBPS', 1):
            self.assertEqual([r['ip'] for r in runner.apply_result_filters(result)], ['192.0.2.2', '192.0.2.4'])

    def test_pool_filters_can_backfill_without_claiming_download_measured(self):
        rows = [dict(ip='192.0.2.1', ok=True, score_ms=1, colo='SJC', download_ok=True, download_mbps=10),
                dict(ip='192.0.2.2', ok=True, score_ms=2, colo='HKG')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            _, result = runner.parse_json_lines(path)
        with patch.object(runner, 'CF_COLO', 'HKG'), patch.object(runner, 'MIN_DOWNLOAD_MBPS', 0):
            filtered = runner.apply_result_filters(result)
            self.assertEqual([row['ip'] for row in filtered], ['192.0.2.2'])
            self.assertNotIn('download_mbps', filtered[0])

    def test_download_candidate_target_and_explicit_disable(self):
        for configured, expected in [('5', 30), ('40', 40), ('0', 0), ('20000', 10000)]:
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'TOP_N', 10), \
                    patch.object(runner, 'TOP_TEST', 5), patch.object(runner, 'DOWNLOAD_TOP', configured), \
                    patch.object(runner, 'SCAN_ONLY', True), patch.object(runner.subprocess, 'run') as run, \
                    patch.object(runner, 'parse_json_lines', return_value=('score_ms', [])):
                (Path(tmp) / 'ipv4cidr.txt').write_text('fixture')
                runner.run_mode('4', {}, 'fake')
                args = run.call_args.args[0]
                self.assertIn('-download-top', args)
                self.assertEqual(int(args[args.index('-download-top') + 1]), expected)
                self.assertGreaterEqual(int(args[args.index('-top') + 1]), max(10, expected))
                self.assertLessEqual(int(args[args.index('-top') + 1]), 10000)

    def test_ipv6_route_check_is_local_and_only_definitive_errors_stop_scan(self):
        for error, expected in [(None, True), (OSError(errno.ENETUNREACH, 'route'), False),
                                (OSError(errno.EADDRNOTAVAIL, 'source'), False),
                                (socket.timeout('remote timeout'), True), (OSError(errno.EACCES, 'unknown'), True)]:
            with self.subTest(error=error), patch.object(runner.socket, 'socket') as make:
                sock = make.return_value.__enter__.return_value
                sock.connect.side_effect = error
                self.assertEqual(runner.ipv6_network_available(), expected)
                make.assert_called_once_with(socket.AF_INET6, socket.SOCK_DGRAM)
                sock.send.assert_not_called()
                sock.recv.assert_not_called()

    def test_ipv6_unavailable_keeps_dns_and_old_results_even_in_scan_only(self):
        for scan_only in (False, True):
            with self.subTest(scan_only=scan_only), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'SCAN_ONLY', scan_only), \
                    patch.object(runner, 'ipv6_network_available', return_value=False, create=True), \
                    patch.object(runner.subprocess, 'run') as run, patch.object(runner, 'cf_request') as dns, \
                    patch.object(runner, 'update_dns_records') as sync:
                root = Path(tmp)
                (root / 'ipv6cidr.txt').write_text('fixture')
                old = root / 'best_ips_v6.json'
                old.write_text('{"result":[{"ip":"2606:4700::1111"}]}')
                result = runner.run_mode('6', {}, 'fake')
                self.assertEqual(result['status'], 'network_unavailable')
                self.assertEqual(result['reason'], 'ipv6_no_local_route_kept_old')
                self.assertEqual(result['selected_count'], 0)
                self.assertEqual(old.read_text(), '{"result":[{"ip":"2606:4700::1111"}]}')
                self.assertFalse((root / 'scan_results_v6.log').exists())
                run.assert_not_called(); dns.assert_not_called(); sync.assert_not_called()

    def test_network_unavailable_summary_is_not_success(self):
        for modes in [('6', [{'status': 'network_unavailable', 'selected_count': 0}]),
                      ('both', [{'status': 'verified', 'selected_count': 5}, {'status': 'network_unavailable', 'selected_count': 0}])]:
            with self.subTest(modes=modes[0]), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'IP_MODE', modes[0]), \
                    patch.object(runner, 'load_config', return_value={}), patch.object(runner, 'ensure_mcis'), \
                    patch.object(runner, 'run_mode', side_effect=modes[1]):
                self.assertNotEqual(runner.run_all_modes(), 0)
                summary = json.loads((Path(tmp) / 'run_history.json').read_text())[-1]
                self.assertNotIn(summary['status'], ['verified', 'scan_only'])
                self.assertEqual(summary['selected_count'], sum(r['selected_count'] for r in modes[1]))

    def test_verified_expansion_records_membership_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'TOP_N', 2), patch.object(runner, 'resolve_zone_id', return_value='zone'):
            old = dict(id='1', type='A', name='a.example', content='192.0.2.1', ttl=60, proxied=False)
            new = dict(old, id='2', content='192.0.2.2')
            checks = {'192.0.2.1': good(10), '192.0.2.2': good(500)}
            with patch.object(runner, 'cf_request_all', side_effect=[[old], [old, new]]), \
                    patch.object(runner, 'cf_request') as request, patch.object(runner, 'recheck_ips', return_value=checks):
                result = runner.update_dns_records('x', [{'domain': 'a.example'}], 'A', [{'ip': new['content']}], 60, False, 'example.com')
            self.assertEqual(result[0]['status'], 'verified')
            self.assertTrue(result[0]['switched'])
            self.assertEqual([call.args[0] for call in request.call_args_list], ['POST'])
            entry = runner.load_switch_state()['entries']['a.example|A']
            self.assertGreater(entry['last_switch_at'], 0)
            self.assertEqual(set(entry['actual_ips']), {old['content'], new['content']})

    def test_verified_shrink_deletes_only_healthy_surplus_and_records_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'TOP_N', 1), patch.object(runner, 'resolve_zone_id', return_value='zone'):
            healthy = dict(id='1', type='A', name='a.example', content='192.0.2.1', ttl=60, proxied=False)
            protected = dict(healthy, id='2', content='192.0.2.2')
            checks = {healthy['content']: good(), protected['content']: {'attempts': 3, 'successes': 1}}
            with patch.object(runner, 'cf_request_all', side_effect=[[healthy, protected], [protected]]), \
                    patch.object(runner, 'cf_request') as request, patch.object(runner, 'recheck_ips', return_value=checks):
                result = runner.update_dns_records('x', [{'domain': 'a.example'}], 'A', [{'ip': healthy['content']}], 60, False, 'example.com')
            self.assertEqual(result[0]['status'], 'verified')
            self.assertTrue(result[0]['switched'])
            self.assertIn('shrunk', result[0]['reason'])
            self.assertEqual([call.args[0] for call in request.call_args_list], ['DELETE'])
            self.assertTrue(request.call_args.args[1].endswith('/1'))
            self.assertGreater(runner.load_switch_state()['entries']['a.example|A']['last_switch_at'], 0)

    def test_scan_only_summary_reports_actual_candidate_counts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'SCAN_ONLY', True), patch.object(runner, 'TOP_N', 2), \
                patch.object(runner.subprocess, 'run') as run:
            (Path(tmp) / 'ipv4cidr.txt').write_text('fixture')
            rows = [dict(ip='192.0.2.1', ok=True, score_ms=10, download_ok=True, download_mbps=20),
                    dict(ip='192.0.2.2', ok=True, score_ms=20, download_ok=False, download_mbps=100),
                    dict(ip='192.0.2.3', ok=True, score_ms=30)]
            def fake_scan(*args, **kwargs):
                kwargs['stdout'].write('\n'.join(json.dumps(row) for row in rows))
            run.side_effect = fake_scan
            result = runner.run_mode('4', {}, 'fake')
            self.assertEqual(result['status'], 'scan_only')
            self.assertEqual(result['requested_count'], 2)
            self.assertEqual(result['candidate_count'], 3)
            self.assertEqual(result['download_success_count'], 1)
            self.assertEqual(result['selected_count'], 2)
