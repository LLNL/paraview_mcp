"""
ParaView MCP Server

This script runs as a standalone process and:
1. Connects to ParaView using its Python API over network
2. Exposes key ParaView functionality through the MCP protocol
3. Updates visualizations in the existing ParaView viewport

Usage:
1. Start pvserver with --multi-clients flag (e.g., pvserver --multi-clients --server-port=11111)
2. Start ParaView app and connect to the server
3. Configure Claude Desktop to use this script

"""
import os
import sys
import logging
import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
from paraview_manager import ParaViewManager

# Configure logging
log_dir = Path.home() / "paraview_logs"
os.makedirs(log_dir, exist_ok=True)
log_file = log_dir / "paraview_mcp_external.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

# Default prompt that instructs Claude how to interact with ParaView
default_prompt = """
When using ParaView through this interface, please follow these guidelines:

1. IMPORTANT: Only call strictly necessary ParaView functions per reply (and please limit the total number of call per reply). This ensures operations execute in a more interative manner and no excessive calls to related but non-essential functions. 

2. The only execute multiple repeated function call when given a target goal (e.g., identify a specific object), where different parameters need to used (e.g., isosurface with different isovalue). Avoid repeated calling of color map function unless user specific ask for color map design.

3. Paraview will be connect to mcp server on starup so no need to connect first.


"""
    
logger = logging.getLogger("pv_external_mcp")

# Create the ParaView manager with configurable screenshot compression
# Environment variables can override defaults:
# - PARAVIEW_COMPRESS_SCREENSHOTS: "true"/"false" (default: true)
# - PARAVIEW_MAX_SCREENSHOT_WIDTH: integer (default: 1280)
# - PARAVIEW_SCREENSHOT_QUALITY: 1-100 (default: 85)

import os
compress_screenshots = os.environ.get("PARAVIEW_COMPRESS_SCREENSHOTS", "true").lower() == "true"
max_width = int(os.environ.get("PARAVIEW_MAX_SCREENSHOT_WIDTH", "1280"))
quality = int(os.environ.get("PARAVIEW_SCREENSHOT_QUALITY", "85"))

pv_manager = ParaViewManager(
    compress_screenshots=compress_screenshots,
    max_screenshot_width=max_width,
    screenshot_quality=quality
)

# Initialize FastMCP server for Claude Desktop integration with default prompt
mcp = FastMCP("ParaView", instructions=default_prompt)

# ============================================================================
# MCP Tools for ParaView
# ============================================================================

@mcp.tool()
def load_data(file_path: str) -> str:
    """
    Load data from a file into ParaView.
    
    Args:
        file_path: Path to the data file (supports VTK, EXODUS, CSV, RAW, etc.)
    
    Returns:
        Status message
    """
    success, message, _, source_name = pv_manager.load_data(file_path)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def load_raw_data(file_path: str,
                 dimensions: list[float],
                 data_type: str = 'uint8',
                 byte_order: str = 'LittleEndian',
                 spacing: list[float] = None,
                 num_components: int = 1) -> str:
    """
    Load RAW volume data with explicit specifications.

    This function is essential for loading raw binary data files that don't have
    embedded metadata. You must specify the dimensions and data type.

    Args:
        file_path: Path to the RAW data file
        dimensions: List of 3 values [x, y, z] specifying the data dimensions
        data_type: Data type - one of: 'uint8', 'uint16', 'int8', 'int16', 'float32', 'float64'
        byte_order: Byte order - either 'LittleEndian' (default) or 'BigEndian'
        spacing: Optional list of 3 values [sx, sy, sz] for data spacing. Default is [1, 1, 1]
        num_components: Number of scalar components (default: 1)

    Returns:
        Status message indicating success or failure

    Examples:
        # Load a 256x256x256 uint8 volume
        load_raw_data("data.raw", [256, 256, 256], "uint8")

        # Load a 512x512x128 float32 volume with custom spacing
        load_raw_data("volume.raw", [512, 512, 128], "float32", spacing=[0.5, 0.5, 2.0])

        # Load multi-component data
        load_raw_data("vector_field.raw", [128, 128, 128], "float32", num_components=3)
    """
    # Convert lists to tuples for the manager
    dims_tuple = tuple(dimensions) if dimensions else (256, 256, 256)

    # Handle spacing
    if spacing:
        spacing_tuple = tuple(spacing)
    else:
        spacing_tuple = (1, 1, 1)

    # Validate data type
    valid_types = ['uint8', 'uint16', 'int8', 'int16', 'float32', 'float64']
    if data_type not in valid_types:
        return f"Invalid data_type '{data_type}'. Must be one of: {', '.join(valid_types)}"

    # Call the manager's load_raw_data method
    success, message, _, source_name = pv_manager.load_raw_data(
        file_path,
        dimensions=dims_tuple,
        data_type=data_type,
        byte_order=byte_order,
        spacing=spacing_tuple,
        num_components=num_components
    )

    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def save_contour_as_stl(stl_filename: str = "contour.stl") -> str:
    """
    Save the currently active contour (or any surface/mesh source) as an STL file
    in the same folder as the originally loaded data.

    Args:
        stl_filename: The STL file name to use, defaults to 'contour.stl'.

    Returns:
        A status message (string).
    """
    success, message, path = pv_manager.save_contour_as_stl(stl_filename)
    return message

@mcp.tool()
def save_paraview_state(save_directory: str, filename: str = "paraview_state.pvsm") -> str:
    """
    Save the current ParaView state to a file in the specified directory.
    This saves the complete visualization pipeline, camera settings, and all current configurations.
    
    Args:
        save_directory: Directory path where the state file will be saved
        filename: Name of the state file (default: "paraview_state.pvsm"). .pvsm extension will be added if not present.
    
    Returns:
        Status message with the full path to the saved state file
    """
    success, message, file_path = pv_manager.save_state(save_directory, filename)
    if success:
        return f"{message}"
    else:
        return message

@mcp.tool()
def save_txt_file(file_path: str, content: str) -> str:
    """
    Save text content to a file at the specified path.
    
    Args:
        file_path: Full path where the text file will be saved (including filename and extension)
        content: Text content to write to the file
    
    Returns:
        Status message indicating success or failure
    """
    try:
        from pathlib import Path
        
        # Convert to Path object for easier handling
        path = Path(file_path)
        
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content to file
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Successfully saved text file to: {path}")
        return f"Successfully saved text file to: {path}"
        
    except Exception as e:
        logger.error(f"Error saving text file: {str(e)}")
        return f"Error saving text file: {str(e)}"

@mcp.tool()
def create_source(source_type: str) -> str:
    """
    Create a new geometric source.
    
    Args:
        source_type: Type of source to create (Sphere, Cone, Cylinder, Plane, Box)
    
    Returns:
        Status message
    """
    success, message, _, source_name = pv_manager.create_source(source_type)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def create_isosurface(value: float, field: str = None) -> str:
    """
    Create an isosurface visualization of the active source.
    
    Args:
        value: Isovalue
        field: Optional field name to contour by
    
    Returns:
        Status message
    """
    success, message, contour_obj, contour_name = pv_manager.create_isosurface(value, field)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Filter registered as '{contour_name}'."
    else:
        return message

@mcp.tool()
def create_slice(origin_x: float = None, origin_y: float = None, origin_z: float = None,
                 normal_x: float = 0, normal_y: float = 0, normal_z: float = 1) -> str:
    """
    Create a slice through the loaded volume data.
    
    Args:
        origin_x, origin_y, origin_z: Coordinates for the slice plane's origin. If None,
            defaults to the data set's center.
        normal_x, normal_y, normal_z: Normal vector for the slice plane (default [0, 0, 1]).
    
    Returns:
        A string message containing success/failure details, plus the pipeline name.
    """
    success, message, slice_filter, slice_name = pv_manager.create_slice(
        origin_x,
        origin_y,
        origin_z,
        normal_x,
        normal_y,
        normal_z
    )

    # Return either an error message or a success message including the slice's name
    return message if success else f"Error creating slice: {message}"

@mcp.tool()
def create_clip(origin_x: float = None, origin_y: float = None, origin_z: float = None,
                normal_x: float = 1, normal_y: float = 0, normal_z: float = 0,
                invert: bool = False) -> str:
    """
    Create a clip filter to cut the data with a plane.
    
    Args:
        origin_x, origin_y, origin_z: Coordinates for the clip plane's origin. If None,
            defaults to the data set's center.
        normal_x, normal_y, normal_z: Normal vector for the clip plane (default [1, 0, 0] for y-z plane).
        invert (bool): If False, keeps the positive side of the plane normal (default).
                      If True, keeps the negative side of the plane normal.
    
    Examples:
        - To clip with y-z plane at x=0, keeping -x half: create_clip(origin_x=0, normal_x=1, invert=True)
        - To clip with x-y plane at z=0, keeping +z half: create_clip(origin_z=0, normal_z=1, invert=False)
    
    Returns:
        A string message containing success/failure details, plus the pipeline name.
    """
    success, message, clip_filter, clip_name = pv_manager.create_clip(
        origin_x,
        origin_y,
        origin_z,
        normal_x,
        normal_y,
        normal_z,
        invert
    )
    
    # Return either an error message or a success message including the clip's name
    return message if success else f"Error creating clip: {message}"

@mcp.tool()
def toggle_volume_rendering(enable: bool = True) -> str:
    """
    Toggle the visibility of volume rendering for the active source.
    
    Args:
        enable (bool): Whether to show (True) or hide (False) volume rendering.
                      If True, shows volume rendering (switching to 'Volume' representation if needed).
                      If False, hides the volume but preserves the volume representation settings.
    
    Returns:
        Status message
    """
       
    success, message, source_name = pv_manager.create_volume_rendering(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def toggle_visibility(enable: bool = True) -> str:
    """
    Toggle the visibility for the active source.
    
    Args:
        enable (bool): Whether to show (True) or hide (False) the active source.
                      If True, makes the active source visible.
                      If False, hides the active source but preserves the representation settings.
    
    Returns:
        Status message
    """
       
    success, message, source_name = pv_manager.toggle_visibility(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message


@mcp.tool()
def set_active_source(name: str) -> str:
    """
    Set the active pipeline object by its name.

    Usage:
      set_active_source("Contour1")

    Returns a status message.
    """
    success, message = pv_manager.set_active_source(name)
    return message

@mcp.tool()
def get_active_source_names_by_type(source_type: str = None) -> str:
    """
    Get a list of source names filtered by their type.

    Args:
        source_type (str, optional): Filter sources by type (e.g., 'Sphere', 'Contour', etc.).
                                  If None, returns all sources.

    Returns:
        A string message containing the source names or error message.
    """
    success, message, source_names = pv_manager.get_active_source_names_by_type(source_type)
    
    if success and source_names:
        sources_list = "\n- ".join(source_names)
        result = f"{message}:\n- {sources_list}"
        return result
    else:
        return message

# @mcp.tool()
# def edit_volume_opacity(field_name: str, opacity_points: list[tuple[float, float]]) -> str:
#     """
#     Edit ONLY the opacity transfer function for the specified field,
#     ensuring we pass only (value, alpha) pairs.

#     [Tips: only needed by volume rendering particularly finetuning the result, likely not needed when the color is ideal, usually the lower value should always have lower opacity]

#     Args:
#         field_name (str): The data array (field) name whose opacity we're adjusting.
#         opacity_points (list of [value, alpha] pairs):
#             Example: [[0.0, 0.0], [50.0, 0.3], [100.0, 1.0]]

#     Returns:
#         A status message (success or error)
#     """
#     success, message = pv_manager.edit_volume_opacity(field_name, opacity_points)
#     return message

# Compatible with OpenAI tool using
@mcp.tool()
def edit_volume_opacity(field_name: str, opacity_points: list[dict[str, float]]) -> str:
    """
    Edit ONLY the opacity transfer function for the specified field.

    Args:
        field_name (str): The scalar field to modify.
        opacity_points (list): A list of dicts like:
            [{"value": 0.0, "alpha": 0.0}, {"value": 50.0, "alpha": 0.3}]

    Returns:
        A status message (success or error)
    """
    formatted_points = [[pt["value"], pt["alpha"]] for pt in opacity_points]
    success, message = pv_manager.edit_volume_opacity(field_name, formatted_points)
    return message

# @mcp.tool()
# def set_color_map(field_name: str, color_points: list[tuple[float, tuple[float, float, float]]]) -> str:
#     """
#     Sets the color transfer function for the specified field.

#     [Tips: only volume rendering should be using the set_color_map function, the lower values range corresponds to lower density objects, whereas higher values indicate high physical density. When design the color mapping try to assess the object of interest's density first from the default colormap (low value assigned to blue, high value assigned to red) and re-assign customized color accordingly, the order of the color may need to be adjust based on the rendering result. The more solid object should have higher density (!high value range). And a screen_shot should always be taken once this function is called to assess how to adjust the color_map again.]

#     Args:
#         field_name (str): The name of the field/array (as it appears in ParaView).
#         color_points (list of [value, [r, g, b]]):
#             e.g., [[0.0, [0.0, 0.0, 1.0]], [50.0, [0.0, 1.0, 0.0]], [100.0, [1.0, 0.0, 0.0]]]
#             Each element is (value, (r, g, b)) with r,g,b in [0,1].

#     Returns:
#         A status message as a string (e.g., success or error).
#     """
#     success, message = pv_manager.set_color_map(field_name, color_points)
#     return message

@mcp.tool()
def set_color_map(field_name: str, color_points: list[dict]) -> str:
    """
    Sets the color transfer function for the specified field.

    [Tips: only volume rendering should be using the set_color_map function, the lower values range corresponds to lower density objects, whereas higher values indicate high physical density. When design the color mapping try to assess the object of interest's density first from the default colormap (low value assigned to blue, high value assigned to red) and re-assign customized color accordingly, the order of the color may need to be adjust based on the rendering result. The more solid object should have higher density (!high value range). And a screen_shot should always be taken once this function is called to assess how to adjust the color_map again.]

    Args:
        field_name (str): The name of the field/array (as it appears in ParaView).
        color_points (list of dicts): Each element should be a dict:
            {"value": float, "rgb": [r, g, b]} where r,g,b ∈ [0,1].

            Example:
            [
                {"value": 0.0, "rgb": [0.0, 0.0, 1.0]},
                {"value": 50.0, "rgb": [0.0, 1.0, 0.0]},
                {"value": 100.0, "rgb": [1.0, 0.0, 0.0]}
            ]

    Returns:
        A status message (success or error).
    """
    # Transform color_points to expected internal format: list[tuple[float, tuple[float, float, float]]]
    try:
        formatted_points = [(pt["value"], tuple(pt["rgb"])) for pt in color_points]
    except Exception as e:
        return f"Invalid format for color_points: {e}"

    success, message = pv_manager.set_color_map(field_name, formatted_points)
    return message


@mcp.tool()
def color_by(field: str, component: int = -1) -> str:
    """
    Color the active visualization by a specific field.
    This function first checks if the active source can be colored by fields
    (i.e., it's a dataset with arrays) before attempting to apply colors.
    [tips] Volume rendering should not use this function 

    Args:
        field: Field name to color by
        component: Component to color by (-1 for magnitude)
    
    Returns:
        Status message
    """
    success, message = pv_manager.color_by(field, component)
    return message

@mcp.tool()
def compute_surface_area() -> str:
    """
    Compute the surface area of the currently active dataset.
    NOTE: Must be a surface mesh or 'Area' array won't exist.
    """
    success, message, area_value = pv_manager.compute_surface_area()
    return message

# @mcp.tool()
# def set_color_map_preset(preset_name: str) -> str:
#     """
#     Set the color map (lookup table) for the current visualization.
#     [tips: this should only be call at the beginning of the volume rendering]

#     Args:
#         preset_name: Name of the color map preset (e.g., "Rainbow", "Cool to Warm", "viridis")
    
#     Returns:
#         Status message
#     """
#     success, message = pv_manager.set_color_map(preset_name)
#     return message

@mcp.tool()
def set_representation_type(rep_type: str) -> str:
    """
    Set the representation type for the active source.
    
    [Tips: This function should not be used for volume rendering]

    Args:
        rep_type: Representation type (Surface, Wireframe, Points, etc.)
    
    Returns:
        Status message
    """
    success, message = pv_manager.set_representation_type(rep_type)
    return message

@mcp.tool()
def get_pipeline() -> str:
    """
    Get the current pipeline structure.
    
    Returns:
        Description of the current pipeline
    """
    success, message = pv_manager.get_pipeline()
    return message

@mcp.tool()
def get_available_arrays() -> str:
    """
    Get a list of available arrays in the active source.

    [tips: normally volume rendering would not require this information]
    
    Returns:
        List of available arrays
    """
    success, message = pv_manager.get_available_arrays()
    return message

@mcp.tool()
def create_streamline(seed_point_number: int, vector_field: str = None,
                     integration_direction: str = "BOTH", max_steps: int = 1000,
                     initial_step: float = 0.1, maximum_step: float = 50.0) -> str:
    """
    Create streamlines from the loaded vector volume using the StreamTracer filter.
    This function automatically generates seed points based on the data bounds.
    
    Args:
        seed_point_number (int): The number of seed points to automatically generate.
        vector_field (str, optional): The name of the vector field to use for tracing. 
                                    If None, the first vector field will be chosen automatically.
        integration_direction (str): Integration direction ("FORWARD", "BACKWARD", or "BOTH"; default: "BOTH").
        max_steps (int): Maximum number of integration steps (default: 1000).
        initial_step (float): Initial integration step length (default: 0.1).
        maximum_step (float): Maximum streamline length (default: 50.0).
        
    Returns:
        str: Status message indicating whether the streamline was successfully created.
    """
    # Call the stream tracer creation method in your ParaViewManager
    success, message, streamline, tube_name = pv_manager.create_stream_tracer(
        vector_field=vector_field,
        base_source=None,  # Use the active source
        point_center=None,  # Auto-calculate the center
        integration_direction=integration_direction,
        initial_step_length=initial_step,
        maximum_stream_length=maximum_step,
        number_of_streamlines=seed_point_number
    )
    
    if success:
        return f"{message} Tube registered as '{tube_name}'."
    else:
        return message

@mcp.tool()
def configure_screenshot_compression(
    enable_compression: bool = None,
    max_width: int = None,
    quality: int = None
) -> str:
    """
    Configure screenshot compression settings to reduce token usage.

    This is useful for managing API token limits when screenshots are too large.
    Default settings compress images to ~100-200KB instead of several MB.

    Args:
        enable_compression: Enable/disable compression. None keeps current setting.
        max_width: Maximum width in pixels (height scales proportionally). None keeps current.
        quality: JPEG quality 1-100 (85 recommended). None keeps current.

    Returns:
        Current settings after update
    """
    if enable_compression is not None:
        pv_manager.compress_screenshots = enable_compression

    if max_width is not None and max_width > 0:
        pv_manager.max_screenshot_width = max_width

    if quality is not None and 1 <= quality <= 100:
        pv_manager.screenshot_quality = quality

    return (f"Screenshot settings: compression={'enabled' if pv_manager.compress_screenshots else 'disabled'}, "
            f"max_width={pv_manager.max_screenshot_width}px, "
            f"quality={pv_manager.screenshot_quality}")

@mcp.tool()
def get_screenshot() -> str:
    """
    Capture a screenshot of the current view and display it in chat.

    By default, screenshots are compressed to reduce token usage (JPEG, max 1280px width).
    Use configure_screenshot_compression() to adjust settings if needed.

    Returns:
        Compressed image for display in chat
    """
    success, message, img_path = pv_manager.get_screenshot()    

    if not success:
        return message
    else:
        return Image(path=img_path)
    
@mcp.tool()
def rotate_camera(azimuth: float = 30.0, elevation: float = 0.0) -> str:
    """
    Rotate the camera by specified angles.
    
    Args:
        azimuth: Rotation around vertical axis in degrees
        elevation: Rotation around horizontal axis in degrees
    
    Returns:
        Status message
    """
    success, message = pv_manager.rotate_camera(azimuth, elevation)
    return message

@mcp.tool()
def reset_camera(padding_factor: float = 1.5) -> str:
    """
    Reset the camera to show all data with optional padding for better framing.

    Args:
        padding_factor (float): Multiplier for camera distance to add padding around objects.
                               1.0 = no padding, 1.5 = 50% padding (default for better framing),
                               2.0 = 100% padding. Recommended range: 1.0-2.0.

    Tips: Use 1.5 (default) for evaluation/screenshots to ensure objects are well-framed.
          Use 1.0 for tight framing when you need to see details.

    Returns:
        Status message
    """
    success, message = pv_manager.reset_camera(padding_factor)
    return message

@mcp.tool()
def reset_colormaps(array_name: str = None) -> str:
    """
    Reset colormaps and transfer functions to default settings.

    This is useful after loading new data or when colormaps become misconfigured.
    Resets both color and opacity transfer functions to sensible defaults.

    Args:
        array_name: Specific array name to reset. If None, resets common arrays
                   like 'ImageFile', 'MetaImage', 'Scalars_', 'RTData', 'PointData'.

    Returns:
        Status message indicating which colormaps were reset
    """
    success, message = pv_manager.reset_colormaps(array_name)
    return message

@mcp.tool()
def plot_over_line(point1: list[float] = None, point2: list[float] = None, resolution: int = 100) -> str:
    """
    Create a 'Plot Over Line' filter to sample data along a line between two points.

    Args:
        point1 (list of float): The [x, y, z] coordinates of the start point. If None, will use data bounds.
        point2 (list of float): The [x, y, z] coordinates of the end point. If None, will use data bounds.
        resolution (int): Number of sample points along the line (default: 100).

    Returns:
        Status message
    """
    success, message, plot_filter = pv_manager.plot_over_line(point1, point2, resolution)
    return message


@mcp.tool()
def warp_by_vector(vector_field: str = None, scale_factor: float = 1.0) -> str:
    """
    Apply the 'Warp By Vector' filter to the active source.

    Args:
        vector_field (str, optional): The name of the vector field to use for warping. If None, the first available vector field will be used.
        scale_factor (float, optional): The scale factor for the warp (default: 1.0).

    Returns:
        Status message
    """
    success, message, warp_filter = pv_manager.warp_by_vector(vector_field, scale_factor)
    return message

@mcp.tool()
def clear_pipeline_and_reset() -> str:
    """
    Clear the entire ParaView rendering pipeline and reset to a fresh state,
    equivalent to restarting the application.

    This function:
    - Deletes all sources and filters from the pipeline
    - Resets all internal references
    - Resets the camera and view settings
    - Reinitializes colormaps and transfer functions
    - Clears any cached data
    
    Returns:
        Status message indicating success or failure
    """
    success, message = pv_manager.clear_pipeline_and_reset()
    return message

@mcp.tool()
def set_background_color(red: float = 0.32, green: float = 0.34, blue: float = 0.43) -> str:
    """
    Set the background color of the active view.
    
    Args:
        red (float): Red component (0.0 to 1.0). Default: 0.32
        green (float): Green component (0.0 to 1.0). Default: 0.34  
        blue (float): Blue component (0.0 to 1.0). Default: 0.43
        
    Note:
        Default values approximate ParaView's default dark background.
        
    Returns:
        Status message indicating the new background color
    """
    success, message = pv_manager.set_background_color(red, green, blue)
    return message

@mcp.tool()
def get_histogram(field: str = None, num_bins: int = 256, data_location: str = "POINTS") -> str:
    """
    Compute and retrieve histogram data for a field in the active data source.
    This function is designed to work with volume sources. By default it uses the
    point data arrays (data_location="POINTS"), but you can specify "CELLS" if your
    volume source stores scalars on cells.

    If no field is provided and the active source contains exactly one available numeric 
    field in the specified data location, that field is automatically used. If multiple 
    arrays exist, the user must specify which field to use.

    Args:
        field (str, optional): The name of the field for which the histogram is computed.
        num_bins (int, optional): Number of histogram bins (default is 256).
        data_location (str, optional): Specify "POINTS" (default) or "CELLS" to indicate the source of the data.
        
    Returns:
        Status message with histogram data formatted as string
    """
    success, message, histogram_data = pv_manager.get_histogram(field, num_bins, data_location)
    if success and histogram_data:
        # Format histogram data as readable string
        hist_str = f"{message}\nHistogram data:\n"
        for bin_center, frequency in histogram_data[:10]:  # Show first 10 bins
            hist_str += f"  Bin {bin_center:.2f}: {frequency}\n"
        if len(histogram_data) > 10:
            hist_str += f"  ... ({len(histogram_data) - 10} more bins)"
        return hist_str
    return message

@mcp.tool()
def filter_data(filter_type: str = "threshold", field_name: str = None, min_value: float = None, 
                max_value: float = None, invert: bool = False, all_points: bool = False) -> str:
    """
    Apply data filtering operations including threshold and selection extraction.
    Combines threshold and extract selection functionality into a single versatile filter.
    
    Args:
        filter_type (str): Type of filter - "threshold" or "extract_selection"
        field_name (str, optional): Name of the scalar field to filter by. Auto-detected if None.
        min_value (float, optional): Minimum threshold value
        max_value (float, optional): Maximum threshold value  
        invert (bool): Whether to invert the selection (keep values outside range)
        all_points (bool): For threshold - whether to include all points in cells that pass
        
    Returns:
        Status message
    """
    success, message, filter_obj, filter_name = pv_manager.filter_data(
        filter_type, field_name, min_value, max_value, invert, all_points
    )
    if success:
        return f"{message}. Filter registered as '{filter_name}'."
    else:
        return message

@mcp.tool()
def calculate_field(result_name: str, expression: str, attribute_mode: str = "Point Data") -> str:
    """
    Apply mathematical calculations to create new data fields.
    Combines calculator functionality with support for common mathematical operations.
    
    Args:
        result_name (str): Name for the new calculated field
        expression (str): Mathematical expression to evaluate
                        Examples: "sqrt(velocity_X^2 + velocity_Y^2 + velocity_Z^2)"
                                "pressure * 2.0"  
                                "coords_X + coords_Y + coords_Z"
        attribute_mode (str): "Point Data" or "Cell Data" - where to store result
        
    Returns:
        Status message
    """
    success, message, calc_filter, calc_name = pv_manager.calculate_field(result_name, expression, attribute_mode)
    if success:
        return f"{message}. Calculator registered as '{calc_name}'."
    else:
        return message

@mcp.tool()  
def transform_data(operation: str = "translate", translate_x: float = 0.0, translate_y: float = 0.0, 
                   translate_z: float = 0.0, rotate_x: float = 0.0, rotate_y: float = 0.0, 
                   rotate_z: float = 0.0, scale_x: float = 1.0, scale_y: float = 1.0, scale_z: float = 1.0) -> str:
    """
    Apply geometric transformations to datasets.
    Combines translation, rotation, and scaling into a single versatile transform operation.
    
    Args:
        operation (str): Transform type - "translate", "rotate", "scale", or "combined"
        translate_x, translate_y, translate_z (float): Translation amounts
        rotate_x, rotate_y, rotate_z (float): Rotation angles in degrees
        scale_x, scale_y, scale_z (float): Scale factors
        
    Returns:
        Status message
    """
    success, message, transform_filter, transform_name = pv_manager.transform_data(
        operation, translate_x, translate_y, translate_z, 
        rotate_x, rotate_y, rotate_z, scale_x, scale_y, scale_z
    )
    if success:
        return f"{message}. Transform registered as '{transform_name}'."
    else:
        return message

@mcp.tool()
def create_vector_visualization(glyph_type: str = "arrow", vector_field: str = None, scale_factor: float = None,
                               scale_mode: str = "vector", max_number_of_glyphs: int = 5000, auto_scale: bool = True,
                               scale_percentage: float = 0.01) -> str:
    """
    Create vector field visualizations using glyphs.
    Combines glyph functionality for arrows, cones, spheres to visualize vector data.

    Args:
        glyph_type (str): Type of glyph - "arrow", "cone", "sphere", "line"
        vector_field (str, optional): Name of vector field. Auto-detected if None.
        scale_factor (float, optional): Overall scaling factor for glyphs. Auto-computed based on data bounds if None.
        scale_mode (str): "vector", "scalar", or "off" - how to scale glyphs
        max_number_of_glyphs (int): Maximum number of glyphs to display
        auto_scale (bool): Automatically compute scale factor based on data bounds if scale_factor is None (default: True)
        scale_percentage (float): Percentage of data diagonal for auto-scaling (0.01 = 1%, 0.005 = 0.5%). Default: 0.01

    Tips:
    - If glyphs appear too large, reduce scale_factor (e.g., 0.001) or scale_percentage (e.g., 0.005)
    - Default auto-scaling uses 1% of data diagonal. Try 0.005 (0.5%) or 0.002 (0.2%) for smaller glyphs

    Returns:
        Status message
    """
    success, message, glyph_filter, glyph_name = pv_manager.create_vector_visualization(
        glyph_type, vector_field, scale_factor, scale_mode, max_number_of_glyphs, auto_scale, scale_percentage
    )
    if success:
        return f"{message}. Glyph filter registered as '{glyph_name}'."
    else:
        return message

@mcp.tool()
def analyze_field_data(analysis_type: str = "gradient", field_name: str = None, compute_vorticity: bool = False,
                       compute_divergence: bool = False, compute_qcriterion: bool = False) -> str:
    """
    Analyze field data including gradients, derivatives, and connectivity.
    Combines gradient computation and connectivity analysis into a unified interface.
    
    Args:
        analysis_type (str): "gradient", "connectivity", or "combined" 
        field_name (str, optional): Field to analyze. Auto-detected if None.
        compute_vorticity (bool): Compute vorticity for vector fields
        compute_divergence (bool): Compute divergence for vector fields  
        compute_qcriterion (bool): Compute Q-criterion for vector fields
        
    Returns:
        Status message
    """
    success, message, analysis_filter, filter_name = pv_manager.analyze_field_data(
        analysis_type, field_name, compute_vorticity, compute_divergence, compute_qcriterion
    )
    if success:
        return f"{message}. Analysis filter registered as '{filter_name}'."
    else:
        return message

@mcp.tool()
def export_data(export_format: str = "csv", filename: str = None, export_type: str = "all") -> str:
    """
    Export data in various formats with enhanced capabilities.
    Combines multiple export formats into a single versatile function.
    
    Args:
        export_format (str): "csv", "vtk", "stl", "ply", "obj"
        filename (str, optional): Output filename. Auto-generated if None.
        export_type (str): "all", "points", "cells", "arrays" - what to export
        
    Returns:
        Status message with export path
    """
    success, message, export_path = pv_manager.export_data(export_format, filename, export_type)
    return message

@mcp.tool()
def create_delaunay3d(alpha: float = 0.0, offset: float = 2.0, tolerance: float = 0.001) -> str:
    """
    Create a 3D Delaunay triangulation of the active dataset.
    
    Args:
        alpha (float): Specify alpha (or distance) value to control output. For non-zero alpha value, 
                      only edges or triangles contained within alpha radius are output. 
                      Default is 0.0 which produces the convex hull.
        offset (float): Offset to multiply the radius of the circumsphere by. Default is 2.0.
        tolerance (float): Specify a tolerance to control discarding of degenerate tetrahedra. Default is 0.001.
    
    Returns:
        Status message
    """
    success, message, delaunay_filter, delaunay_name = pv_manager.create_delaunay3d(alpha, offset, tolerance)
    if success:
        return f"{message}. Filter registered as '{delaunay_name}'."
    else:
        return message

@mcp.tool()
def list_commands() -> str:
    """
    List all available commands in this ParaView MCP server.
    
    Returns:
        List of available commands
    """
    commands = [
        "load_data: Load data from a file",
        "create_source: Create a geometric source (Sphere, Cone, etc.)",
        "create_isosurface: Create an isosurface visualization",
        "create_clip: Create a clip filter to cut data with a plane",
        "create_slice: Create a slice through the data",
        "create_delaunay3d: Create a 3D Delaunay triangulation of the dataset",
        "filter_data: Apply threshold and data selection filters",
        "calculate_field: Create new fields with mathematical expressions",
        "transform_data: Apply geometric transformations (translate/rotate/scale)",
        "create_vector_visualization: Visualize vector fields with glyphs (arrows/cones)",
        "analyze_field_data: Compute gradients, connectivity analysis",
        "export_data: Export data in multiple formats (CSV, VTK, STL, etc.)",
        "clear_pipeline_and_reset: Clear all pipeline objects and reset to fresh state",
        "set_background_color: Set the background color of the view",
        "toggle_volume_rendering: Enable or disable volume rendering",
        "toggle_visibility: Enable or disable visibility for the active source",
        "set_active_source: Set the active pipeline object by name",
        "get_active_source_names_by_type: Get a list of sources filtered by type",
        "color_by: Color the visualization by a field",
        # "set_color_map_preset: Set the color map preset",
        "set_color_map: Set custom color transfer function for volume rendering",
        "set_representation_type: Set the representation type (Surface, Wireframe, etc.)",
        "edit_volume_opacity: Edit the opacity transfer function",
        "get_pipeline: Get the current pipeline structure",
        "get_available_arrays: Get available data arrays",
        "get_histogram: Compute histogram for a data field",
        "create_streamline: Create stream line visualization with tubes",
        "compute_surface_area: Compute the surface area of the active surface",
        "save_contour_as_stl: Save the active surface as STL",
        "get_screenshot: Capture a screenshot and display it in chat",
        "rotate_camera: Rotate the camera view",
        "reset_camera: Reset the camera to show all data",
        "plot_over_line: Create a plot over line filter",
        "warp_by_vector: Warp the active source by a vector field",
        "save_paraview_state: Save the current ParaView state to a file",
        "save_txt_file: Save text content to a file",
    ]
    
    return "Available ParaView commands:\n\n" + "\n".join(commands)


def main():
    parser = argparse.ArgumentParser(description="ParaView External MCP Server")
    parser.add_argument("--server", type=str, default="localhost", help="ParaView server hostname (default: localhost)")
    parser.add_argument("--port", type=int, default=11111, help="ParaView server port (default: 11111)")
    parser.add_argument("--paraview_package_path", type=str, help="Path to the ParaView Python package", default=None)
    
    args = parser.parse_args()

    # Add the ParaView package path to sys.path
    if args.paraview_package_path:
        sys.path.append(args.paraview_package_path)
    
    # Connect to ParaView
    pv_manager.connect(args.server, args.port)
    
    # Run the MCP server
    try:
        logger.info("Starting ParaView External MCP Server")
        logger.info(f"ParaView server: {args.server}:{args.port}")
        # logger.info("Default prompt enabled: Claude will call one function per reply")
        
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Error running MCP server: {str(e)}")

if __name__ == "__main__":
    main()
