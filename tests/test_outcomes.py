import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_runner import runner


class OutcomeTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner.subprocess, 'run'), patch.object(runner, 'parse_json_lines', return_value=('score_ms', [{'ip': '1.1.1.1', 'score_ms': 12}])), patch.object(runner, 'cf_request') as request, patch.object(runner, 'update_dns_records') as sync:
            root = Path(tmp)
            (root / 'ipv4cidr.txt').write_text('fixture')
            old = root / 'best_ips_v4.json'
            old.write_text('{"result":[]}')
            with patch.object(runner, 'SCAN_ONLY', True):
                self.assertEqual(runner.run_mode('4', {}, 'fake')['status'], 'scan_only')
            request.assert_not_called()
            sync.assert_not_called()
            sync.return_value = [{'domain': 'a.example', 'status': 'verified'}, {'domain': 'b.example', 'status': 'failed'}]
            cfg = {'cloudflare': {'token': 'secret', 'records': [{'domain': 'a.example'}, {'domain': 'b.example'}]}}
            with patch.object(runner, 'SCAN_ONLY', False):
                self.assertEqual(runner.run_mode('4', cfg, 'fake')['status'], 'partial_failure')
            self.assertEqual(old.read_text(), '{"result":[]}')

    def test_invalid_env_filters_stop_before_scan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'PROJECT_DIR', Path(tmp)), patch.object(runner, 'MAX_LOSS_RATE', float('nan')), patch.object(runner, 'ensure_mcis') as ensure:
            self.assertEqual(runner.run_all_modes(), 1)
            ensure.assert_not_called()
