# Publishing MQTTD to PyPI

This guide explains how to build and publish the `mqttd` package to PyPI.

## Prerequisites

1. **PyPI Account**: Create an account at https://pypi.org/account/register/
2. **TestPyPI Account** (optional, for testing): https://test.pypi.org/account/register/
3. **Install build tools**:
   ```bash
   pip install --upgrade build twine
   ```

## Step 1: Update Version

Before publishing, ensure the version is updated in:
- `mqttd/__init__.py` - `__version__` variable
- `setup.py` - `version` variable (or it reads from __init__.py)
- `pyproject.toml` - `version` field

## Step 2: Clean Previous Builds

```bash
# Remove old build artifacts
rm -rf build/
rm -rf dist/
rm -rf *.egg-info
```

## Step 3: Build the Package

### Build source distribution and wheel:

```bash
python -m build
```

This creates:
- `dist/mqttd-0.2.0.tar.gz` (source distribution)
- `dist/mqttd-0.2.0-py3-none-any.whl` (wheel)

### Verify the build:

```bash
# Check the files
ls -lh dist/

# Verify the package contents
tar -tzf dist/mqttd-*.tar.gz | head -20
```

## Step 4: Test on TestPyPI (Recommended)

Before publishing to production PyPI, test on TestPyPI:

```bash
# Upload to TestPyPI
python -m twine upload --repository testpypi dist/*

# You'll be prompted for:
# - Username: your_testpypi_username
# - Password: your_testpypi_password (or API token)
```

### Test Installation from TestPyPI:

```bash
# Create a virtual environment for testing
python -m venv test_env
source test_env/bin/activate  # On Windows: test_env\Scripts\activate

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ mqttd

# Test the package
python -c "from mqttd import MQTTApp; print('Success!')"
```

## Step 5: Publish to Production PyPI

Once tested, publish to production PyPI:

```bash
# Upload to PyPI
python -m twine upload dist/*

# You'll be prompted for:
# - Username: __token__
# - Password: your_pypi_api_token (recommended)
#   OR
# - Username: your_pypi_username
# - Password: your_pypi_password
```

### Using API Token (Recommended)

1. Go to https://pypi.org/manage/account/token/
2. Create a new API token
3. Use `__token__` as username and the token as password

## Step 6: Verify Publication

After publishing, verify:

1. Check PyPI: https://pypi.org/project/mqttd/
2. Test installation:
   ```bash
   pip install mqttd
   python -c "from mqttd import MQTTApp; print('Success!')"
   ```

## Updating the Package

For subsequent releases:

1. Update version in `mqttd/__init__.py` and `pyproject.toml`
2. Update `CHANGELOG.md` (if you have one)
3. Clean, build, and upload:
   ```bash
   rm -rf build/ dist/ *.egg-info
   python -m build
   python -m twine upload dist/*
   ```

## Troubleshooting

### "File already exists" error

This means the version already exists on PyPI. Update the version number.

### Authentication errors

- Make sure you're using the correct credentials
- For API tokens, use `__token__` as username
- Check that your token has the correct permissions

### Package not found after upload

- Wait a few minutes for PyPI to process
- Check https://pypi.org/project/mqttd/ directly
- Try: `pip install --upgrade mqttd`

## Automated Publishing with GitHub Actions (Optional)

You can set up GitHub Actions to automatically publish on tags:

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  release:
    types: [created]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.x'
      - name: Install build tools
        run: |
          python -m pip install --upgrade pip
          pip install build twine
      - name: Build package
        run: python -m build
      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
```

Then add `PYPI_API_TOKEN` to your GitHub repository secrets.
