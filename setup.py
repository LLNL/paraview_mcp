"""
Setup script for ParaView MCP Server
"""
from setuptools import setup, find_packages
import os

# Read the README file
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Read requirements
def read_requirements(filename):
    """Read requirements from file"""
    with open(filename, "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

# Get version from package (should match pyproject.toml)
version = "0.1.1"

setup(
    name="paraview-mcp",
    version=version,
    author="Shusen Liu and Haichao Miao",
    author_email="liu42@llnl.gov and miao1@llnl.gov",
    description="MCP (Model Context Protocol) server for ParaView visualization control",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/LLNL/paraview_mcp",
    packages=["paraview_mcp"],
    package_dir={"paraview_mcp": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Visualization",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        "mcp>=1.10.0,<2.0.0",  # Pin to compatible version range
        "httpx>=0.24.0",
        "Pillow>=9.0.0",
        # Note: ParaView Python bindings are typically installed separately
        # as they require the full ParaView installation
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "black>=22.0.0",
            "flake8>=5.0.0",
            "mypy>=0.990",
        ],
        "eval": [
            "promptfoo>=0.118.0",
            "anthropic>=0.34.0",
            "openai>=1.0.0",
            "python-dotenv>=1.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "paraview-mcp-server=paraview_mcp.paraview_mcp_server:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)