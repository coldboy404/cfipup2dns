import contextlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_runner import runner
from test_selection import probe_result


def checked(speed=20, ms=100):
    return dict(attempts=3, successes=3, total_ms=ms,
                download_mbps=speed, download_recheck_ok=speed is not None)


class ThroughputTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in dict(SELECT_PROFILE='throughput', TOP_N=1, MIN_DOWNLOAD_MBPS=0,
                               MAX_LATENCY_MS=0, MIN_LATENCY_MS=0, MAX_LOSS_RATE=1,
                               CF_COLO='', DOWNLOAD_QUALIFY_MBPS=10, DOWNLOAD_TOP='4',
                               DOWNLOAD_MODE='sequential', DOWNLOAD_RECHECK_ROUNDS=2,
                               SWITCH_MIN_IMPROVEMENT=.2, SWITCH_MIN_INTERVAL=100).items():
            self.stack.enter_context(patch.object(runner, key, value))
        # Any accidental direct HTTPS or DNS access fails locally, never production.
        self.stack.enter_context(patch.object(runner, 'direct_https_probe', side_effect=AssertionError('unexpected HTTPS')))
        self.stack.enter_context(patch.object(runner, 'cf_request', side_effect=AssertionError('unexpected DNS')))
        self.stack.enter_context(patch.object(runner, 'log'))

    def rows(self):
        return [dict(ip=f'192.0.2.{i}', ok=True, score_ms=i, latency_ms=i) for i in range(1, 7)]

    def decide(self, checks, previous=None):
        return runner.decide_switch('a', 'A', ['192.0.2.1'],
            [dict(ip='192.0.2.2', download_mbps=99999)], checks, previous or {}, 100)

    def test_bucket_round_robin_preserves_latency_rank_within_bucket(self):
        rows = [dict(ip=f'104.25.0.{i}', colo='HKG') for i in range(1, 5)]
        rows += [dict(ip='172.66.1.1', colo='HKG'), dict(ip='104.25.1.1', colo='NRT'),
                 dict(ip='162.159.138.1', colo='HKG')]
        ordered = runner.diverse_candidates(rows)
        self.assertEqual([r['ip'] for r in ordered[:4]],
                         ['104.25.0.1', '172.66.1.1', '104.25.1.1', '162.159.138.1'])
        self.assertEqual(len(ordered), len(rows))
        self.assertEqual(runner.diversity_prefix('2606:4700:1200::1'), '2606:4700:1200::/40')

    def test_exploration_excludes_covered_buckets_but_preserves_singletons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cidrs'
            original = '104.24.0.0/15\n162.159.138.1/32\n2606:4700::/32\n'
            path.write_text(original)
            remaining = runner.exploration_cidrs(path, [{'ip': '104.25.1.1'}], '4')
            self.assertEqual(remaining, ['104.24.0.0/16', '162.159.138.1/32'])
            self.assertEqual(path.read_text(), original)
            ipv6 = runner.exploration_cidrs(path, [{'ip': '2606:4700:1200::1'}], '6')
            self.assertTrue(ipv6)
            covered = ipaddress.ip_network('2606:4700:1200::/40')
            self.assertTrue(all(not ipaddress.ip_network(net).overlaps(covered) for net in ipv6))
            self.assertTrue(all(ipaddress.ip_network(net).subnet_of(ipaddress.ip_network('2606:4700::/32')) for net in ipv6))

    def test_two_pass_budget_dedup_and_no_original_cidr_changes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'MAX_SCAN_SECONDS', 8):
            root = Path(tmp)
            cidr = root / 'cidrs'
            original = '104.25.0.0/16\n162.159.138.1/32\n'
            cidr.write_text(original)
            budgets, timeouts = [], []
            def scan(args, output, mode, timeout):
                budgets.append(int(args[args.index('-budget') + 1]))
                timeouts.append(timeout)
                source = Path(args[args.index('-cidr-file') + 1]).read_text()
                ip = '104.25.1.1' if len(budgets) == 1 else '162.159.138.1'
                if len(budgets) == 2:
                    self.assertEqual(source.strip(), '162.159.138.1/32')
                output.write_text(json.dumps(dict(ip=ip, ok=True, score_ms=10)))
                return {'scan_timed_out': False}
            args = ['fake', '-budget', '100', '-cidr-file', str(cidr), '-top', '200', '-heads', '4']
            with patch.object(runner, 'run_mcis', side_effect=scan):
                rows, meta = runner.scan_candidates(args, root / 'rows', '4', cidr)
            self.assertEqual(budgets, [75, 25])
            self.assertEqual(sum(budgets), 100)
            self.assertEqual(timeouts[0], 6)
            self.assertLessEqual(timeouts[1], 8)
            self.assertEqual(len(rows), 2)
            self.assertEqual(meta['candidate_prefix_count'], 2)
            self.assertEqual(cidr.read_text(), original)
            self.assertFalse(list(root.glob('.explore-*')))

    def test_slow_success_does_not_stop_goal_and_zero_min_keeps_fastest_fallback(self):
        with patch.object(runner, 'sample_download', side_effect=[probe_result(True, n) for n in [1, 3, 2, 4]]) as probe:
            selected, counts = runner.sample_candidates(self.rows(), '4', 'example.com', '/')
        self.assertEqual(probe.call_count, 4)
        self.assertEqual(selected[0]['download_mbps'], 4)
        self.assertEqual(counts['download_qualified_count'], 0)
        self.assertEqual(counts['download_success_count'], 4)
        self.assertEqual(counts['download_qualify_mbps'], 10)

    def test_goal_is_early_stop_not_filter_and_explicit_min_fails_closed(self):
        with patch.object(runner, 'sample_download', side_effect=[probe_result(True, n) for n in [1, 11]]) as probe:
            selected, counts = runner.sample_candidates(self.rows(), '4', 'example.com', '/')
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(selected[0]['download_mbps'], 11)
        self.assertEqual(counts['download_qualified_count'], 1)
        with patch.object(runner, 'MIN_DOWNLOAD_MBPS', 20), \
                patch.object(runner, 'sample_download', return_value=probe_result(True, 5)):
            selected, counts = runner.sample_candidates(self.rows(), '4', 'example.com', '/')
        self.assertEqual(selected, [])
        self.assertEqual(counts['download_attempt_count'], 4)
        self.assertEqual(counts['download_qualify_mbps'], 20)

    def test_faster_speed_replaces_even_if_https_latency_is_slower(self):
        result = self.decide({'192.0.2.1': checked(10, 10), '192.0.2.2': checked(30, 100)})
        self.assertEqual(result[0], ['192.0.2.2'])
        self.assertEqual(result[1], 'minimum_improvement_met')
        self.assertTrue(result[3])

    def test_equal_slower_missing_speed_never_fall_back_to_latency_or_historical_speed(self):
        for old, new in [(10, 10), (10, 9), (None, 30), (10, None), (10, float('nan'))]:
            with self.subTest(old=old, new=new):
                result = self.decide({'192.0.2.1': checked(old, 500), '192.0.2.2': checked(new, 1)})
                self.assertEqual(result[0], ['192.0.2.1'])
                self.assertFalse(result[3])

    def test_cooldown_and_https_failure_counter_unchanged(self):
        checks = {'192.0.2.1': checked(10), '192.0.2.2': checked(30)}
        self.assertEqual(self.decide(checks, {'last_switch_at': 99})[1], 'cooldown')
        checks['192.0.2.1'] = dict(attempts=3, successes=0, total_ms=None)
        first = self.decide(checks)
        self.assertIn('failure_wait_1/2', first[1])
        second = self.decide(checks, first[2])
        self.assertEqual(second[0], ['192.0.2.2'])
        self.assertEqual(second[1], 'incumbent_failure_threshold')

    def test_https_outage_recovery_does_not_depend_on_download_budget_when_min_disabled(self):
        checks = {'192.0.2.1': dict(attempts=3, successes=0), '192.0.2.2': checked(None)}
        previous = {'failure_counts': {'192.0.2.1': 1}, 'last_switch_at': 99}
        result = self.decide(checks, previous)
        self.assertEqual(result[0], ['192.0.2.2'])
        self.assertEqual(result[1], 'incumbent_failure_threshold')
        with patch.object(runner, 'MIN_DOWNLOAD_MBPS', 1):
            result = self.decide(checks, previous)
            self.assertEqual(result[0], ['192.0.2.1'])
            self.assertFalse(result[3])

    def test_expansion_requires_fresh_speed_but_not_qualify_goal(self):
        for speed, allowed in [(None, False), (1, True)]:
            with self.subTest(speed=speed), patch.object(runner, 'TOP_N', 2):
                result = self.decide({'192.0.2.1': checked(None), '192.0.2.2': checked(speed)})
                self.assertEqual(len(result[0]), 2 if allowed else 1)
        with patch.object(runner, 'MAX_LATENCY_MS', 50):
            self.assertFalse(self.decide({'192.0.2.1': checked(10), '192.0.2.2': checked(100, 100)})[3])

    def test_recheck_median_requires_all_rounds_success_finite_positive(self):
        for bad in [probe_result(False), probe_result(True, float('inf')), probe_result(True, 0)]:
            with self.subTest(bad=bad), patch.object(runner, 'sample_download', side_effect=[probe_result(True, 10), bad]):
                result = runner.recheck_downloads(['192.0.2.1'])['192.0.2.1']
                self.assertFalse(result['download_recheck_ok'])
                self.assertIsNone(result['download_mbps'])
        with patch.object(runner, 'sample_download', side_effect=[probe_result(True, 10), probe_result(True, 30)]) as probe:
            result = runner.recheck_downloads(['192.0.2.1', '192.0.2.1'])['192.0.2.1']
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(result['download_mbps'], 20)
        self.assertTrue(result['download_recheck_ok'])
        self.assertIsInstance(result['download_measured_at'], float)
        with patch.object(runner, 'sample_download') as probe:
            result = runner.recheck_downloads(['192.0.2.1'], deadline=time.monotonic() - 1)
            probe.assert_not_called()
            self.assertFalse(result['192.0.2.1']['download_recheck_ok'])

    def test_dns_invocation_caches_downloads_across_domains_with_shared_deadline(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'resolve_zone_id', return_value='zone'):
            records = [dict(id='1', type='A', name=f'{name}.example', content='192.0.2.1', ttl=60, proxied=False)
                       for name in ['a', 'b']]
            checks = {'192.0.2.1': checked(30), '192.0.2.2': checked(10)}
            speeds = {ip: {key: value for key, value in check.items() if key.startswith('download_')} for ip, check in checks.items()}
            with patch.object(runner, 'cf_request_all', side_effect=[[records[0]], [records[0]], [records[1]], [records[1]]]), \
                    patch.object(runner, 'recheck_ips', side_effect=lambda *a: {ip: dict(c) for ip, c in checks.items()}), \
                    patch.object(runner, 'recheck_downloads', return_value=speeds) as download:
                outcomes = runner.update_dns_records('x', [{'domain': r['name']} for r in records], 'A',
                                                     [{'ip': '192.0.2.2'}], 60, False, 'example.com')
            self.assertEqual(download.call_count, 1)
            self.assertEqual([r['status'] for r in outcomes], ['verified', 'verified'])
            self.assertEqual(outcomes[0]['download_checks']['192.0.2.1']['download_mbps'], 30)

    def test_download_disabled_never_runs_dns_downloads_or_latency_replacement(self):
        record = dict(id='1', type='A', name='a.example', content='192.0.2.1', ttl=60, proxied=False)
        checks = {'192.0.2.1': dict(attempts=3, successes=3, total_ms=500),
                  '192.0.2.2': dict(attempts=3, successes=3, total_ms=1)}
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'DOWNLOAD_TOP', '0'), patch.object(runner, 'resolve_zone_id', return_value='zone'), \
                patch.object(runner, 'cf_request_all', side_effect=[[record], [record]]), \
                patch.object(runner, 'recheck_ips', return_value=checks), \
                patch.object(runner, 'recheck_downloads') as download:
            result = runner.update_dns_records('x', [{'domain': 'a.example'}], 'A',
                [{'ip': '192.0.2.2'}], 60, False, 'example.com')
        download.assert_not_called()
        self.assertEqual(result[0]['actual_ips'], ['192.0.2.1'])
        self.assertFalse(result[0]['switched'])

    def test_run_persists_incumbent_same_round_metrics_not_candidate_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), \
                patch.object(runner, 'SCAN_ONLY', False), \
                patch.object(runner, 'scan_candidates', return_value=([{'ip': '192.0.2.2', 'score_ms': 10}], {})), \
                patch.object(runner, 'sample_candidates', return_value=([{'ip': '192.0.2.2', 'download_mbps': 999}], {'download_success_count': 1})), \
                patch.object(runner, 'update_dns_records', return_value=[dict(status='verified', actual_ips=['192.0.2.1'],
                    download_checks={'192.0.2.1': dict(download_ok=True, download_recheck_ok=True, download_mbps=30, download_measured_at=123.0)})]):
            root = Path(tmp)
            (root / 'ipv4cidr.txt').write_text('192.0.2.1/32')
            result = runner.run_mode('4', {'cloudflare': {'token': 'x', 'records': [{'domain': 'a.example'}]}}, 'fake')
            saved = json.loads((root / 'best_ips_v4.json').read_text())['result'][0]
            self.assertEqual(saved['ip'], '192.0.2.1')
            self.assertEqual(saved['download_mbps'], 30)
            self.assertEqual(saved['download_measured_at'], 123.0)
            self.assertEqual(result['actual_dns_count'], 1)

    def test_partial_download_budget_replaces_only_measured_incumbent_slots(self):
        checks = {'192.0.2.1': checked(10), '192.0.2.2': checked(None),
                  '192.0.2.3': checked(30)}
        with patch.object(runner, 'TOP_N', 2):
            result = runner.decide_switch('a', 'A', ['192.0.2.1', '192.0.2.2'],
                [{'ip': '192.0.2.3'}], checks, {}, 100)
        self.assertEqual(result[0], ['192.0.2.3', '192.0.2.2'])
        self.assertTrue(result[3])

    def test_recheck_budget_completes_useful_pairs_without_partial_medians(self):
        clock = [0.0]
        def probe(*args, **kwargs):
            clock[0] += min(6, kwargs['timeout_seconds'])
            return probe_result(True, 20)
        with patch.object(runner.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(runner, 'sample_download', side_effect=probe) as sample:
            result = runner.recheck_downloads(['192.0.2.1', '192.0.2.2', '192.0.2.3'], deadline=25)
        self.assertEqual(sample.call_count, 5)
        self.assertTrue(result['192.0.2.1']['download_recheck_ok'])
        self.assertTrue(result['192.0.2.2']['download_recheck_ok'])
        self.assertFalse(result['192.0.2.3']['download_recheck_ok'])
        self.assertEqual(clock[0], 25)

    def test_dns_recheck_minimum_speed_is_hard_filter(self):
        with patch.object(runner, 'MIN_DOWNLOAD_MBPS', 50), patch.object(runner, 'TOP_N', 2):
            result = self.decide({'192.0.2.1': checked(10), '192.0.2.2': checked(40)})
        self.assertEqual(result[0], ['192.0.2.1'])
        self.assertFalse(result[3])

    def test_isolated_cli_scan_only_never_dns_rechecks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'ipv4cidr.txt').write_text('192.0.2.1/32\n')
            (root / 'ipv6cidr.txt').write_text('2001:db8::1/128\n')
            # Executing main in an isolated interpreter with trap seams makes
            # scan-only exercise real locking/history/serialization and zero DNS.
            script = ("import cfip_runner as r\n"
                      "r.ensure_mcis=lambda: 'fake'\n"
                      "def fail(*a, **kw): raise AssertionError('DNS forbidden')\n"
                      "r.update_dns_records=r.recheck_downloads=r.cf_request=fail\n"
                      "def scan(args, path, mode, timeout):\n"
                      " path.write_text('{\"ip\":\"192.0.2.1\",\"ok\":true,\"score_ms\":10}'); return {}\n"
                      "r.run_mcis=scan\n"
                      "raise SystemExit(r.main())\n")
            env = dict(os.environ, PROJECT_DIR=tmp, SELECT_PROFILE='throughput', SCAN_ONLY='true',
                       DOWNLOAD_TOP='0', IP_MODE='4', CONFIG_FILE=str(root / 'absent'))
            result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parents[1],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / 'scan_only_v4.json').exists())
            self.assertFalse((root / 'best_ips_v4.json').exists())
            self.assertFalse((root / 'switch_state.json').exists())
