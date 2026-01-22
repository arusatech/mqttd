"""
Setup script for MQTTD package
"""

from setuptools import setup, find_packages  # type: ignore[import-untyped]
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

# Read version from __init__.py
version_file = Path(__file__).parent / "mqttd" / "__init__.py"
version = "0.3.0"  # Default version (will be overridden by reading from __init__.py)
if version_file.exists():
    for line in version_file.read_text().splitlines():
        if line.startswith("__version__"):
            # Extract version, handling comments and quotes
            version_str = line.split("=")[1].strip()
            # Remove comments
            if "#" in version_str:
                version_str = version_str.split("#")[0].strip()
            # Remove quotes
            version = version_str.strip('"').strip("'")
            break

setup(
    name="mqttd",
    version=version,
    description="FastAPI-like MQTT/MQTTS server for Python, compatible with libcurl clients",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Yakub Mohammad",
    author_email="yakub@arusatech.com",
    url="https://github.com/arusatech/mqttd",
    project_urls={
        "Bug Reports": "https://github.com/arusatech/mqttd/issues",
        "Source": "https://github.com/arusatech/mqttd",
        "Documentation": "https://github.com/arusatech/mqttd#readme",
    },
    packages=find_packages(exclude=["tests", "tests.*", "examples", "examples.*", "docs", "docs.*", "reference", "reference.*"]),
    python_requires=">=3.7",  # Support Python 3.7+ (3.13+ recommended for no-GIL)
    install_requires=[
        # Redis is optional - only needed for Redis pub/sub mode
        # Direct routing works without Redis
    ],
    extras_require={
        "redis": [
            "redis>=5.0.0",
        ],
        "quic": [
            # Optional QUIC support via aioquic (not compatible with no-GIL Python)
            "aioquic>=0.9.20",
        ],
        "quic-ngtcp2": [
            # Note: ngtcp2 must be installed as C library
            # Install via system package manager or build from source
            # See: https://github.com/ngtcp2/ngtcp2
        ],
        "dev": [
            "pytest>=6.0",
            "pytest-asyncio>=0.18.0",
            "black>=21.0",
            "mypy>=0.900",
        ],
        "all": [
            "redis>=5.0.0",
            "aioquic>=0.9.20",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Communications",
        "Topic :: Internet",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Networking",
    ],
    keywords="mqtt mqtts mqtt5 server broker fastapi libcurl quic http3",
    zip_safe=False,
    include_package_data=True,
)
