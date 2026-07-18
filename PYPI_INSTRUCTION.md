# Complete Guide to Publishing paraview-mcp to PyPI

## Step-by-Step Instructions

### 1. Create PyPI Account (5 minutes)

1. Go to https://pypi.org/account/register/
2. Fill in:
   - Username (e.g., your GitHub username)
   - Email address
   - Password (strong password required)
3. Verify your email address
4. **OPTIONAL**: Also create test account at https://test.pypi.org/account/register/

### 2. Generate API Token (2 minutes)

1. Log in to PyPI
2. Go to Account Settings: https://pypi.org/manage/account/
3. Scroll down to "API tokens"
4. Click "Add API token"
5. Set:
   - Token name: `paraview-mcp-publish`
   - Scope: "Entire account" (for first time) or "Project: paraview-mcp" (after first upload)
6. **COPY THE TOKEN IMMEDIATELY** - it shows only once!
   - It will look like: `pypi-AgEIcHlwaS5vcmcCJGE4ZDA2MjFmLTk3NTQtNDQxNS1hNmY3LWE2M2Q4YzI2YjM5YwACKlszLCI4YzE5MGQ4Yi0zZjE1LTRmMjEtYmI2Ni02MzI5NGI4NTIyYTAiXQAABiDaOiFmX...`

### 3. Install Required Tools (1 minute)

```bash
# Install build and upload tools
pip install --upgrade pip setuptools wheel build twine
```

### 4. Update Package Metadata (2 minutes)

Edit these files to replace placeholder values:

**setup.py** - Change these lines:
```python
author="Your Name",  # Change to your name
author_email="your.email@example.com",  # Change to your email
url="https://github.com/yourusername/paraview-mcp",  # Change to your GitHub URL
```

**pyproject.toml** - Change these lines:
```toml
authors = [
    {name = "Your Name", email = "your.email@example.com"}  # Update with your info
]
[project.urls]
Homepage = "https://github.com/yourusername/paraview-mcp"  # Your GitHub URL
Repository = "https://github.com/yourusername/paraview-mcp"  # Your GitHub URL
```

**src/__init__.py** - Change these lines:
```python
__author__ = "Your Name"  # Your name
__email__ = "your.email@example.com"  # Your email
```

### 5. Build the Package (1 minute)

```bash
# Navigate to the project directory
cd /Users/liu42/gitRepo/LC/LLNL_git_official/paraview_mcp

# Clean any previous builds
rm -rf build dist *.egg-info src/*.egg-info

# Build the package
python -m build

# You should see output like:
# * Creating venv isolated environment...
# * Installing packages in isolated environment... (setuptools>=61.0, wheel)
# * Getting build dependencies for sdist...
# * Building sdist...
# * Building wheel from sdist...
# Successfully built paraview-mcp-0.1.0.tar.gz and paraview_mcp-0.1.0-py3-none-any.whl

# Verify the files were created
ls -la dist/
# Should show:
# paraview-mcp-0.1.0.tar.gz
# paraview_mcp-0.1.0-py3-none-any.whl
```

### 6. Check the Package (1 minute)

```bash
# Check for common issues
python -m twine check dist/*

# Should output:
# Checking dist/paraview-mcp-0.1.0.tar.gz: PASSED
# Checking dist/paraview_mcp-0.1.0-py3-none-any.whl: PASSED
```

### 7. Upload to TestPyPI First (RECOMMENDED) (2 minutes)

```bash
# Upload to test repository
python -m twine upload --repository testpypi dist/*

# It will prompt:
# Uploading distributions to https://test.pypi.org/legacy/
# Enter your username: __token__
# Enter your password: [PASTE YOUR TEST-PYPI TOKEN HERE]
```

**Test the installation:**
```bash
# Create a test virtual environment
python -m venv test_install
source test_install/bin/activate  # On Windows: test_install\Scripts\activate

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ paraview-mcp

# Verify it works
paraview-mcp-server --help

# Deactivate the test environment
deactivate
```

### 8. Upload to Real PyPI (1 minute)

```bash
# Upload to production PyPI
python -m twine upload dist/*

# It will prompt:
# Uploading distributions to https://upload.pypi.org/legacy/
# Enter your username: __token__
# Enter your password: [PASTE YOUR PYPI TOKEN HERE - starts with pypi-]

# You should see:
# Uploading paraview_mcp-0.1.0-py3-none-any.whl
# 100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 45.2/45.2 kB • 00:00 • ?
# Uploading paraview-mcp-0.1.0.tar.gz
# 100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 42.1/42.1 kB • 00:00 • ?

# View at: https://pypi.org/project/paraview-mcp/
```

### 9. Verify Installation (1 minute)

```bash
# Wait 1-2 minutes for PyPI to update
# Then anyone can install with:
pip install paraview-mcp

# Test it
paraview-mcp-server --help
```

## Alternative: Save Credentials for Future Use

Create `~/.pypirc` file:
```bash
cat > ~/.pypirc << 'EOF'
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-[YOUR-REAL-TOKEN-HERE]

[testpypi]
username = __token__
password = pypi-[YOUR-TEST-TOKEN-HERE]
EOF

# Set proper permissions
chmod 600 ~/.pypirc
```

Then you can upload without entering credentials:
```bash
python -m twine upload dist/*
```

## Quick Command Summary

```bash
# One-time setup
pip install --upgrade pip setuptools wheel build twine

# For each release
cd /Users/liu42/gitRepo/LC/LLNL_git_official/paraview_mcp
rm -rf build dist *.egg-info
python -m build
python -m twine check dist/*
python -m twine upload dist/*
# Username: __token__
# Password: [your-pypi-token]
```

## Troubleshooting

**"Package already exists"**:
- The name `paraview-mcp` might be taken. Try `paraview-mcp-server` or add your username

**"Invalid credentials"**:
- Make sure username is exactly `__token__` (not your PyPI username)
- Password should be the full token starting with `pypi-`

**"Invalid package"**:
- Run `python -m twine check dist/*` to find issues
- Make sure README.md exists and is valid markdown

**After upload, can't install**:
- Wait 1-2 minutes for PyPI's CDN to update
- Try: `pip install --no-cache-dir paraview-mcp`

## What Happens After Publishing

1. Your package will be available at: https://pypi.org/project/paraview-mcp/
2. Anyone can install it with: `pip install paraview-mcp`
3. You'll be the owner and can:
   - Upload new versions
   - Add other maintainers
   - Manage the project page

## Updating Your Package Later

1. Change version in `setup.py`, `pyproject.toml`, and `src/__init__.py` (e.g., "0.1.1")
2. Rebuild: `python -m build`
3. Upload: `python -m twine upload dist/*` (only uploads new files)

## Total Time Required: ~15 minutes

- Account creation: 5 minutes
- Setup and configuration: 5 minutes
- Build and upload: 5 minutes

The actual commands take only 2-3 minutes once you have your account and token ready!