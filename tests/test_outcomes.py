import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_runner import runner


class OutcomeTests(unittest.TestCase):
    def _tls_probe(self, response_parts, delays=()):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        key, cert = root / 'key.pem', root / 'cert.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, capture_output=True)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(1)
        port = listener.getsockname()[1]
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        def serve():
            conn, _ = listener.accept()
            try:
                with server_context.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(4096)
                    for index, part in enumerate(response_parts):
                        if index < len(delays): time.sleep(delays[index])
                        tls.sendall(part)
            finally:
                listener.close()
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        client_context = ssl.create_default_context(cafile=str(cert))
        return tmp, thread, client_context, port

    def test_direct_https_probe_fragmented_status_and_delayed_body(self):
        parts = [b'H', b'TTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n', b'data']
        tmp, thread, context, port = self._tls_probe(parts, (0, .03, .08))
        try:
            result = runner.direct_https_probe('127.0.0.1', 'localhost', '/x', 2, context, port)
            self.assertTrue(result['ok'], result)
            self.assertGreaterEqual(result['total_ms'] - result['ttfb_ms'], 60)
            self.assertGreaterEqual(result['total_ms'], 100)
            self.assertLess(result['ttfb_ms'], result['total_ms'])
        finally:
            thread.join(2); tmp.cleanup()

    def test_direct_https_probe_rejects_redirect_and_injection(self):
        tmp, thread, context, port = self._tls_probe([b'HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n'])
        try:
            self.assertFalse(runner.direct_https_probe('127.0.0.1', 'localhost', '/', 2, context, port)['ok'])
        finally:
            thread.join(2); tmp.cleanup()
        for host, path in [('localhost\r\nX: y', '/'), ('localhost', '/x\r\nX: y')]:
            self.assertFalse(runner.direct_https_probe('127.0.0.1', host, path, .1)['ok'])

    def test_actual_mcis_schema_trace_and_timing_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            path.write_text(json.dumps({'ip': '1.1.1.1', 'ok': True, 'connect_ms': 3,
                'tls_ms': 7, 'ttfb_ms': 11, 'total_ms': 13, 'score_ms': 13,
                'trace': {'colo': 'HKG'}, 'prefix_samples': 10, 'prefix_ok': 8, 'prefix_fail': 2}))
            _, rows = runner.parse_json_lines(path)
            self.assertEqual(rows[0]['colo'], 'HKG')
            self.assertEqual([rows[0][k] for k in ('connect_ms', 'tls_ms', 'ttfb_ms', 'total_ms', 'latency_ms')], [3, 7, 11, 13, 13])
            self.assertIsNone(runner.row_loss_rate(rows[0]))

    def test_malformed_nested_trace_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            path.write_text(json.dumps({'ip': '1.1.1.1', 'ok': True, 'score_ms': 1, 'trace': 'bad', 'tls_ms': 'bad'}))
            _, rows = runner.parse_json_lines(path)
            self.assertNotIn('colo', rows[0])
            self.assertIsNone(rows[0]['tls_ms'])

    def test_missing_loss_uses_repeated_probe_not_prefix_counts(self):
        rows = [{'ip': '1.1.1.1', 'prefix_samples': 10, 'prefix_ok': 0}]
        with patch.object(runner, 'MAX_LOSS_RATE', .5), patch.object(runner, 'TOP_N', 1), \
                patch.object(runner, 'recheck_ips', return_value={'1.1.1.1': {'failure_rate': 1/3, 'attempts': 3}}), \
                patch.object(runner, 'log') as log:
            result = runner.add_per_ip_loss_rechecks(rows, 'example.com', '/cdn-cgi/trace')
        self.assertEqual(result[0]['loss_rate'], 1/3)
        self.assertIn('不代用', log.call_args.args[0])

    def test_loss_recheck_scans_past_pre_filter_rejections(self):
        rows = [{'ip': f'192.0.2.{i}', 'colo': 'SJC' if i < 4 else 'HKG', 'latency_ms': 10} for i in range(1, 7)]
        checked = {row['ip']: {'failure_rate': 0, 'attempts': 3} for row in rows}
        with patch.object(runner, 'MAX_LOSS_RATE', .5), patch.object(runner, 'TOP_N', 2), \
                patch.object(runner, 'CF_COLO', 'HKG'), patch.object(runner, 'recheck_ips', return_value=checked):
            filtered = runner.apply_result_filters(rows, skip_loss=True)
            result = runner.add_per_ip_loss_rechecks(filtered, 'example.com', '/')
        self.assertEqual([r['ip'] for r in result], ['192.0.2.4', '192.0.2.5'])

    def test_switch_policy_small_benefit_cooldown_failure_and_bootstrap(self):
        candidate = [{'ip': '2.2.2.2'}]
        checks = {'1.1.1.1': {'attempts': 3, 'successes': 3, 'total_ms': 100},
                  '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 85}}
        with patch.object(runner, 'TOP_N', 1), patch.object(runner, 'SWITCH_MIN_IMPROVEMENT', .20), patch.object(runner, 'SWITCH_MIN_INTERVAL', 100), patch.object(runner, 'SWITCH_FAILURE_THRESHOLD', 2):
            self.assertEqual(runner.decide_switch('a', 'A', ['1.1.1.1'], candidate, checks, {}, 100)[1], 'benefit_too_small')
            checks['2.2.2.2']['total_ms'] = 50
            self.assertEqual(runner.decide_switch('a', 'A', ['1.1.1.1'], candidate, checks, {'last_switch_at': 50}, 100)[1], 'cooldown')
            desired, reason, _, changed = runner.decide_switch('a', 'A', [], candidate, checks, {}, 100)
            self.assertEqual((desired, reason, changed), (['2.2.2.2'], 'bootstrap', True))
            failed = {'1.1.1.1': {'attempts': 3, 'successes': 0, 'total_ms': None}, '2.2.2.2': checks['2.2.2.2']}
            _, reason, state, changed = runner.decide_switch('a', 'A', ['1.1.1.1'], candidate, failed, {}, 100)
            self.assertFalse(changed); self.assertIn('wait_1/2', reason)
            desired, reason, _, changed = runner.decide_switch('a', 'A', ['1.1.1.1'], candidate, failed, state, 200)
            self.assertEqual((desired, reason, changed), (['2.2.2.2'], 'incumbent_failure_threshold', True))

    def test_switch_slots_preserve_overlap_and_reject_slower_second_candidate(self):
        incumbents = ['1.1.1.1', '1.0.0.1']
        candidates = [{'ip': '1.1.1.1'}, {'ip': '2.2.2.2'}, {'ip': '3.3.3.3'}]
        checks = {'1.1.1.1': {'attempts': 3, 'successes': 3, 'total_ms': 40},
                  '1.0.0.1': {'attempts': 3, 'successes': 3, 'total_ms': 100},
                  '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 50},
                  '3.3.3.3': {'attempts': 3, 'successes': 3, 'total_ms': 500}}
        with patch.object(runner, 'SWITCH_MIN_IMPROVEMENT', .2), patch.object(runner, 'TOP_N', 2):
            desired, reason, _, changed = runner.decide_switch('a', 'A', incumbents, candidates, checks, {}, 100)
        self.assertEqual((desired, reason, changed), (['1.1.1.1', '2.2.2.2'], 'minimum_improvement_met', True))

    def test_partial_failure_is_per_ip_and_identity_change_resets_counter(self):
        candidates = [{'ip': '2.2.2.2'}]
        checks = {'1.1.1.1': {'attempts': 3, 'successes': 0},
                  '1.0.0.1': {'attempts': 3, 'successes': 3, 'total_ms': 40},
                  '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 50}}
        previous = {'failure_counts': {'9.9.9.9': 99, '1.1.1.1': 1}, 'last_switch_at': 99}
        with patch.object(runner, 'SWITCH_FAILURE_THRESHOLD', 2), patch.object(runner, 'TOP_N', 2):
            desired, reason, state, changed = runner.decide_switch('a', 'A', ['1.1.1.1', '1.0.0.1'], candidates, checks, previous, 100)
        self.assertEqual((desired, reason, changed), (['2.2.2.2', '1.0.0.1'], 'incumbent_failure_threshold', True))
        self.assertNotIn('9.9.9.9', state['failure_counts'])

    def test_zero_improvement_does_not_replace_tie_and_malformed_metrics_keep(self):
        candidate = [{'ip': '2.2.2.2'}]
        with patch.object(runner, 'TOP_N', 1), patch.object(runner, 'SWITCH_MIN_IMPROVEMENT', 0):
            desired, reason, _, changed = runner.decide_switch('a', 'A', ['1.1.1.1'], candidate,
                {'1.1.1.1': {'attempts': 3, 'successes': 3, 'total_ms': 50},
                 '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 50}},
                {'failure_counts': {'1.1.1.1': 'bad'}, 'last_switch_at': 'nan'}, 100)
        self.assertEqual((desired, reason, changed), (['1.1.1.1'], 'benefit_too_small', False))

    def test_recheck_defaults_resolve_patched_globals_at_call_time(self):
        with patch.object(runner, 'SWITCH_RECHECK_ROUNDS', 2), patch.object(runner, 'SWITCH_RECHECK_TIMEOUT', .25), \
                patch.object(runner, 'direct_https_probe', return_value={'ok': False}) as probe:
            runner.recheck_ips(['1.1.1.1'], 'example.com', '/')
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args.args[-1], .25)
    def test_partial_success_breaks_consecutive_failure_streak(self):
        checks = {'1.1.1.1': {'attempts': 3, 'successes': 1, 'total_ms': 100},
                  '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 50}}
        old = {'failure_counts': {'1.1.1.1': 1}}
        with patch.object(runner, 'TOP_N', 1):
            _, reason, state, changed = runner.decide_switch('a', 'A', ['1.1.1.1'],
                [{'ip': '2.2.2.2'}], checks, old, 100)
        self.assertEqual(reason, 'recheck_uncertain_keep')
        self.assertFalse(changed)
        self.assertEqual(state['failure_counts']['1.1.1.1'], 0)

    def test_parser_finite_numeric_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan'
            rows = [dict(ip='1.1.1.1', ok=True, score_ms='12', download_mbps='garbage', latency_ms='inf', extra=float('nan')),
                    dict(ip='1.0.0.1', ok=True, score_ms='NaN', download_mbps='inf'),
                    dict(ip='bad-ip', ok=True, score_ms=1),
                    dict(ip='8.8.8.8', ok=[], score_ms=1)]
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            mode, result = runner.parse_json_lines(path)
            self.assertEqual(mode, 'score_ms')
            self.assertEqual(len(result), 1)
            self.assertIsNone(result[0]['latency_ms'])
            self.assertIsNone(result[0]['download_mbps'])
            json.dumps(result, allow_nan=False)

    def test_missing_required_metrics_fail_closed(self):
        for name, value in [('MAX_LATENCY_MS', 100), ('MIN_LATENCY_MS', 10), ('MAX_LOSS_RATE', .1), ('MIN_DOWNLOAD_MBPS', 1)]:
            with self.subTest(name=name), patch.object(runner, name, value):
                self.assertEqual(runner.apply_result_filters([{'ip': '1.1.1.1'}]), [])
        self.assertEqual(runner.row_loss_rate({'sent': 10, 'received': 0}), 1)
        self.assertIsNone(runner.row_loss_rate({'loss_rate': -1}))
        self.assertIsNone(runner.row_loss_rate({'loss_rate': 101}))

    def test_readback_mismatch_and_other_domain_continues(self):
        good = dict(id='1', type='A', name='good.example', content='1.1.1.1', ttl=60, proxied=False)
        with patch.object(runner, 'resolve_zone_id', return_value='zone'), patch.object(runner, 'cf_request_all', side_effect=[[], [], [good], [good]]), patch.object(runner, 'cf_request'):
            result = runner.update_dns_records('secret', [{'domain': 'bad.example'}, {'domain': 'good.example'}], 'A', [{'ip': '1.1.1.1'}], 60, False)
        self.assertEqual([r['status'] for r in result], ['failed', 'verified'])
        self.assertNotIn('secret', json.dumps(result))

    def test_readback_checks_ttl_proxy_duplicates_ipv6(self):
        for changes, dns_type, ip in [({'ttl': 120}, 'A', '1.1.1.1'), ({'proxied': True}, 'A', '1.1.1.1'), ({}, 'AAAA', '2606:4700::1111')]:
            record = dict(id='1', type=dns_type, name='cf.example', content=ip, ttl=60, proxied=False)
            actual = dict(record, **changes)
            if dns_type == 'AAAA':
                actual['content'] = '2606:4700:0:0:0:0:0:1111'
            with patch.object(runner, 'resolve_zone_id', return_value='zone'), patch.object(runner, 'cf_request_all', side_effect=[[record], [actual]]), patch.object(runner, 'cf_request'):
                result = runner.update_dns_records('secret', [{'domain': 'cf.example'}], dns_type, [{'ip': ip}], 60, False)
            self.assertEqual(result[0]['status'], 'verified' if dns_type == 'AAAA' else 'failed')

    def test_real_cli_scan_only_then_empty_preserves_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'montecarlo-ip-searcher'
            binary.write_text('#!/bin/sh\nif [ "$1" = "-h" ]; then echo --download-mode; else echo \'{"ip":"1.1.1.1","ok":true,"score_ms":12}\'; fi\n')
            binary.chmod(0o700)
            for version in (4, 6):
                (root / f'ipv{version}cidr.txt').write_text('test fixture\n')
            old = root / 'best_ips_v4.json'
            old.write_text('{"result":[{"ip":"1.0.0.1"}]}')
            env = dict(os.environ, PROJECT_DIR=tmp, CONFIG_FILE=str(root / 'missing-config'), SCAN_ONLY='true', IP_MODE='4')
            command = [sys.executable, str(Path(__file__).parents[1] / 'cfip_runner.py')]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads((root / 'scan_only_v4.json').read_text())['result'][0]['ip'], '1.1.1.1')
            self.assertEqual(json.loads(old.read_text())['result'][0]['ip'], '1.0.0.1')
            binary.write_text('#!/bin/sh\necho --download-mode\n')
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            history = json.loads((root / 'run_history.json').read_text())
            self.assertEqual([h['status'] for h in history], ['scan_only', 'no_new_results'])
            self.assertEqual(history[-1]['results'][0]['status'], 'kept_old')

    def test_bounded_history_and_partial_outcome(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'load_config', return_value={}), patch.object(runner, 'ensure_mcis'), patch.object(runner, 'IP_MODE', 'both'), patch.object(runner, 'SCAN_ONLY', False), patch.object(runner, 'run_mode', side_effect=[{'status': 'verified'}, {'status': 'failed'}]):
            path = Path(tmp) / 'run_history.json'
            path.write_text(json.dumps([{'status': 'old'}] * 40))
            self.assertEqual(runner.run_all_modes(), 1)
            history = json.loads(path.read_text())
            self.assertEqual(len(history), 30)
            self.assertEqual(history[-1]['status'], 'partial_failure')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_scan_only_never_calls_dns_and_partial_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'run_mcis', return_value={'scan_timed_out': False}), patch.object(runner, 'DOWNLOAD_TOP', '0'), patch.object(runner, 'parse_json_lines', return_value=('score_ms', [{'ip': '1.1.1.1', 'score_ms': 12}])), patch.object(runner, 'cf_request') as request, patch.object(runner, 'update_dns_records') as sync:
            root = Path(tmp)
            (root / 'ipv4cidr.txt').write_text('fixture')
            old = root / 'best_ips_v4.json'
            old.write_text('{"result":[]}')
            with patch.object(runner, 'SCAN_ONLY', True):
                self.assertEqual(runner.run_mode('4', {}, 'fake')['status'], 'scan_only')
            self.assertFalse((root / 'switch_state.json').exists())
            request.assert_not_called()
            sync.assert_not_called()
            sync.return_value = [{'domain': 'a.example', 'status': 'verified'}, {'domain': 'b.example', 'status': 'failed'}]
            cfg = {'cloudflare': {'token': 'secret', 'records': [{'domain': 'a.example'}, {'domain': 'b.example'}]}}
            with patch.object(runner, 'SCAN_ONLY', False):
                self.assertEqual(runner.run_mode('4', cfg, 'fake')['status'], 'partial_failure')
            self.assertEqual(old.read_text(), '{"result":[]}')

    def test_per_domain_divergence_and_failed_verification_do_not_advance_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'resolve_zone_id', return_value='zone'):
            a = dict(id='a', type='A', name='a.example', content='1.1.1.1', ttl=60, proxied=False)
            b = dict(id='b', type='A', name='b.example', content='3.3.3.3', ttl=60, proxied=False)
            reads = [[a], [a], [b], []]  # a keeps its incumbent; b readback fails
            checks = {
                '1.1.1.1': {'attempts': 3, 'successes': 3, 'total_ms': 40},
                '3.3.3.3': {'attempts': 3, 'successes': 3, 'total_ms': 100},
                '2.2.2.2': {'attempts': 3, 'successes': 3, 'total_ms': 50},
            }
            with patch.object(runner, 'cf_request_all', side_effect=reads), patch.object(runner, 'cf_request'), \
                    patch.object(runner, 'recheck_ips', return_value=checks), patch.object(runner, 'TOP_N', 1), patch.object(runner, 'SWITCH_MIN_IMPROVEMENT', .2):
                result = runner.update_dns_records('x', [{'domain': 'a.example'}, {'domain': 'b.example'}], 'A', [{'ip': '2.2.2.2'}], 60, False, 'example.com')
            self.assertEqual([r['status'] for r in result], ['verified', 'failed'])
            state = runner.load_switch_state()['entries']
            self.assertIn('a.example|A', state)
            self.assertNotIn('b.example|A', state)

    def test_failed_state_save_cannot_leak_into_later_domain(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'resolve_zone_id', return_value='zone'):
            a = dict(id='a', type='A', name='a.example', content='1.1.1.1', ttl=60, proxied=False)
            b = dict(id='b', type='A', name='b.example', content='1.1.1.1', ttl=60, proxied=False)
            checks = {'1.1.1.1': {'attempts': 3, 'successes': 3, 'total_ms': 40}}
            real_save = runner.save_switch_state
            calls = {'n': 0}
            def flaky(state):
                calls['n'] += 1
                if calls['n'] == 1: raise OSError('disk')
                real_save(state)
            with patch.object(runner, 'cf_request_all', side_effect=[[a], [a], [b], [b]]), \
                    patch.object(runner, 'recheck_ips', return_value=checks), patch.object(runner, 'save_switch_state', side_effect=flaky):
                result = runner.update_dns_records('x', [{'domain': 'a.example'}, {'domain': 'b.example'}], 'A', [{'ip': '1.1.1.1'}], 60, False, 'example.com')
            self.assertEqual([x['status'] for x in result], ['failed', 'verified'])
            entries = runner.load_switch_state()['entries']
            self.assertNotIn('a.example|A', entries); self.assertIn('b.example|A', entries)

    def test_invalid_env_filters_stop_before_scan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'MAX_LOSS_RATE', float('nan')), patch.object(runner, 'ensure_mcis') as ensure:
            self.assertEqual(runner.run_all_modes(), 1)
            ensure.assert_not_called()
