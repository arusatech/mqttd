# Quick Guide: Publishing to PyPI

## Prerequisites

```bash
pip install --upgrade build twine
```

## Quick Steps

### 1. Build the Package

```bash
python -m build
```

### 2. Test on TestPyPI (Optional but Recommended)

```bash
python -m twine upload --repository testpypi dist/*
```

### 3. Publish to PyPI

```bash
python -m twine upload dist/*
```

**Credentials:**
- Username: `__token__` (for API token)
- Password: Your PyPI API token (get from https://pypi.org/manage/account/token/)

## Or Use the Helper Script

```bash
./build_and_publish.sh
```

## Verify Installation

```bash
pip install mqttd
python -c "from mqttd import MQTTApp; print('Success!')"
```

## Update Version

Before publishing, update version in:
- `mqttd/__init__.py` - `__version__ = "0.2.0"`
- `pyproject.toml` - `version = "0.2.0"`
