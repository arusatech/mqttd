#!/usr/bin/env python3
"""
Test runner for ngtcp2 implementation tests

Usage:
    python tests/run_tests.py              # Run all tests
    python tests/run_tests.py -v          # Verbose output
    python tests/run_tests.py test_bindings # Run specific test module
"""

import sys
import os
import unittest

# Skip TLS initialization in tests to avoid crashes when ngtcp2 is not fully configured
# This prevents "ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable" crashes
os.environ['MQTTD_SKIP_TLS_INIT'] = '1'

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_tests(pattern=None, verbosity=1):
    """Run tests matching pattern"""
    # Discover tests in tests directory
    loader = unittest.TestLoader()
    start_dir = os.path.dirname(os.path.abspath(__file__))
    
    if pattern:
        # Load specific test module
        suite = loader.loadTestsFromName(f'tests.{pattern}')
    else:
        # Discover all tests
        suite = loader.discover(start_dir, pattern='test_*.py')
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Run ngtcp2 implementation tests')
    parser.add_argument('pattern', nargs='?', help='Test pattern to run (e.g., test_bindings)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--pytest', action='store_true', help='Use pytest instead of unittest')
    
    args = parser.parse_args()
    
    verbosity = 2 if args.verbose else 1
    
    if args.pytest:
        # Try to use pytest
        try:
            import pytest
            test_args = ['-v' if args.verbose else '-q']
            if args.pattern:
                test_args.append(f'tests/{args.pattern}.py')
            else:
                test_args.append('tests/')
            sys.exit(pytest.main(test_args))
        except ImportError:
            print("pytest not available, falling back to unittest")
            sys.exit(0 if run_tests(args.pattern, verbosity) else 1)
    else:
        # Use unittest
        success = run_tests(args.pattern, verbosity)
        sys.exit(0 if success else 1)
