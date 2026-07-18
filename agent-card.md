---
language:
- en # ISO language tag
tags:
- project:genesis # include on all GENESIS project models
- team:LLNL # Lawrence Livermore National Laboratory
- type:agent # Agent type
- science:visualization # Scientific visualization
- risk:general # indicates level of risk review {general, reviewed, restricted}
license: BSD-3-Clause # SPDX license identifier
base_model: claude-3-5-sonnet # Can be used with any MCP-compatible LLM

datasets:
    - # Supports VTK, EXODUS, CSV, RAW, and other ParaView-compatible formats

metrics:
    - # Visualization quality metrics and task completion rates

# Agent discovery + interoperability metadata
agent_card:
  name: "ParaView-MCP"
  description: "An autonomous agent that integrates multimodal large language models with ParaView through the Model Context Protocol, enabling natural language control of scientific visualizations."
  provider:
    organization: "Lawrence Livermore National Laboratory"
    url: "https://github.com/LLNL/paraview_mcp"
  version: "0.1.1"
  documentation_url: "https://paraview-mcp.readthedocs.io"
  protocol_version: "0.1.0"
  preferred_transport: "JSONRPC"
  capabilities:
    streaming: false
    push_notifications: false
    state_transition_history: false

# Authentication requirements for the Agent
authentication: 
  schemes: 
    - "None" # Local deployment, no authentication required
  credentials: ""

  # Defaults
  default_input_modes:
    - "text/plain"
    - "image/png"
  default_output_modes:
    - "text/plain"
    - "image/jpeg"

  # Skills: capability units exposed to users/other agents
  skills:
    - id: "data_loading"
      name: "Data Loading"
      description: "Load scientific data files (VTK, EXODUS, CSV, RAW volumes) into ParaView"
      tags: ["io", "data"]
      examples: ["Load the simulation results from output.vtk", "Load raw volume data with dimensions 256x256x256"]
      input_modes: ["text/plain"]
      output_modes: ["text/plain"]
    
    - id: "visualization_creation"
      name: "Visualization Creation"
      description: "Create isosurfaces, slices, clips, streamlines, and volume renderings"
      tags: ["visualization", "rendering"]
      examples: ["Create an isosurface at value 128", "Add volume rendering with custom transfer function"]
      input_modes: ["text/plain"]
      output_modes: ["text/plain", "image/jpeg"]
    
    - id: "camera_control"
      name: "Camera Control"
      description: "Control camera position, rotation, and view settings"
      tags: ["camera", "view"]
      examples: ["Rotate the camera 45 degrees", "Reset camera to show all data"]
      input_modes: ["text/plain"]
      output_modes: ["text/plain"]
    
    - id: "data_analysis"
      name: "Data Analysis"
      description: "Compute histograms, gradients, field calculations, and data filtering"
      tags: ["analysis", "computation"]
      examples: ["Show histogram of the scalar field", "Calculate gradient magnitude"]
      input_modes: ["text/plain"]
      output_modes: ["text/plain"]
    
    - id: "export"
      name: "Data Export"
      description: "Export data and visualizations in various formats (CSV, VTK, STL, screenshots)"
      tags: ["export", "io"]
      examples: ["Save the contour as STL", "Export data to CSV"]
      input_modes: ["text/plain"]
      output_modes: ["text/plain"]

# BPSW-only additions are explicitly separated as extensions.
Extensions:
  agent_runtime:
    framework: "FastMCP"
    service_endpoint: "localhost (via pvserver)"
    rate_limits: ""
    logging: ""
    memory: "stateless"

---

# ParaView-MCP

ParaView-MCP is an autonomous agent that integrates multimodal large language models with ParaView through the Model Context Protocol, enabling users to create and manipulate scientific visualizations using natural language and visual inputs instead of complex commands or GUI operations. The system features visual feedback capabilities that allow it to observe the viewport and iteratively refine visualizations, making advanced visualization accessible to non-experts while augmenting expert workflows with intelligent automation.

*Last Updated*: **2026-02-09**

## Developed by

- **Shusen Liu** (liu42@llnl.gov) - Lawrence Livermore National Laboratory
- **Haichao Miao** (miao1@llnl.gov) - Lawrence Livermore National Laboratory

## Contributed by

Lawrence Livermore National Laboratory (LLNL)

## Agent Changelog

+ **2025** Initial public version with core ParaView MCP functionality
+ **0.1.1** Current release with comprehensive visualization tools

## Agent short description

Tool-using visualization agent that enables natural language control of ParaView scientific visualization software through the Model Context Protocol (MCP).

## Agent description

1. The agent receives user requests in natural language and translates them into ParaView visualization operations.
2. The agent connects to a running ParaView server (pvserver) and executes visualization commands.
3. The agent can capture screenshots and provide visual feedback to iteratively refine visualizations.
4. The agent supports loading various scientific data formats and creating complex visualization pipelines.

## Underlying model(s)

- Primary model(s): Compatible with any MCP-capable LLM (Claude, GPT-4, etc.)
- The agent acts as an MCP server that exposes ParaView tools to the LLM.

## Inputs and outputs

- **Input**: Natural language commands describing desired visualizations
- **Output**: Status messages, pipeline information, and JPEG screenshots of visualizations

### Default interaction modes

- defaultInputModes: ["text/plain", "image/png"]
- defaultOutputModes: ["text/plain", "image/jpeg"]

### Skills

#### Data Loading & Management

| Skill ID | Name | Description |
|----------|------|-------------|
| load_data | Load Data | Load data files (VTK, EXODUS, CSV, etc.) into ParaView |
| load_raw_data | Load RAW Data | Load raw binary volume data with explicit dimensions and data type |
| clear_pipeline_and_reset | Clear Pipeline | Clear entire pipeline and reset to fresh state |

#### Source Creation

| Skill ID | Name | Description |
|----------|------|-------------|
| create_source | Create Source | Create geometric sources (Sphere, Cone, Cylinder, Plane, Box) |
| create_delaunay3d | Delaunay 3D | Create 3D Delaunay triangulation |

#### Visualization Filters

| Skill ID | Name | Description |
|----------|------|-------------|
| create_isosurface | Isosurface | Create isosurface visualization at specified value |
| create_slice | Slice | Create slice plane through volume data |
| create_clip | Clip | Clip data with a plane |
| create_streamline | Streamlines | Create streamline visualization for vector fields |
| warp_by_vector | Warp by Vector | Deform geometry by vector field |
| filter_data | Filter Data | Apply threshold and selection extraction |
| plot_over_line | Plot Over Line | Sample data along a line between two points |

#### Rendering & Display

| Skill ID | Name | Description |
|----------|------|-------------|
| toggle_volume_rendering | Volume Rendering | Toggle volume rendering visibility |
| toggle_visibility | Toggle Visibility | Show/hide active source |
| set_representation_type | Representation Type | Set Surface, Wireframe, Points, etc. |
| color_by | Color By Field | Color visualization by specific field |
| set_color_map | Set Color Map | Define custom color transfer function |
| edit_volume_opacity | Edit Opacity | Configure opacity transfer function |
| reset_colormaps | Reset Colormaps | Reset to default colormap settings |
| set_background_color | Background Color | Set view background color |

#### Camera & View

| Skill ID | Name | Description |
|----------|------|-------------|
| rotate_camera | Rotate Camera | Rotate camera by azimuth/elevation |
| reset_camera | Reset Camera | Reset camera to show all data |
| get_screenshot | Screenshot | Capture and return view screenshot |
| configure_screenshot_compression | Configure Compression | Adjust screenshot quality settings |

#### Data Analysis

| Skill ID | Name | Description |
|----------|------|-------------|
| get_histogram | Histogram | Compute histogram for field data |
| compute_surface_area | Surface Area | Compute surface area of mesh |
| calculate_field | Calculate Field | Create new fields with mathematical expressions |
| analyze_field_data | Analyze Field | Compute gradients, vorticity, divergence, Q-criterion |
| create_vector_visualization | Vector Glyphs | Create glyph-based vector visualization |

#### Pipeline Management

| Skill ID | Name | Description |
|----------|------|-------------|
| get_pipeline | Get Pipeline | Get current pipeline structure |
| get_available_arrays | Get Arrays | List available data arrays |
| set_active_source | Set Active Source | Set active pipeline object by name |
| get_active_source_names_by_type | Get Sources by Type | List sources filtered by type |

#### Data Export

| Skill ID | Name | Description |
|----------|------|-------------|
| export_data | Export Data | Export to CSV, VTK, STL, PLY, OBJ |
| save_contour_as_stl | Save STL | Save contour as STL file |
| save_paraview_state | Save State | Save complete ParaView state file |
| save_txt_file | Save Text | Save text content to file |
| transform_data | Transform Data | Apply geometric transformations |

#### Utility

| Skill ID | Name | Description |
|----------|------|-------------|
| list_commands | List Commands | List all available MCP tools |

### Tools and permissions

All tools interact with ParaView through the pvserver connection:

- **Data Loading Tools**: Read files from disk, load into ParaView memory
- **Visualization Tools**: Create and modify ParaView pipeline objects
- **Export Tools**: Write files to disk (STL, state files, screenshots)
- **Analysis Tools**: Compute derived quantities (read-only)
- Required permissions: File system access for data loading/saving

### Service endpoint and discovery

- Base URL: Local execution via pvserver connection
- Transport: STDIO (for Claude Desktop integration)
- Invocation: Add to Claude Desktop config or run as standalone MCP server

## Runtime Infrastructure

The agent runs as a Python process that connects to a ParaView server.

### Hardware

- No special hardware required
- GPU recommended for complex volume rendering

### Software

Python 3.8+ with dependencies:

```txt
mcp>=0.1.0
Pillow>=9.0.0
```

Optional development dependencies in pyproject.toml.

Requires ParaView with pvserver capability.

## Papers and Scientific Outputs

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

## Agent License

BSD 3-Clause License

Copyright (c) 2018, Lawrence Livermore National Security, LLC. All rights reserved.

LLNL-CODE-2007260

## Contact Info and Card Authors

- **Shusen Liu** - liu42@llnl.gov
- **Haichao Miao** - miao1@llnl.gov

# Intended Uses

## Intended Use

ParaView-MCP is designed for:

1. **Interactive scientific visualization** - Enable researchers to create visualizations through natural language
2. **Automated visualization pipelines** - Generate standard visualizations from data files
3. **Educational purposes** - Help newcomers learn ParaView concepts through conversational interaction
4. **Accessibility** - Make ParaView's powerful capabilities available without learning complex APIs

### Primary Intended Users

- Scientific researchers working with simulation data
- Domain experts who need visualizations but lack ParaView expertise
- Data scientists exploring volumetric and mesh datasets
- Educators teaching scientific visualization concepts

### Mission Relevance

Supports DOE mission by democratizing access to advanced scientific visualization capabilities, enabling faster analysis of simulation results and experimental data.

## Out-of-Scope Use Cases

- **Production batch rendering** - Not optimized for high-throughput rendering jobs
- **Real-time visualization** - MCP round-trip adds latency unsuitable for real-time needs
- **Mission-critical automation** - LLM outputs may be unpredictable; human verification recommended
- **Protected/classified data** - No security hardening for sensitive data handling

# How to use

## Install Instructions

```bash
# Clone the repository
git clone https://github.com/LLNL/paraview_mcp.git
cd paraview_mcp

# Create and activate conda environment
conda env create -f environment.yml
conda activate paraview_mcp

# Install in development mode
pip install -e .
```

## Agent configuration

### Claude Desktop Integration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ParaView": {
      "command": "/path/to/python",
      "args": ["/path/to/paraview_mcp/src/paraview_mcp_server.py"]
    }
  }
}
```

### ParaView Server

```bash
# Start ParaView server
pvserver --multi-clients

# Connect ParaView GUI to server (File -> Connect)
```

## Invocation / integration

1. Start pvserver with `--multi-clients`
2. Connect ParaView GUI to the server
3. Start Claude Desktop (or other MCP-compatible client)
4. Issue natural language commands

# Code snippets of how to use the agent

Example conversation with Claude Desktop:

```
User: Load the brain scan from /data/brain_256x256x256_uint8.raw with dimensions 256x256x256

User: Create volume rendering and set opacity to make values below 50 transparent

User: Rotate the camera to show the side view and take a screenshot

User: Save the contour as brain_surface.stl
```

# Limitations

## Risks

### Agent-specific risk notes (tool use)

- **File system access**: The agent can read and write files. Ensure data paths are properly restricted.
- **ParaView state modification**: Operations modify the ParaView session state in the connected GUI.
- **Resource consumption**: Complex visualizations may consume significant memory/GPU resources.
- **No sandboxing**: Commands execute with full user permissions.

### Prompt injection considerations

- Natural language inputs could potentially be crafted to execute unintended operations
- LLM may misinterpret ambiguous commands

## Limitations

- **Network dependency**: Requires active connection to pvserver
- **LLM variability**: Different LLMs may interpret commands differently
- **Complex workflows**: Multi-step workflows may require multiple conversational turns
- **Data format support**: Limited to formats supported by ParaView
- **No undo**: Operations cannot be undone except by reloading data

# Agent evaluation details

The project includes:

- **Unit tests**: `pytest tests/test_paraview_manager_live.py`
- **Promptfoo evaluation**: `promptfoo eval -c eval/eval_claude.yaml`
- **Data anonymization**: Tools to prevent evaluation bias

Evaluation criteria:
- Task success rate
- Tool call correctness
- Visualization accuracy vs. reference images

# More Information

- **Video Demo**: https://youtu.be/GvcBnAcIXp4
- **GitHub Repository**: https://github.com/LLNL/paraview_mcp
- **Documentation**: https://github.com/LLNL/paraview_mcp
