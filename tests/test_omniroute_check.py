#!/usr/bin/env python3
import sys
import os
import unittest
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import time

SCRIPT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'omniroute_check.py'))

class SharedState:
    recorded_headers = []

class MockServerRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        SharedState.recorded_headers.append(list(self.headers.keys()))
        if self.path == '/v1/models' or self.path == '/models':
            if self.server.scenario == 'invalid_json':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"NOT A JSON")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'data': [{'id': 'apertus-70b'}]}).encode())
        elif self.path == '/v1/omniroute/status' or self.path == '/omniroute/status':
            if self.server.scenario == 'capability_404':
                self.send_response(404)
                self.end_headers()
            elif self.server.scenario == 'capability_timeout':
                time.sleep(4)
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

class TestOmnirouteCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), MockServerRequestHandler)
        cls.server.scenario = 'valid'
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        SharedState.recorded_headers = []
        self.server.scenario = 'valid'

    def test_positive_degraded_capability_404(self):
        self.server.scenario = 'capability_404'
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}/v1'
        env['OMNIROUTE_MODEL'] = 'apertus-70b'
        env['OMNIROUTE_API_KEY'] = 'test-key'
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"unsupported/degraded", proc.stdout)

    def test_positive_degraded_capability_timeout(self):
        self.server.scenario = 'capability_timeout'
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}/v1'
        env['OMNIROUTE_MODEL'] = 'apertus-70b'
        env['OMNIROUTE_API_KEY'] = 'test-key'
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"unsupported/degraded", proc.stdout)

    def test_positive_normalization_no_auth(self):
        self.server.scenario = 'valid'
        env = os.environ.copy()
        # Missing /v1 and missing API key
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}'
        env['OMNIROUTE_MODEL'] = 'apertus-70b'
        if 'OMNIROUTE_API_KEY' in env:
            del env['OMNIROUTE_API_KEY']
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0)

        auth_found = False
        for headers in SharedState.recorded_headers:
            if "Authorization" in headers:
                auth_found = True
        self.assertFalse(auth_found, "Authorization header found despite empty key")

    def test_negative_missing_mandatory(self):
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}/v1'
        env['OMNIROUTE_MODEL'] = ''
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 1)

    def test_negative_model_not_found(self):
        self.server.scenario = 'valid'
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}/v1'
        env['OMNIROUTE_MODEL'] = 'invalid-model'
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 1)

    def test_negative_invalid_json(self):
        self.server.scenario = 'invalid_json'
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = f'http://127.0.0.1:{self.port}/v1'
        env['OMNIROUTE_MODEL'] = 'apertus-70b'
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 1)

    def test_negative_blackhole_timeout(self):
        env = os.environ.copy()
        env['OMNIROUTE_DISCOVERY_URL'] = 'http://240.240.240.0:80/v1'
        env['OMNIROUTE_MODEL'] = 'apertus-70b'
        proc = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 1)

if __name__ == '__main__':
    unittest.main()
