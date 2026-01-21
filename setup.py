"""
Setup script for MQTTD package
"""

from setuptools import setup, find_packages  # type: ignore[import-untyped]
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="mqttd",
    version="0.1.0",
    description="FastAPI-like MQTT/MQTTS server for Python, compatible with libcurl clients",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Yakub Mohammad",
    author_email="yakub@arusatech.com",
    url="https://github.com/yourusername/mqttd",
    packages=find_packages(),
    python_requires=">=3.13",  # No-GIL support (3.13+ with --disable-gil, 3.14+ by default)
    install_requires=[
        "redis>=5.0.0",
        # aioquic removed - not compatible with no-GIL Python (Limited API issue)
        # Pure Python QUIC implementation included (transport_quic_pure.py)
        # For production-grade QUIC with ngtcp2, install ngtcp2 C library separately
    ],
    extras_require={
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
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Communications",
        "Topic :: Internet",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="mqtt mqtts server broker fastapi libcurl",
)
