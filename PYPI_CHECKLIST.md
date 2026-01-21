# PyPI Publishing Checklist

## Pre-Publishing Checklist

- [x] Update version in `mqttd/__init__.py`
- [x] Update version in `setup.py` (auto-reads from __init__.py)
- [x] Update version in `pyproject.toml`
- [x] Verify `README.md` is complete and accurate
- [x] Ensure `LICENSE` file exists
- [x] Check `MANIFEST.in` includes necessary files
- [x] Test package builds successfully
- [x] Verify no sensitive data in package

## Files Created/Updated

### Core Package Files
- ✅ `setup.py` - Updated with proper PyPI metadata
- ✅ `pyproject.toml` - Modern Python packaging configuration
- ✅ `MANIFEST.in` - Package file inclusion rules
- ✅ `mqttd/__init__.py` - Version defined here

### Documentation
- ✅ `README.md` - Comprehensive documentation
- ✅ `PUBLISH.md` - Detailed publishing instructions
- ✅ `QUICK_PUBLISH.md` - Quick reference guide
- ✅ `PYPI_CHECKLIST.md` - This checklist

### Scripts
- ✅ `build_and_publish.sh` - Helper script for building and publishing

## Package Configuration

### Version
- Current: `0.2.0`
- Location: `mqttd/__init__.py` → `__version__ = "0.2.0"`

### Dependencies
- **Required**: None (Redis is optional)
- **Optional**: 
  - `redis>=5.0.0` (for Redis pub/sub mode)
  - `aioquic>=0.9.20` (for QUIC support)

### Python Versions
- Minimum: Python 3.7+
- Recommended: Python 3.13+ (for no-GIL support)

## Build Commands

```bash
# Clean
rm -rf build/ dist/ *.egg-info

# Build
python -m build

# Check
ls -lh dist/

# Test on TestPyPI
python -m twine upload --repository testpypi dist/*

# Publish to PyPI
python -m twine upload dist/*
```

## Post-Publishing

- [ ] Verify package appears on PyPI: https://pypi.org/project/mqttd/
- [ ] Test installation: `pip install mqttd`
- [ ] Update GitHub repository with release tag
- [ ] Announce release (if applicable)

## Notes

- Package name: `mqttd`
- PyPI URL: https://pypi.org/project/mqttd/
- GitHub URL: https://github.com/arusatech/mqttd
