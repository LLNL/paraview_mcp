# Paraview_MCP

ParaView-MCP is an autonomous agent that integrates multimodal large language models with ParaView through the Model Context Protocol, enabling users to create and manipulate scientific visualizations using natural language and visual inputs instead of complex commands or GUI operations. The system features visual feedback capabilities that allow it to observe the viewport and iteratively refine visualizations, making advanced visualization accessible to non-experts while augmenting expert workflows with intelligent automation.

## Video Demo

Click the image below to watch the video:

[![Video Title](https://img.youtube.com/vi/GvcBnAcIXp4/maxresdefault.jpg)](https://youtu.be/GvcBnAcIXp4)


## Installation

The upstream installation uses conda. For a non-conda setup using an existing ParaView.app installation, see [LOCAL_SETUP.md](LOCAL_SETUP.md).

```shell
git clone https://github.com/LLNL/paraview_mcp.git
cd paraview_mcp

conda create -n paraview_mcp python=3.10
conda install conda-forge::paraview
conda install mcp[cli] httpx
```

## Setup for LLM

To set up integration with claude desktop, add the following to claude_desktop_config.json

```json
    "mcpServers": {
      "ParaView": {
        "command": "/path/to/python",
        "args": [
        "/path/to/paraview_mcp/paraview_mcp_server.py"
        ]
      }
    }
```

## running 

### 1. Start paraview server

```shell
python pvserver --multi-clients
```

### 2. Connect to paraview server from paraview GUI (file -> connect)

### 3. Start claude desktop app 

***

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
Paraview_MCP was created by Shusen Liu (liu42@llnl.gov) and Haichao Miao (miao1@llnl.gov)

## License
Paraview_MCP is distributed under the terms of the BSD-3 license.

LLNL-CODE-2007260
