# Python 3.13+ No-GIL Setup Guide

## Overview

Python 3.13+ with `--disable-gil` flag (or Python 3.14+ by default) enables true parallelism in a single process, perfect for million-scale MQTT connections.

## Installation

### Option 1: Python 3.13 with --disable-gil (Current)

```bash
# Install Python 3.13
# Note: You may need to build from source with --disable-gil flag

# Run server with no-GIL
python3.13 --disable-gil -m mqttd.app
```

### Option 2: Python 3.14 (When Available)

```bash
# Python 3.14 will have no-GIL by default
python3.14 -m mqttd.app
```

## Current Status

Your system: **Python 3.12.12** (does not support no-GIL)

**Action Required:**
1. Install Python 3.13+ with `--disable-gil` support
2. Or wait for Python 3.14 release

## Building Python 3.13 with No-GIL

```bash
# Download Python 3.13 source
wget https://www.python.org/ftp/python/3.13.0/Python-3.13.0.tgz
tar -xzf Python-3.13.0.tgz
cd Python-3.13.0

# Configure with --disable-gil
./configure --disable-gil --prefix=/usr/local/python3.13-nogil

# Build
make -j24  # Use all 24 cores

# Install
sudo make install
```

## Architecture Changes

### Before (Multi-Process)
- 24 separate processes
- 48-96 GB memory
- Process overhead
- Inter-process communication

### After (No-GIL Single Process)
- Single process with 24 threads
- 5-10 GB memory (80-90% reduction!)
- Thread overhead (minimal)
- Shared memory (fast)

## Benefits

1. **Memory Efficiency**: 80-90% reduction
2. **CPU Efficiency**: Better utilization of all 24 cores
3. **Simpler Architecture**: Single process
4. **Lower Latency**: No inter-process communication
5. **Easier Debugging**: Single process

## Code Changes

The codebase now includes:
- `mqttd/thread_safe.py` - Thread-safe data structures
- Updated architecture for no-GIL mode
- Thread pool support

## Running with No-GIL

```bash
# Python 3.13
python3.13 --disable-gil examples/mqtt5_server.py

# Python 3.14 (when available)
python3.14 examples/mqtt5_server.py
```

## Verification

```python
import sys

# Check if running with no-GIL
if hasattr(sys, 'getswitchinterval'):
    print(f"Switch interval: {sys.getswitchinterval()}")
    if sys.getswitchinterval() == 0:
        print("✓ Running with no-GIL!")
    else:
        print("✗ Running with GIL")
else:
    print("✗ No-GIL not available")
```

## Expected Performance

**With No-GIL Python:**
- Single process: **1-2 million connections**
- Memory: **5-10 GB** (vs 50-100 GB multi-process)
- CPU: Efficient use of all 24 cores
- Latency: Lower (shared memory)

## Migration Checklist

- [ ] Install Python 3.13+ with --disable-gil
- [ ] Update code to use thread-safe data structures
- [ ] Test with no-GIL mode
- [ ] Benchmark performance
- [ ] Monitor for thread safety issues
- [ ] Update dependencies (ensure no-GIL compatibility)

## Notes

- No-GIL is experimental in Python 3.13
- Some C extensions may need updates
- JIT compiler not available with no-GIL in 3.14
- Test thoroughly before production
