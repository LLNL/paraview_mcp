# Paraview_MCP

ParaView-MCP is an autonomous agent that integrates multimodal large language models with ParaView through the Model Context Protocol, enabling users to create and manipulate scientific visualizations using natural language and visual inputs instead of complex commands or GUI operations. The system features visual feedback capabilities that allow it to observe the viewport and iteratively refine visualizations, making advanced visualization accessible to non-experts while augmenting expert workflows with intelligent automation.

## Disclaimer 
The current implementation of the connection between the MCP server and ParaView, in both the main and dev branches, relies on synchronization between pvserver and the ParaView client. Because this feature has been deprecated in most recent ParaView versions, the ParaView application view may not display content from the pvserver instance correctly, and overall stability issues may occur.

## Video Demo

Click the image below to watch the video:

[![Video Title](https://img.youtube.com/vi/GvcBnAcIXp4/maxresdefault.jpg)](https://youtu.be/GvcBnAcIXp4)


## Installation

### Option 1: Conda Environment (Recommended)

The easiest way to install ParaView-MCP is using the provided conda environment file:

```shell
# Clone the repository
git clone https://github.com/LLNL/paraview_mcp.git
cd paraview_mcp

# Create and activate the conda environment
conda env create -f environment.yml
conda activate paraview_mcp

# Install the package in development mode
pip install -e .
```


## Setup for LLM

To set up integration with claude desktop, add the following to claude_desktop_config.json

```json
    "mcpServers": {
      "ParaView": {
        "command": "/path/to/python",
        "args": [
        "/path/to/paraview_mcp/src/paraview_mcp_server.py"
        ]
      }
    }
```

## Running 

### 1. Start paraview server

```shell
pvserver --multi-clients
```

### 2. Connect to paraview server from paraview GUI (file -> connect)

### 3. Start claude desktop app 

***

## Testing

### Unit and Integration Tests

ParaView-MCP includes comprehensive unit and integration tests to ensure reliability.

#### Prerequisites

1. **Install test dependencies**:
```bash
pip install pytest pytest-cov
```

2. **Start ParaView server** (required for integration tests):
```bash
pvserver --multi-clients --server-port=11111
```

3. **Connect ParaView GUI** to the server:
   - Open ParaView GUI
   - File -> Connect -> Add Server
   - Host: localhost, Port: 11111
   - Connect

#### Running Tests

```bash
# Run live integration tests (requires running ParaView server)
pytest tests/test_paraview_manager_live.py -v

# Run with coverage report
pytest tests/test_paraview_manager_live.py --cov=src --cov-report=html

# Run specific test class
pytest tests/test_paraview_manager_live.py::TestParaViewLive -v

# Run specific test method
pytest tests/test_paraview_manager_live.py::TestParaViewLive::test_create_sphere -v
```

#### Test Structure

- **`tests/test_paraview_manager_live.py`**: Live integration tests with ParaView
  - Tests real ParaView operations
  - Verifies end-to-end functionality
  - Requires running ParaView server connected on port 11111

## Evaluation

### Promptfoo Evaluation Framework

For comprehensive feature testing using LLM evaluation:

#### Setup

1. **Install promptfoo**:
```bash
npm install -g promptfoo
```

2. **Start ParaView server** (if not already running):
```bash
pvserver --multi-clients --server-port=11111
```
Then connect ParaView GUI to the server (File -> Connect -> localhost:11111)

#### Run Evaluation Tests

```bash
# Run evaluation with Claude
promptfoo eval --no-cache -c eval/eval_claude.yaml --verbose

# Run simple action tests
promptfoo eval --no-cache -c eval/eval_claude.yaml -t eval/simple_action_eval.yaml --verbose

```

The `--no-cache` flag ensures fresh test runs, and `--verbose` provides detailed debugging output.

## Data Anonymization

To prevent evaluation bias from metadata or file naming patterns, ParaView-MCP includes a tool to anonymize datasets and test files.

### Usage

```bash
# Anonymize a test file and copy all referenced datasets
python eval/anonymize_dataset.py test.yaml

# Quick mode - only update YAML paths without copying data files
python eval/anonymize_dataset.py test.yaml --quick

# Custom output directory
python eval/anonymize_dataset.py test.yaml -o my_output_dir

# Preview changes without writing files
python eval/anonymize_dataset.py test.yaml --dry-run

# Save mapping for later reference
python eval/anonymize_dataset.py test.yaml -m mapping.json
```

### What Gets Anonymized

- **Dataset names**: `aneurism` → `dataset_001`
- **Descriptive filenames**: `aneurism_256x256x256_uint8.raw` → `data_001.raw`
- **File paths in YAML**: All paths updated to point to anonymized versions
- **Directory structure**: Organized as `output_dir/dataset_XXX/data/`

### Output

The tool creates:
1. `test_anonymized.yaml` - Updated test file with anonymous paths
2. `eval/anonymized_datasets/` - Directory containing copied, anonymized data files
3. `mapping.json` (optional) - Record of all name mappings

### Example

Original YAML:
```yaml
question: "Load ../SciVisAgentBench-tasks/aneurism/data/aneurism_256x256x256_uint8.raw"
```

Anonymized YAML:
```yaml
question: "Load eval/anonymized_datasets/dataset_001/data/data_000.raw"
```

## Citing Paraview_MCP

S. Liu, H. Miao, and P.-T. Bremer, “Paraview-MCP: Autonomous Visualization Agents with Direct Tool Use,” in Proc. IEEE VIS 2025 Short Papers, 2025, pp. 00

```bibtex
@inproceedings{liu2025paraview,
  title={Paraview-MCP: Autonomous Visualization Agents with Direct Tool Use},
  author={Liu, S. and Miao, H. and Bremer, P.-T.},
  booktitle={Proc. IEEE VIS 2025 Short Papers},
  pages={00},
  year={2025},
  organization={IEEE}
}
```

## Authors 
Paraview_MCP was originally created by Shusen Liu (liu42@llnl.gov) and Haichao Miao (miao1@llnl.gov)

## License
Paraview_MCP is distributed under the terms of the BSD-3 license.

LLNL-CODE-2007260
