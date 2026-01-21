# ngtcp2 Implementation Tests

This directory contains tests for the ngtcp2 QUIC implementation.

## Test Structure

- `test_ngtcp2_bindings.py` - Tests for ngtcp2 Python bindings (library loading, function calls, type conversions)
- `test_quic_ngtcp2.py` - Tests for QUIC connection and stream management
- `test_mqtt_quic_ngtcp2.py` - Integration tests for MQTT over QUIC
- `conftest.py` - Pytest configuration and fixtures

## Running Tests

### ⚠️ **CRITICAL: Known Crash Issue**

**Tests will crash with segfault if ngtcp2 is not properly configured:**
```
ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable.
Aborted (core dumped)
```

**This crash happens during import and cannot be caught by Python.**

**Solution**: Fix ngtcp2 configuration first (see `TESTING_NOTES.md`), or don't run tests until ngtcp2 is properly set up.

### Using Safe Test Runner (May Still Crash)

```bash
# Attempts to run only safe tests (may still crash if ngtcp2 not configured)
python tests/run_tests_safe.py

# Run with verbose output
python tests/run_tests_safe.py -v
```

**Note**: Even the "safe" runner may crash if ngtcp2 modules are imported. The crash occurs in the ngtcp2 C library and cannot be prevented from Python.

### Using unittest (default)

```bash
# Run all tests (may crash if ngtcp2 not properly configured)
python tests/run_tests.py

# Run with verbose output
python tests/run_tests.py -v

# Run specific test module
python tests/run_tests.py test_ngtcp2_bindings
```

### Using pytest (recommended)

```bash
# Install pytest first
pip install pytest pytest-asyncio

# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run specific test module
pytest tests/test_ngtcp2_bindings.py

# Run specific test class
pytest tests/test_quic_ngtcp2.py::TestNGTCP2Stream

# Run with coverage
pytest tests/ --cov=mqttd --cov-report=html
```

### Using unittest directly

```bash
# Run all tests
python -m unittest discover tests

# Run specific test
python -m unittest tests.test_ngtcp2_bindings
```

## Test Requirements

- Python 3.8+
- ngtcp2 library installed (for full test coverage)
- pytest and pytest-asyncio (optional, for pytest runner)

Tests will automatically skip if ngtcp2 is not available.

## Test Coverage

### Unit Tests (test_ngtcp2_bindings.py)
- Library loading
- Function calls
- Type conversions
- Error handling
- Constants

### Connection Tests (test_quic_ngtcp2.py)
- Stream creation and management
- Stream reader/writer interfaces
- Connection lifecycle
- Packet handling
- State transitions

### Integration Tests (test_mqtt_quic_ngtcp2.py)
- MQTT CONNECT over QUIC
- MQTT PUBLISH over QUIC
- MQTT SUBSCRIBE over QUIC
- Connection resilience
- Multiple streams

## Notes

- Tests use mocking for ngtcp2 connections when the library is not available
- Some tests require actual QUIC packets and may need proper TLS setup
- Integration tests may require certificates for full TLS testing
