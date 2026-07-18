"""
ParaView MCP Server

This class encapsulates paraview.simple API to expose a higher-level API that is compatible with LLM access/control.
"""

import logging
import re
from paraview.simple import *

class ParaViewManager:
    """
    Encapsulates all ParaView-specific functionality.
    This class handles the interaction with ParaView and provides
    a clean interface for the MCP server.
    """

    def __init__(self, compress_screenshots=True, max_screenshot_width=1280, screenshot_quality=85):
        """
        Initialize the ParaView manager

        Args:
            compress_screenshots (bool): Whether to compress screenshots to reduce token usage.
                                        Default: True (recommended for API usage)
            max_screenshot_width (int): Maximum width for screenshots when compression is enabled.
                                       Height is scaled proportionally. Default: 1280
            screenshot_quality (int): JPEG quality (1-100) when compression is enabled.
                                    Default: 85 (good quality with significant size reduction)
        """
        self.connection = None
        self.logger = logging.getLogger("paraview_manager")
        # This will always hold the originally loaded data source,
        # which is needed for operations like volume rendering.
        self.original_source = None
        self._data_folder = ""

        # Screenshot compression settings
        self.compress_screenshots = compress_screenshots
        self.max_screenshot_width = max_screenshot_width
        self.screenshot_quality = screenshot_quality
        self.logger.info(f"ParaViewManager initialized with screenshot compression: {compress_screenshots}")
    
    def _get_source_name(self, proxy):
        """
        Get the name (registered name) of a source proxy.
        
        Args:
            proxy: The source proxy object.
            
        Returns:
            str: The name of the proxy, or empty string if not found.
        """
        try:
            from paraview.simple import GetSources
            
            if proxy is None:
                return ""
                
            sources_dict = GetSources()
            for (key, src_proxy) in sources_dict.items():
                if src_proxy == proxy:
                    return key[0]  # Return the first element (name) of the key tuple
            
            return ""  # Return empty string if proxy not found
        except Exception as e:
            self.logger.error(f"Error getting source name: {str(e)}")
            return ""
    
    def connect(self, server_url="localhost", port=11111):
        """
        Connect to a running ParaView server
        
        Args:
            server_url: Server hostname (default: localhost)
            port: Server port (default: 11111)
            
        Returns:
            bool: Success status
        """
        try:
            # Import the paraview.simple module
            import importlib.util
            
            if importlib.util.find_spec("paraview.simple") is not None:
                from paraview.simple import Connect, GetActiveView
                
                # Connect to the existing ParaView server
                full_server_url = f"{server_url}:{port}" if port else server_url
                self.logger.info(f"Connecting to ParaView at {full_server_url}")
                self.connection = Connect(full_server_url)
                
                # Get the active view to confirm connection
                view = GetActiveView()
                
                self.logger.info("Successfully connected to ParaView")
                return True
            else:
                self.logger.warning("paraview.simple module not found. Running in simulation mode.")
                return False
        except Exception as e:
            self.logger.error(f"Failed to connect to ParaView: {str(e)}")
            return False
    
    def load_data(self, file_path):
        """
        Load data from a file into ParaView
        
        Args:
            file_path: Path to the data file (can be relative or absolute)
            
        Returns:
            tuple: (success, message, reader, source_name)
        """
        try:
            import os
            from paraview.simple import OpenDataFile, Show, GetActiveView

            # Handle relative paths by checking multiple possible base directories
            original_path = file_path
            
            # Convert to absolute path if it's relative
            if not os.path.isabs(file_path):
                # Try different base directories for relative paths
                possible_bases = [
                    os.getcwd(),  # Current working directory
                    os.path.dirname(os.path.dirname(__file__)),  # Project root (parent of src/)
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'eval')  # eval directory
                ]
                
                for base_dir in possible_bases:
                    test_path = os.path.join(base_dir, file_path)
                    if os.path.exists(test_path):
                        file_path = test_path
                        break
                else:
                    # If still not found, use absolute path of original
                    file_path = os.path.abspath(original_path)
            
            # Final check if file exists
            if not os.path.exists(file_path):
                self.logger.error(f"File not found: {file_path} (original: {original_path})")
                return False, f"File not found: {file_path} (tried from multiple locations)", None, ""
            
            # Record the directory of the loaded file so we can re-use it.
            self._data_folder = os.path.dirname(file_path)

            # Get file extension
            _, file_extension = os.path.splitext(file_path)
            file_extension = file_extension.lower()
            file_name = os.path.basename(file_path)

            # Special handling for raw volume files
            if file_extension == '.raw':
                reader = self._configure_raw_reader(file_path, file_name)
            else:
                # Standard loading for other file types
                reader = OpenDataFile(file_path)
            
            if not reader:
                return False, f"Failed to load data from {file_path}", None, ""

            # Show in the active view
            view = GetActiveView()
            if not view:
                # Create a render view if none exists
                from paraview.simple import CreateRenderView
                view = CreateRenderView()
                self.logger.info("Created new render view")

            display = Show(reader, view)

            # For RAW files, check if it's 3D and set appropriate representation
            if file_extension == '.raw' and hasattr(reader, 'DataExtent'):
                extent = reader.DataExtent
                # Check if this is 3D data (z dimension > 1)
                if extent and len(extent) >= 6 and extent[5] - extent[4] > 0:
                    # Use Outline representation to avoid slice mapper issues
                    display.SetRepresentationType('Outline')
                    self.logger.info("Set representation to Outline for 3D RAW data")
            # else:
                # display.ScaleFactor = 0.5

            view.ResetCamera()  # Allow full camera reset including clipping range

            # Add some padding by zooming out slightly for better framing
            cam = view.GetActiveCamera()
            if cam:
                cam.Dolly(0.7)  # Zoom out by 30% for better initial view
                from paraview.simple import Render
                Render()  # Update the view after camera adjustment

            # Save the loaded reader as the original data source
            self.original_source = reader
            
            # Get the source name using the helper function
            source_name = self._get_source_name(reader)
            
            return True, f"Successfully loaded data from {file_path}", reader, source_name
        except Exception as e:
            self.logger.error(f"Error loading data: {str(e)} file path{file_path}")
            return False, f"Error loading data: {str(e)} file path{file_path}", None, ""

    
    def _configure_raw_reader(self, file_path, file_name, dimensions=None, data_type=None,
                             byte_order=None, num_components=None):
        """
        Configure a reader for RAW volume files

        Args:
            file_path: Path to the RAW file
            file_name: Name of the file
            dimensions: Optional tuple of (x, y, z) dimensions
            data_type: Optional data type string (uint8, uint16, float32, etc.)
            byte_order: Optional byte order ('LittleEndian' or 'BigEndian')
            num_components: Optional number of scalar components

        Returns:
            reader: Configured reader object
        """
        from paraview.simple import OpenDataFile

        # If parameters not provided, try to parse from filename
        if dimensions is None or data_type is None:
            # Try to parse dimensions and data type from filename
            # Expected format: name_XxYxZ_datatype.raw (e.g., foot_256x256x256_uint8.raw)
            dimensions_match = re.search(r'(\d+)x(\d+)x(\d+)', file_name)
            datatype_match = re.search(r'_(uint8|uint16|int8|int16|float32|float64)', file_name.lower())
            scalar_components_match = re.search(r'_scalar(\d+)', file_name.lower())

            if dimensions is None and dimensions_match:
                dimensions = (int(dimensions_match.group(1)),
                            int(dimensions_match.group(2)),
                            int(dimensions_match.group(3)))

            if data_type is None and datatype_match:
                data_type = datatype_match.group(1)

            if num_components is None and scalar_components_match:
                num_components = int(scalar_components_match.group(1))

        # Load the raw file
        reader = OpenDataFile(file_path)
        if not reader:
            return None

        # Set reader properties
        if dimensions:
            dim_x, dim_y, dim_z = [int(d) for d in dimensions]
            reader.DataExtent = [0, dim_x-1, 0, dim_y-1, 0, dim_z-1]
            reader.FileDimensionality = 3
            self.logger.info(f"Set dimensions: {dim_x}x{dim_y}x{dim_z}")

        # Map to ParaView data types
        datatype_map = {
            'uint8': 'unsigned char',
            'uint16': 'unsigned short',
            'int8': 'char',
            'int16': 'short',
            'float32': 'float',
            'float64': 'double'
        }

        if data_type and data_type in datatype_map:
            reader.DataScalarType = datatype_map[data_type]
            self.logger.info(f"Set data type: {datatype_map[data_type]}")
        else:
            # Default to unsigned char if not specified
            reader.DataScalarType = 'unsigned char'

        # Set byte order
        if byte_order:
            reader.DataByteOrder = byte_order
        else:
            reader.DataByteOrder = 'LittleEndian'  # Default to LittleEndian

        # Set number of scalar components
        if num_components:
            reader.NumberOfScalarComponents = num_components
            self.logger.info(f"Set scalar components: {num_components}")
        else:
            reader.NumberOfScalarComponents = 1    # Default to single component

        self.logger.info(f"Configured RAW reader with: ScalarType={reader.DataScalarType}, " +
                         f"ByteOrder={reader.DataByteOrder}, Extent={reader.DataExtent}, " +
                         f"NumberOfScalarComponents={reader.NumberOfScalarComponents}")

        return reader
    

    def load_raw_data(self, file_path, dimensions, data_type, byte_order='LittleEndian',
                     spacing=(1, 1, 1), num_components=1):
        """
        Load RAW data from a file with explicit parameters

        Args:
            file_path: Path to the RAW data file
            dimensions: Tuple of (x, y, z) dimensions
            data_type: Data type string (uint8, uint16, int8, int16, float32, float64)
            byte_order: Byte order ('LittleEndian' or 'BigEndian'), default: 'LittleEndian'
            spacing: Data spacing tuple (sx, sy, sz), default: (1, 1, 1)
            num_components: Number of scalar components, default: 1

        Returns:
            tuple: (success, message, reader, source_name)

        Example:
            # Load a 256x256x256 uint8 volume
            success, msg, reader, name = manager.load_raw_data(
                "data.raw",
                dimensions=(256, 256, 256),
                data_type='uint8'
            )
        """
        try:
            import os
            from paraview.simple import Show, GetActiveView

            # Handle relative paths
            original_path = file_path

            # Convert to absolute path if it's relative
            if not os.path.isabs(file_path):
                possible_bases = [
                    os.getcwd(),
                    os.path.dirname(os.path.dirname(__file__)),
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'eval'),
                    self._data_folder if self._data_folder else ""
                ]

                for base_dir in possible_bases:
                    if base_dir:
                        test_path = os.path.join(base_dir, file_path)
                        if os.path.exists(test_path):
                            file_path = test_path
                            break
                else:
                    file_path = os.path.abspath(original_path)

            # Check if file exists
            if not os.path.exists(file_path):
                self.logger.error(f"File not found: {file_path}")
                return False, f"File not found: {file_path}", None, ""

            # Record the directory
            self._data_folder = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)

            # Configure and load the RAW reader with explicit parameters
            reader = self._configure_raw_reader(
                file_path, file_name,
                dimensions=dimensions,
                data_type=data_type,
                byte_order=byte_order,
                num_components=num_components
            )

            if not reader:
                return False, f"Failed to load RAW data from {file_path}", None, ""

            # Set data spacing if provided
            if hasattr(reader, 'DataSpacing') and spacing != (1, 1, 1):
                reader.DataSpacing = list(spacing)
                self.logger.info(f"Set data spacing: {spacing}")

            # Show in the active view
            view = GetActiveView()
            display = Show(reader, view)

            # For 3D data, ensure we use volume or outline representation
            # to avoid the image slice mapper error
            if dimensions and len(dimensions) == 3:
                dim_x, dim_y, dim_z = [int(d) for d in dimensions]
                if dim_z > 1:  # This is 3D data
                    # Use Outline representation to avoid slice mapper issues
                    display.SetRepresentationType('Outline')
                    self.logger.info("Set representation to Outline for 3D data")

            view.ResetCamera()  # Allow full camera reset including clipping range

            # Add some padding by zooming out slightly for better framing
            cam = view.GetActiveCamera()
            if cam:
                cam.Dolly(0.7)  # Zoom out by 30% for better initial view
                from paraview.simple import Render
                Render()  # Update the view after camera adjustment

            # Save as original source
            self.original_source = reader

            # Get the source name
            source_name = self._get_source_name(reader)

            # Convert dimensions to integers for display
            int_dimensions = tuple(int(d) for d in dimensions)
            self.logger.info(f"Successfully loaded RAW data from {file_path}")
            return True, f"Successfully loaded RAW data from {file_path} with dimensions {int_dimensions}", reader, source_name

        except Exception as e:
            self.logger.error(f"Error loading RAW data: {str(e)}")
            return False, f"Error loading RAW data: {str(e)}", None, ""

    def clear_pipeline_and_reset(self):
        """
        Completely clear the ParaView pipeline and return the GUI to a clean,
        freshly-started state.

        Steps performed:
        1.  Delete every source and filter currently in the pipeline.
        2.  Wipe all cached handles in this `ParaViewManager` instance.
        3.  Reset the active render view (camera, background, colormaps, etc.).
        4.  Reset all visualization settings to defaults.
        5.  Force a render so the UI immediately reflects the change.

        Returns
        -------
        (success: bool, message: str)
        """
        try:
            # Core ParaView helpers we need
            from paraview.simple import (
                GetSources, Delete, GetActiveView, ResetCamera, Render,
                GetColorTransferFunction, GetOpacityTransferFunction
            )

            # --------------------------------------------------------
            # 1.  Delete every pipeline object that exists
            # --------------------------------------------------------
            sources = GetSources()
            num_sources = len(sources) if sources else 0

            if sources:
                # Collect proxies first, then delete – avoids iterator invalidation
                proxies_to_delete = [proxy for (_, proxy) in sources.items()]
                for proxy in proxies_to_delete:
                    try:
                        Delete(proxy)
                    except Exception as err:
                        self.logger.warning(f"Could not delete proxy: {err}")

            # --------------------------------------------------------
            # 2.  Reset our own cached state
            # --------------------------------------------------------
            self.original_source = None
            if hasattr(self, "isosurface_filter"):
                self.isosurface_filter = None
            self._data_folder = ""

            # --------------------------------------------------------
            # 3.  Reset colormaps and transfer functions
            # --------------------------------------------------------
            # Reset commonly used colormaps to default ranges
            common_arrays = ['ImageFile', 'MetaImage', 'Scalars_', 'RTData']
            for array_name in common_arrays:
                try:
                    # Reset color transfer function
                    ctf = GetColorTransferFunction(array_name)
                    if ctf:
                        ctf.RescaleTransferFunction(0.0, 255.0)  # Default range for uint8
                        # Reset to default colormap (e.g., Cool to Warm)
                        ctf.ApplyPreset('Cool to Warm', True)

                    # Reset opacity transfer function
                    otf = GetOpacityTransferFunction(array_name)
                    if otf:
                        otf.Points = [0.0, 0.0, 0.5, 0.0, 255.0, 1.0, 0.5, 0.0]  # Default linear
                except:
                    pass  # Ignore if the array doesn't exist

            # --------------------------------------------------------
            # 4.  Reset the active view & camera
            # --------------------------------------------------------
            view = GetActiveView()
            if view:
                # One call handles both camera framing *and* clipping range
                ResetCamera(view)

                # Set a neutral dark-grey background (solid, no gradient)
                if hasattr(view, "Background"):
                    view.Background = [0.32, 0.34, 0.43]
                if hasattr(view, "Background2"):   # match Background -> no gradient
                    view.Background2 = [0.32, 0.34, 0.43]

                # Let ResetCamera handle the camera position based on data bounds
                # Only set view up and ensure proper projection
                # Check if view supports camera (3D render view) vs 2D plot views
                cam = None
                if hasattr(view, "GetActiveCamera"):
                    cam = view.GetActiveCamera()
                    if cam:
                        # Keep Z-up orientation which is common for many datasets
                        cam.SetViewUp(0.0, 0.0, 1.0)
                        # Use the default view angle
                        cam.SetViewAngle(30.0)

                    # Center of rotation should match camera focal point for intuitive rotation
                    if hasattr(view, "CenterOfRotation") and cam:
                        view.CenterOfRotation = cam.GetFocalPoint()

                # Ensure perspective projection
                if hasattr(view, "CameraParallelProjection"):
                    view.CameraParallelProjection = 0

                # Reset any view-specific settings
                # For ParaView 5.10+, use BackgroundColorMode instead of UseGradientBackground
                if hasattr(view, "BackgroundColorMode"):
                    view.BackgroundColorMode = 'Single Color'  # Use single color, not gradient
                elif hasattr(view, "UseGradientBackground"):
                    # Fallback for older versions
                    view.UseGradientBackground = 0

                if hasattr(view, "OrientationAxesVisibility"):
                    view.OrientationAxesVisibility = 1  # Show orientation axes

            # --------------------------------------------------------
            # 5.  Force a redraw so the GUI updates immediately
            # --------------------------------------------------------
            try:
                if view:
                    Render(view)
            except Exception as err:
                self.logger.warning(f"Render failed after reset: {err}")

            msg = (
                "Pipeline cleared successfully. "
                f"Removed {num_sources} source(s), reset all view settings, "
                "and reinitialized colormaps."
            )
            self.logger.info(msg)
            return True, msg

        except Exception as err:
            error_msg = f"Error clearing pipeline: {err}"
            self.logger.error(error_msg)
            return False, error_msg

    def set_background_color(self, red=0.32, green=0.34, blue=0.43):
        """
        Set the background color of the active view.
        
        Args:
            red (float): Red component (0.0 to 1.0). Default: 0.32
            green (float): Green component (0.0 to 1.0). Default: 0.34  
            blue (float): Blue component (0.0 to 1.0). Default: 0.43
            
        Note:
            Default values approximate ParaView's default dark background.
            
        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveView, SetViewProperties
            
            view = GetActiveView()
            if not view:
                return False, "Error: No active view."
            
            # Validate color values
            for value, name in [(red, 'red'), (green, 'green'), (blue, 'blue')]:
                if not 0.0 <= value <= 1.0:
                    return False, f"Error: {name} value must be between 0.0 and 1.0 (got {value})"
            
            # Set both Background and Background2 to the same color for solid color
            SetViewProperties(
                view,
                Background=[red, green, blue],
                Background2=[red, green, blue],  # Set same color to avoid gradient
                UseColorPaletteForBackground=0
            )
            
            return True, f"Background color set to RGB({red:.2f}, {green:.2f}, {blue:.2f})"
            
        except Exception as e:
            self.logger.error(f"Error setting background color: {str(e)}")
            return False, f"Error setting background color: {str(e)}"
                
     
    def save_contour_as_stl(self, stl_filename="contour.stl"):
        """
        Save the active source (e.g. a contour) as an STL file in the same folder
        where the original data was loaded.

        Args:
            stl_filename (str): Name of the STL file to create (defaults to 'contour.stl').

        Returns:
            tuple: (success: bool, message: str, saved_path: str)
        """
        try:
            import os
            from paraview.simple import GetActiveSource, SaveData

            # Ensure we have an active source
            active_source = GetActiveSource()
            if not active_source:
                return False, "Error: No active source to save.", ""

            # Check that we have a recorded data folder, use current directory as fallback
            if not hasattr(self, "_data_folder") or not self._data_folder:
                self._data_folder = os.getcwd()
                self.logger.info(f"No data folder set, using current directory: {self._data_folder}")

            # Compose the full path in the same folder as the loaded data
            full_path = os.path.join(self._data_folder, stl_filename)

            # For STL export, we may need to extract surface or triangulate first
            from paraview.simple import ExtractSurface, Triangulate

            # Check if the data is already surface data
            data_info = active_source.GetDataInformation()

            # If it's volume data, extract surface first
            if data_info.GetDataSetTypeAsString() in ['vtkUnstructuredGrid', 'vtkStructuredGrid', 'vtkImageData']:
                surface = ExtractSurface(Input=active_source)
                triangulated = Triangulate(Input=surface)
            else:
                # For polydata, just triangulate
                triangulated = Triangulate(Input=active_source)

            # Save to STL
            SaveData(full_path, proxy=triangulated)
            
            message = f"Saved active source to STL at: {full_path}"
            return True, message, full_path
        except Exception as e:
            self.logger.error(f"Error saving STL: {str(e)}")
            return False, f"Error saving STL: {str(e)}", ""
        
    def create_source(self, source_type):
        """
        Create a new geometric source
        
        Args:
            source_type: Type of source to create (Sphere, Cone, etc.)
            
        Returns:
            tuple: (success, message, source, source_name)
        """
        try:
            from paraview.simple import GetActiveView, Show
            source = None
            source_type = source_type.lower()
            
            if source_type == "sphere":
                from paraview.simple import Sphere
                source = Sphere()
            elif source_type == "cone":
                from paraview.simple import Cone
                source = Cone()
            elif source_type == "cylinder":
                from paraview.simple import Cylinder
                source = Cylinder()
            elif source_type == "plane":
                from paraview.simple import Plane
                source = Plane()
            elif source_type == "box":
                from paraview.simple import Box
                source = Box()
            elif source_type == "glyph":
                from paraview.simple import Glyph
                source = Glyph()
            else:
                return False, f"Unsupported source type: {source_type}", None, ""
            
            view = GetActiveView()
            Show(source, view)
            
            # Get the source name using the helper function
            source_name = self._get_source_name(source)
            
            return True, f"Created {source_type} source", source, source_name
        except Exception as e:
            self.logger.error(f"Error creating source: {str(e)}")
            return False, f"Error creating source: {str(e)}", None, ""
    
    def set_active_source(self, name):
        """
        Set the active pipeline object by matching its registered name. Use this function to set active source so that the computation is applied to the correct objects in paraview object hiearchy. 

        Args:
            name (str): The name of the pipeline object, e.g. "Slice1" or "Contour1".
                        Typically, ParaView registers pipeline objects using this sort of naming.

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            from paraview.simple import GetSources, SetActiveSource

            sources_dict = GetSources()  # Returns a dict: { (name, ""), proxyObject }, etc.
            if not sources_dict:
                return False, "No sources available in the pipeline."

            # Attempt exact or partial match:
            # Option A: Exact match on the first element of the key
            # Option B: A more flexible approach scanning all source names
            matches = []
            for (source_key, proxy) in sources_dict.items():
                # source_key is typically (registeredName, fileNameOrOtherString)
                if source_key[0] == name:
                    SetActiveSource(proxy)
                    return True, f"Active source set to '{source_key[0]}'"
                # Alternatively, you could allow partial or case-insensitive matches:
                # if name.lower() in source_key[0].lower():
                #     matches.append((source_key[0], proxy))

            return False, f"No source found with the name '{name}'."
        except Exception as e:
            self.logger.error(f"Error in set_active_source: {str(e)}")
            return False, f"Error setting active source: {str(e)}"

    def get_active_source_names_by_type(self, source_type=None):
        """
        Get a list of source names filtered by their type.
        
        Args:
            source_type (str, optional): Filter sources by type (e.g., 'Sphere', 'Contour', etc.).
                                      If None, returns all sources.
        
        Returns:
            tuple: (success: bool, message: str, source_names: list)
        """
        try:
            from paraview.simple import GetSources
            
            sources_dict = GetSources()
            if not sources_dict:
                return True, "No sources available in the pipeline.", []
            
            result_sources = []
            
            for (source_key, proxy) in sources_dict.items():
                proxy_type = proxy.__class__.__name__
                
                # If source_type is None or matches the proxy type, add to results
                if source_type is None or source_type.lower() in proxy_type.lower():
                    result_sources.append(source_key[0])
            
            if not result_sources and source_type:
                message = f"No sources of type '{source_type}' found in the pipeline."
            elif not result_sources:
                message = "No sources found in the pipeline."
            else:
                message = f"Found {len(result_sources)} source(s)" + (f" of type '{source_type}'" if source_type else "")
            
            return True, message, result_sources
            
        except Exception as e:
            self.logger.error(f"Error getting source names by type: {str(e)}")
            return False, f"Error getting source names by type: {str(e)}", []


    def create_isosurface(self, value, field=None):
        """
        Create or update an isosurface visualization of the loaded volume data.
        If an isosurface filter already exists (stored in self.isosurface_filter),
        update its isovalue and contour parameters. Otherwise, create a new filter.

        Args:
            value: Isovalue.
            field: Optional field name to contour by.

        Returns:
            tuple: (success: bool, message: str, contour_proxy, contour_name: str)
        """
        try:
            from paraview.simple import (
                GetActiveView, SetActiveSource, Contour, Show, GetActiveSource
            )

            # Use the originally loaded source if available; fall back to the active source.
            base_source = self.original_source or GetActiveSource()
            if not base_source:
                return False, "Error: No active source. Load data first.", None, ""

            # Determine whether to update an existing isosurface or create a new one.
            if hasattr(self, 'isosurface_filter') and self.isosurface_filter:
                contour = self.isosurface_filter
                contour.Isosurfaces = [value]
                if field:
                    contour.ContourBy = ['POINTS', field]
                message = f"Updated isosurface to value {value}"
            else:
                contour = Contour(Input=base_source)
                contour.Isosurfaces = [value]
                if field:
                    contour.ContourBy = ['POINTS', field]
                self.isosurface_filter = contour
                message = f"Created isosurface at value {value}"

            # Show the contour in the active view
            view = GetActiveView()
            Show(contour, view)

            # Optionally reset active source to the original data
            SetActiveSource(base_source)

            # Get the source name using the helper function
            contour_name = self._get_source_name(contour)

            # Return a 4-tuple including the name
            return True, message, contour, contour_name

        except Exception as e:
            self.logger.error(f"Error creating/updating isosurface: {str(e)}")
            return False, f"Error creating/updating isosurface: {str(e)}", None, ""

    def compute_surface_area(self):
        """
        Compute the surface area of the ACTIVE source.

        IMPORTANT: This assumes the active pipeline object is a surface mesh.
        If the active pipeline is still a volumetric dataset, you won't get
        a valid 'Area' array. For example, you might want to call:
        1) extract_surface()
        2) [SetActiveSource(...) for the extracted surface]
        3) compute_surface_area()

        Returns:
            tuple: (success: bool, message: str, area_value: float)
        """
        try:
            from paraview.simple import GetActiveSource, IntegrateVariables
            import paraview.servermanager as sm

            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", 0.0

            # IntegrateVariables on a surface dataset yields an 'Area' array
            integrate_filter = IntegrateVariables(Input=source)
            integrate_filter.UpdatePipeline()

            # Fetch integrated results
            integrated_data = sm.Fetch(integrate_filter)
            if not integrated_data:
                return False, "Error: Could not fetch integrated data from server.", 0.0

            # Look for 'Area' array in CellData
            area_array = integrated_data.GetCellData().GetArray("Area")
            if not area_array:
                return False, (
                    "No 'Area' array found. Are you sure this is a surface dataset?"
                ), 0.0

            area_value = area_array.GetValue(0)
            return True, f"Computed surface area: {area_value}", area_value

        except Exception as e:
            self.logger.error(f"Error computing surface area: {str(e)}")
            return False, f"Error computing surface area: {str(e)}", 0.0

            # The integrated filter typically stores one value (the total area) in index 0
            total_area = area_array.GetValue(0)

            return (True, "Successfully computed surface area.", total_area)

        except Exception as e:
            self.logger.error(f"Error computing surface area: {str(e)}")
            return (False, f"Error computing surface area: {str(e)}", None)


    def create_clip(self, origin_x=None, origin_y=None, origin_z=None,
                    normal_x=1, normal_y=0, normal_z=0, invert=False):
        """
        Create a clip filter to cut the data with a plane.
        
        Args:
            origin_x, origin_y, origin_z: Coordinates for clip plane origin (default: center of dataset).
                                        If None, it uses the dataset's center.
            normal_x, normal_y, normal_z: Normal of the clip plane (default: [1, 0, 0] for y-z plane).
            invert (bool): If False, keeps the positive side of the plane normal.
                        If True, keeps the negative side of the plane normal.
        
        Returns:
            tuple: (success: bool, message: str, clip_filter, clip_name: str)
        """
        try:
            from paraview.simple import (
                GetActiveView, SetActiveSource, Clip, Show, GetActiveSource
            )
            
            base_source = self.original_source or GetActiveSource()
            if not base_source:
                return False, "Error: No active source. Load data first.", None, None
            
            # If origin is unspecified, use the center of the dataset
            if origin_x is not None and origin_y is not None and origin_z is not None:
                origin = [origin_x, origin_y, origin_z]
            else:
                info = base_source.GetDataInformation()
                bounds = info.GetBounds()
                origin = [
                    (bounds[0] + bounds[1]) / 2,
                    (bounds[2] + bounds[3]) / 2,
                    (bounds[4] + bounds[5]) / 2
                ]
            
            normal = [normal_x, normal_y, normal_z]
            
            # Create and configure the clip filter
            clip_filter = Clip(Input=base_source)
            clip_filter.ClipType = 'Plane'
            clip_filter.ClipType.Origin = origin
            clip_filter.ClipType.Normal = normal
            
            # Set invert property to control which side to keep
            clip_filter.Invert = invert
            
            # Show the clipped result in the view
            view = GetActiveView()
            Show(clip_filter, view)
            
            # Set the clip as active source
            SetActiveSource(clip_filter)
            
            # Get the source name using the helper function
            clip_name = self._get_source_name(clip_filter)
            
            side_kept = "negative" if invert else "positive"
            message = (
                f"Created clip with plane at origin {origin}, normal {normal}. "
                f"Keeping {side_kept} side of the plane. "
                f"Clip name is: {clip_name}"
            )
            return True, message, clip_filter, clip_name
            
        except Exception as e:
            self.logger.error(f"Error creating clip: {str(e)}")
            return False, f"Error creating clip: {str(e)}", None, None
           

    def create_slice(self, origin_x=None, origin_y=None, origin_z=None,
                 normal_x=0, normal_y=0, normal_z=1):
        """
        Create a slice through the loaded volume data.

        Args:
            origin_x, origin_y, origin_z: Coordinates for slice origin (default: center of dataset).
                                        If None, it uses the dataset's center.
            normal_x, normal_y, normal_z: Normal of the slice plane (default: [0, 0, 1]).

        Returns:
            tuple: (success: bool, message: str, slice_filter, slice_name: str)
        """
        try:
            from paraview.simple import (
                GetActiveView, SetActiveSource, Slice, Show, GetActiveSource
            )

            base_source = self.original_source or GetActiveSource()
            if not base_source:
                return False, "Error: No active source. Load data first.", None, None

            # If origin is unspecified, use the center of the dataset
            if origin_x is not None and origin_y is not None and origin_z is not None:
                origin = [origin_x, origin_y, origin_z]
            else:
                info = base_source.GetDataInformation()
                bounds = info.GetBounds()
                origin = [
                    (bounds[0] + bounds[1]) / 2,
                    (bounds[2] + bounds[3]) / 2,
                    (bounds[4] + bounds[5]) / 2
                ]

            normal = [normal_x, normal_y, normal_z]

            # Create and configure the slice filter
            slice_filter = Slice(Input=base_source)
            slice_filter.SliceType = 'Plane'
            slice_filter.SliceType.Origin = origin
            slice_filter.SliceType.Normal = normal

            # Show the new slice in the view
            view = GetActiveView()
            Show(slice_filter, view)

            # (Optional) reset the active source to the original volume
            SetActiveSource(base_source)

            # Get the source name using the helper function
            slice_name = self._get_source_name(slice_filter)

            message = (
                f"Created slice with origin {origin} and normal {normal}. "
                f"Slice name is: {slice_name}"
            )
            return True, message, slice_filter, slice_name

        except Exception as e:
            self.logger.error(f"Error creating slice: {str(e)}")
            return False, f"Error creating slice: {str(e)}", None, None

        
    def create_volume_rendering(self, enable=True):
        """
        Toggle volume rendering for the loaded volume data.
        
        Args:
            enable (bool): Whether to enable (True) or disable (False) volume rendering.
                          If True, shows volume rendering.
                          If False, hides the volume but preserves the volume representation.
        
        Returns:
            tuple: (success, message, source_name)
        """
        try:
            from paraview.simple import GetActiveView, SetActiveSource, GetDisplayProperties

            if not self.original_source:
                return False, "Error: No original data loaded. Load data first.", None

            # Force the original volume data to be active
            SetActiveSource(self.original_source)
            view = GetActiveView()
            display = GetDisplayProperties(self.original_source, view)

            # Get the current representation type
            current_rep = display.GetRepresentationType() if hasattr(display, 'GetRepresentationType') else None
            
            if enable:
                # Switch to Volume representation if not already
                if current_rep != 'Volume':
                    display.SetRepresentationType('Volume')
                # Make sure it's visible
                display.Visibility = 1
                status_message = "Volume rendering enabled"
            else:
                # If currently in Volume mode, make it invisible
                # but don't change the representation type
                if current_rep == 'Volume':
                    display.Visibility = 0
                    status_message = "Volume rendering hidden (representation preserved)"
                else:
                    # If not in Volume mode, just report current state
                    status_message = f"Volume rendering already disabled (current representation: {current_rep})"

            # Get the source name using the helper function
            source_name = self._get_source_name(self.original_source)

            return True, status_message, source_name

        except Exception as e:
            self.logger.error(f"Error toggling volume rendering: {str(e)}")
            return False, f"Error toggling volume rendering: {str(e)}", None

    def toggle_visibility(self, enable=True):
        """
        Toggle visibility for the current source.
        
        Args:
            enable (bool): Whether to enable (True) or disable (False) visibility of the current source.
                          If True, shows the current source.
                          If False, hides the current source.
        
        Returns:
            tuple: (success, message, source_name)
        """
        try:
            from paraview.simple import GetActiveView, SetActiveSource, GetDisplayProperties

            if not GetActiveSource():
                return False, "Error: No data selected. Load data first.", None

            view = GetActiveView()
            display = GetDisplayProperties(GetActiveSource(), view)
            
            if enable:
                display.Visibility = 1
                status_message = "Element was made visibile"
            else:
                display.Visibility = 0
                status_message = "Rendering hidden (representation preserved)"

            # Get the source name using the helper function
            source_name = self._get_source_name(GetActiveSource())

            return True, status_message, source_name

        except Exception as e:
            self.logger.error(f"Error toggling visibility: {str(e)}")
            return False, f"Error toggling visibility: {str(e)}", None
    
    def color_by(self, field, component=-1):
        """
        Color the active visualization by a specific field.
        This function first checks if the active source can be colored by fields
        (i.e., it's a dataset with arrays) before attempting to apply colors.
        
        Args:
            field: Field name to color by.
            component: Component to color by (-1 for magnitude).
            
        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveSource, GetActiveView, GetDisplayProperties, ColorBy
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first."
            
            view = GetActiveView()
            display = GetDisplayProperties(source, view)
            
            # Check if the current representation type can be colored by arrays
            # Some representations (like 'Outline') cannot be colored by data arrays
            rep_type = display.GetRepresentationType() if hasattr(display, 'GetRepresentationType') else None
            if rep_type in ['Outline', 'Wireframe']:
                return False, f"Error: The current representation type '{rep_type}' cannot be colored by fields. Try changing to 'Surface' or 'Volume' first."
            
            # Get data information directly from the source
            data_info = source.GetDataInformation()
            point_info = data_info.GetPointDataInformation()
            cell_info = data_info.GetCellDataInformation()
            
            # Check if the active source has data arrays
            if (point_info.GetNumberOfArrays() == 0 and 
                cell_info.GetNumberOfArrays() == 0):
                return False, "Error: The active source does not have any data arrays to color by."
            
            # Try to find the requested field
            field_available = False
            field_location = None
            
            # Check point data arrays
            for i in range(point_info.GetNumberOfArrays()):
                array_info = point_info.GetArrayInformation(i)
                if array_info.GetName() == field:
                    ColorBy(display, ('POINTS', field), component)
                    field_available = True
                    field_location = 'POINTS'
                    break
            
            # Check cell data arrays if not found in point data
            if not field_available:
                for i in range(cell_info.GetNumberOfArrays()):
                    array_info = cell_info.GetArrayInformation(i)
                    if array_info.GetName() == field:
                        ColorBy(display, ('CELLS', field), component)
                        field_available = True
                        field_location = 'CELLS'
                        break
            
            if not field_available:
                # Build a list of available fields for better error reporting
                available_fields = []
                for i in range(point_info.GetNumberOfArrays()):
                    array_info = point_info.GetArrayInformation(i)
                    available_fields.append(f"{array_info.GetName()} (POINTS)")
                for i in range(cell_info.GetNumberOfArrays()):
                    array_info = cell_info.GetArrayInformation(i)
                    available_fields.append(f"{array_info.GetName()} (CELLS)")
                
                fields_str = ", ".join(available_fields)
                return False, f"Error: Field '{field}' not found. Available fields are: {fields_str}"
            
            # Rescale the color map to show the full data range
            display.RescaleTransferFunctionToDataRange(True)
            return True, f"Colored by field: '{field}' from {field_location}"
        except Exception as e:
            self.logger.error(f"Error coloring by field: {str(e)}")
            return False, f"Error coloring by field: {str(e)}"

    
    # def set_color_map(self, preset_name="Blue-Red"):
    #     """
    #     Set the color map (lookup table) for the current visualization.
        
    #     Args:
    #         preset_name: Name of the color map preset.
    #                     Available presets include (but are not limited to):
    #                     - Blue-Red
    #                     - Cool to Warm
    #                     - Viridis
    #                     - Plasma
    #                     - Magma
    #                     - Inferno
    #                     - Rainbow
    #                     - Grayscale
                        
    #     Returns:
    #         tuple: (success, message)
    #     """
    #     try:
    #         from paraview.simple import GetActiveSource, GetActiveView, GetDisplayProperties, ApplyPreset
    #         source = GetActiveSource()
    #         if not source:
    #             return False, "Error: No active source. Load data first."
            
    #         view = GetActiveView()
    #         display = GetDisplayProperties(source, view)
            
    #         color_tf = display.LookupTable
    #         if not color_tf:
    #             return False, "Error: No active color transfer function"
            
    #         # Apply the requested preset to the color transfer function.
    #         ApplyPreset(color_tf, preset_name, True)
            
    #         available_presets = "Blue-Red, Cool to Warm, Viridis, Plasma, Magma, Inferno, Rainbow, Grayscale"
    #         return True, f"Applied color map preset: {preset_name}. Available presets include: {available_presets}"
    #     except Exception as e:
    #         self.logger.error(f"Error setting color map: {str(e)}")
    #         return False, f"Error setting color map: {str(e)}"

    def get_histogram(self, field=None, num_bins=256, data_location="POINTS"):
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
            num_bins (int, optional): Number of histogram bins (default is 10).
            data_location (str, optional): Specify "POINTS" (default) or "CELLS" to indicate the source of the data.
            
        Returns:
            tuple: (success (bool), message (str), histogram_data (list of tuples))
                histogram_data is a list of tuples (bin_center, frequency) representing the computed histogram.

        Note:
            This function uses the Histogram filter from paraview.simple and updates the pipeline.
            Since direct assignment to properties like 'NumberOfBins' is disallowed, the code retrieves
            the proper property (either "NumberOfBins" or "BinCount") via GetProperty() and sets it via SetElement().
        """
        try:
            from paraview.simple import GetActiveSource, Histogram, UpdatePipeline, servermanager
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None

            # Obtain the data information from the specified location.
            data_info = source.GetDataInformation()
            data_location = data_location.upper()
            if data_location == "CELLS":
                array_info_obj = data_info.GetCellDataInformation()
            else:
                array_info_obj = data_info.GetPointDataInformation()
            num_arrays = array_info_obj.GetNumberOfArrays()

            # Automatically determine the field if not provided.
            if field is None:
                if num_arrays == 1:
                    field = array_info_obj.GetArrayInformation(0).GetName()
                else:
                    available_arrays = []
                    for i in range(num_arrays):
                        available_arrays.append(array_info_obj.GetArrayInformation(i).GetName())
                    return (
                        False,
                        "Error: Multiple fields available. Please specify a field name. Available arrays: " +
                        ", ".join(available_arrays),
                        None
                    )

            # Create and configure the Histogram filter.
            hist_filter = Histogram(Input=source)
            # Set the input array from the chosen location (POINTS or CELLS).
            hist_filter.SelectInputArray = [data_location, field]

            # Set the number of bins via GetProperty to avoid creating new attributes.
            nbins_prop = hist_filter.GetProperty("NumberOfBins")
            if nbins_prop is None:
                nbins_prop = hist_filter.GetProperty("BinCount")
            if nbins_prop is None:
                return False, "Error: Histogram filter does not have a 'NumberOfBins' or 'BinCount' property.", None
            nbins_prop.SetElement(0, num_bins)

            # Update the pipeline to compute the histogram.
            UpdatePipeline()

            # Fetch the computed histogram (returned as a vtkTable).
            hist_table = servermanager.Fetch(hist_filter)
            if hist_table.GetNumberOfRows() == 0:
                return False, "Histogram computation returned empty data.", None

            # Try to extract histogram data assuming columns named "bin_centers" and "bin_frequencies".
            bin_centers_col = hist_table.GetColumnByName("bin_centers")
            frequencies_col = hist_table.GetColumnByName("bin_frequencies")
            # Fallback: use the first two columns if the expected names do not exist.
            if not bin_centers_col or not frequencies_col:
                bin_centers_col = hist_table.GetColumn(0)
                frequencies_col = hist_table.GetColumn(1)

            histogram_data = []
            num_rows = hist_table.GetNumberOfRows()
            for i in range(num_rows):
                # Retrieve each value from the vtkArray for bin center and frequency.
                bin_center = bin_centers_col.GetValue(i)
                frequency = frequencies_col.GetValue(i)
                histogram_data.append((bin_center, frequency))

            return True, f"Histogram computed for field '{field}' in {data_location} with {num_bins} bins.", histogram_data

        except Exception as e:
            self.logger.error(f"Error computing histogram: {str(e)}")
            return False, f"Error computing histogram: {str(e)}", None


    def set_representation_type(self, rep_type):
        """
        Set the representation type for the active source.
        
        Args:
            rep_type: Representation type (Surface, Wireframe, Points, Volume, etc.)
            
        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveSource, GetActiveView, GetDisplayProperties
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first."
            
            view = GetActiveView()
            display = GetDisplayProperties(source, view)
            
            display.SetRepresentationType(rep_type)
            
            return True, f"Set representation type to {rep_type}"
        except Exception as e:
            self.logger.error(f"Error setting representation type: {str(e)}")
            return False, f"Error setting representation type: {str(e)}"
    
    def edit_volume_opacity(self, field_name, opacity_points):
        """
        Edit ONLY the opacity transfer function for a given field, ensuring
        we pass only (value, alpha) pairs to ParaView.

        Args:
            field_name (str): The name of the field/array to modify.
            opacity_points (list of tuples): Each tuple must be (value, alpha).
                Example: [(0.0, 0.0), (50.0, 0.3), (100.0, 1.0)]

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            from paraview.simple import GetOpacityTransferFunction

            if not opacity_points:
                return False, "No opacity points provided."

            # Grab the opacity transfer function for the specified field
            opacity_tf = GetOpacityTransferFunction(field_name)
            if opacity_tf is None:
                return False, f"Could not find an opacity transfer function for field '{field_name}'."

            # Flatten the list of (value, alpha) into the format:
            # [val1, alpha1, midpoint1, sharpness1, val2, alpha2, midpoint2, sharpness2, ...]
            new_opacity_pts = []
            for val, alpha in opacity_points:
                new_opacity_pts.extend([val, alpha, 0.5, 0.0])  # midpoint=0.5, sharpness=0.0

            # Assign them to the piecewise function
            opacity_tf.Points = new_opacity_pts

            return True, f"Opacity transfer function updated for field '{field_name}'."

        except Exception as e:
            self.logger.error(f"Error editing opacity transfer function: {str(e)}")
            return False, f"Error editing opacity transfer function: {str(e)}"


    def set_color_map(self, field_name, color_points):
        """
        Sets the color transfer function for the given field (array) in ParaView.

        Args:
            field_name (str): The name of the field/array (as it appears in ParaView).
            color_points (list of (float, (float, float, float))):
                Each element should be a tuple: (value, (r, g, b))
                where value is the data value, and r, g, b are in [0, 1].

        Returns:
            tuple (success: bool, message: str)
        """
        try:
            from paraview.simple import GetColorTransferFunction

            if not color_points:
                return False, "No color points provided."

            # Retrieve/create the color transfer function for the specified field
            color_tf = GetColorTransferFunction(field_name)
            if color_tf is None:
                return False, f"Could not find or create a color transfer function for '{field_name}'."

            # Flatten the list into [value, R, G, B, value, R, G, B, ...]
            new_rgb_points = []
            for val, rgb in color_points:
                if len(rgb) != 3:
                    return False, f"Invalid RGB tuple for value {val}: {rgb}"
                r, g, b = rgb
                new_rgb_points.extend([val, r, g, b])

            # Update the color transfer function
            color_tf.RGBPoints = new_rgb_points

            # Optionally, you can rescale the transfer function based on min and max values
            # Example:
            # min_val = min([pt[0] for pt in color_points])
            # max_val = max([pt[0] for pt in color_points])
            # color_tf.RescaleTransferFunction(min_val, max_val)

            return True, f"Color transfer function updated for field '{field_name}'."

        except Exception as e:
            msg = f"Error setting color map: {str(e)}"
            return False, msg


    def get_pipeline(self):
        """
        Get the current pipeline structure.
        
        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetSources
            sources = GetSources()
            if not sources:
                return True, "Pipeline is empty. No sources found."
            
            response = "Current pipeline:\n"
            for name, source in sources.items():
                response += f"- {name[0]}: {source.__class__.__name__}\n"
            return True, response
        except Exception as e:
            self.logger.error(f"Error getting pipeline: {str(e)}")
            return False, f"Error getting pipeline: {str(e)}"
    
    def get_available_arrays(self):
        """
        Get a list of available arrays in the active source.

        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveSource
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first."

            # Obtain comprehensive data information from the source.
            data_info = source.GetDataInformation()
            point_info = data_info.GetPointDataInformation()
            cell_info  = data_info.GetCellDataInformation()

            response = "Available arrays:\n\nPoint data arrays:\n"
            if point_info:
                num_point_arrays = point_info.GetNumberOfArrays()
                for i in range(num_point_arrays):
                    # Get the array information for each point array.
                    array_info = point_info.GetArrayInformation(i)
                    array_name = array_info.GetName()  # Use GetName() rather than GetArrayName()
                    components = array_info.GetNumberOfComponents()
                    response += f"- {array_name} ({components} components)\n"
            else:
                response += "No point data arrays found.\n"

            response += "\nCell data arrays:\n"
            if cell_info:
                num_cell_arrays = cell_info.GetNumberOfArrays()
                for i in range(num_cell_arrays):
                    # Get the array information for each cell array.
                    array_info = cell_info.GetArrayInformation(i)
                    array_name = array_info.GetName()
                    components = array_info.GetNumberOfComponents()
                    response += f"- {array_name} ({components} components)\n"
            else:
                response += "No cell data arrays found.\n"

            return True, response
        except Exception as e:
            self.logger.error(f"Error getting available arrays: {str(e)}")
            return False, f"Error getting available arrays: {str(e)}"

    def create_stream_tracer(self, vector_field=None, base_source=None, point_center=None,
                            integration_direction="BOTH", 
                            initial_step_length=0.1,
                            maximum_stream_length=50.0,
                            number_of_streamlines=100,
                            point_radius=1.0,
                            tube_radius=0.1,
                            make_volume_transparent=True):
        """
        Create a stream tracer visualization for a vector volume with tube representation.

        Args:
            vector_field (str, optional): Name of the vector field to trace.
                                        If None, the function automatically selects
                                        the first array with more than one component.
            base_source (optional): The data source (volume) on which to perform stream tracing.
                                If None, uses self.original_source or GetActiveSource().
            point_center (list, optional): Center coordinates [x, y, z] for the seed points.
                                        If None, the center of the volume's bounds is used.
            integration_direction (str): "FORWARD", "BACKWARD", or "BOTH" for integration.
            initial_step_length (float): The initial step size.
            maximum_stream_length (float): Maximum streamline length, beyond which integration terminates.
            number_of_streamlines (int): Number of seed points if a default seed is created.
            point_radius (float): Radius for the Point Cloud seed.
            tube_radius (float): Radius for the tube visualization.
            make_volume_transparent (bool): Whether to make the base volume transparent.

        Returns:
            tuple: (success (bool), message (str), tube filter proxy, tube_name (str))
        """
        try:
            from paraview.simple import (GetActiveSource, GetActiveView, StreamTracer, 
                                        Show, SetActiveSource, FindSource, Tube,
                                        GetDisplayProperties, ColorBy)

            # Determine the base source: use provided, or self.original_source, or the active source.
            if base_source is None:
                base_source = self.original_source or GetActiveSource()
            if not base_source:
                return False, "Error: No active source. Load data first.", None, ""

            # Log the base source name if available.
            base_source_name = None
            if hasattr(base_source, "SMProxy") and hasattr(base_source.SMProxy, "GetXMLName"):
                base_source_name = base_source.SMProxy.GetXMLName()
            else:
                base_source_name = str(base_source)
            self.logger.info(f"Using base source: {base_source_name}")

            # If vector_field is not provided, get the first available multi-component array.
            if vector_field is None:
                # Retrieve the data information and then its point data information.
                data_info = base_source.GetDataInformation()
                point_info = data_info.GetPointDataInformation()
                if point_info:
                    num_arrays = point_info.GetNumberOfArrays()
                    found = False
                    for i in range(num_arrays):
                        array_info = point_info.GetArrayInformation(i)
                        components = array_info.GetNumberOfComponents()
                        if components > 1:
                            vector_field = array_info.GetName()
                            found = True
                            self.logger.info(f"Automatically selected vector field: {vector_field}")
                            break
                    if not found:
                        if num_arrays > 0:
                            vector_field = point_info.GetArrayInformation(0).GetName()
                            self.logger.info(f"No multi-component array found; selected first array: {vector_field}")
                        else:
                            return False, "Error: No arrays found in the base source.", None, ""
                else:
                    return False, "Error: Could not retrieve point data information.", None, ""

            # Determine point center for the seed source
            center = point_center
            if center is None:
                data_info = base_source.GetDataInformation()
                bounds = data_info.GetBounds()  # Format: [xmin, xmax, ymin, ymax, zmin, zmax]
                center = [(bounds[0] + bounds[1]) / 2.0,
                        (bounds[2] + bounds[3]) / 2.0,
                        (bounds[4] + bounds[5]) / 2.0]
                self.logger.info(f"Using auto-calculated center point at {center}")

            # Create the stream tracer filter using Point Cloud seed type
            tracer = StreamTracer(Input=base_source, SeedType='Point Cloud')
            tracer.Vectors = ['POINTS', vector_field]
            tracer.IntegrationDirection = integration_direction
            tracer.InitialStepLength = initial_step_length
            tracer.MaximumStreamlineLength = maximum_stream_length
            
            # Configure the Point Cloud seed
            tracer.SeedType.Center = center
            tracer.SeedType.NumberOfPoints = number_of_streamlines
            tracer.SeedType.Radius = point_radius
            
            # Display the tracer result
            Show(tracer)
            
            # Create tube filter for better visualization
            tube = Tube(Input=tracer)
            tube.Radius = tube_radius
            
            # Show the tube filter
            # Display the tube with proper coloring
            tube_display = Show(tube)
            ColorBy(tube_display, ('POINTS', vector_field))

            # Make the base source transparent if requested
            if make_volume_transparent:
                try:
                    base_display = GetDisplayProperties(base_source)
                    base_display.Opacity = 0.3  # Set opacity to make volume transparent
                except Exception as e:
                    self.logger.warning(f"Could not make volume transparent: {str(e)}")
            
            # Set the active source to the tube filter
            SetActiveSource(tube)
            
            # Get the tube filter name using the helper function
            tube_name = self._get_source_name(tube)

            msg = f"Stream tracer with tubes created for vector field '{vector_field}' using base source '{base_source_name}'."
            return True, msg, tube, tube_name
        except Exception as e:
            self.logger.error(f"Error creating stream tracer: {str(e)}")
            return False, f"Error creating stream tracer: {str(e)}", None, ""

    def get_screenshot(self):
        """
        Capture a screenshot from the current view with optional compression.

        Returns:
            tuple: (success, message, img_path)
        """
        try:
            from paraview.collaboration import processServerEvents
            import tempfile
            import os
            processServerEvents()
            from paraview import servermanager
            from paraview.simple import SetActiveView, RenderAllViews, SaveScreenshot

            # Get the active render view from the GUI connection
            pxm = servermanager.ProxyManager()
            gui_view = None
            views = pxm.GetProxiesInGroup("views")
            for (group, name), view_proxy in views.items():
                if view_proxy.GetXMLName() == "RenderView":
                    gui_view = view_proxy
                    break

            if not gui_view:
                self.logger.error("No existing GUI render view found. Make sure the ParaView GUI is connected.")
                return False, "No GUI render view found. Ensure ParaView GUI is connected.", None

            # Set the found GUI view active
            SetActiveView(gui_view)
            RenderAllViews()

            # Determine output format and path based on compression settings
            if self.compress_screenshots:
                # Use JPEG for compression
                suffix = '.jpg'
                file_format = 'JPEG'
            else:
                # Use PNG for lossless quality
                suffix = '.png'
                file_format = 'PNG'

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                temp_path = tmp.name

            # Save screenshot with appropriate settings
            if self.compress_screenshots:
                # Get current view size to calculate proportional scaling
                view_size = gui_view.GetPropertyValue("ViewSize")
                if view_size and len(view_size) >= 2:
                    width, height = view_size[0], view_size[1]

                    # Calculate new dimensions maintaining aspect ratio
                    if width > self.max_screenshot_width:
                        scale_factor = self.max_screenshot_width / width
                        new_width = self.max_screenshot_width
                        new_height = int(height * scale_factor)
                    else:
                        new_width = width
                        new_height = height

                    # Save with specified resolution
                    SaveScreenshot(temp_path, gui_view,
                                 ImageResolution=[new_width, new_height])

                    self.logger.info(f"Screenshot saved with compression: {new_width}x{new_height}, "
                                   f"quality={self.screenshot_quality}")
                else:
                    # Fallback if we can't get view size
                    SaveScreenshot(temp_path, gui_view,
                                 ImageResolution=[self.max_screenshot_width,
                                               int(self.max_screenshot_width * 0.75)])
            else:
                # Save at full resolution without compression
                SaveScreenshot(temp_path, gui_view)
                self.logger.info("Screenshot saved at full resolution without compression")

            # If using JPEG, apply quality setting using PIL if available
            if self.compress_screenshots and file_format == 'JPEG':
                try:
                    from PIL import Image
                    # Re-save with specified quality
                    img = Image.open(temp_path)

                    # Convert RGBA to RGB if necessary (JPEG doesn't support transparency)
                    if img.mode in ('RGBA', 'LA', 'P'):
                        rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = rgb_img

                    # Save with quality setting
                    img.save(temp_path, 'JPEG', quality=self.screenshot_quality, optimize=True)

                    # Log file size for monitoring
                    file_size_kb = os.path.getsize(temp_path) / 1024
                    self.logger.info(f"Screenshot compressed to {file_size_kb:.1f} KB")
                except ImportError:
                    self.logger.warning("PIL not available for JPEG quality control")
                except Exception as e:
                    self.logger.warning(f"Could not optimize JPEG: {e}")

            return True, "Screenshot captured", temp_path

        except Exception as e:
            self.logger.error(f"Error getting screenshot: {str(e)}")
            return False, f"Error getting screenshot: {str(e)}", None
    

    def rotate_camera(self, azimuth=30.0, elevation=0.0):
        """
        Rotate the camera by specified angles.
        
        Args:
            azimuth: Rotation around vertical axis in degrees.
            elevation: Rotation around horizontal axis in degrees.
            
        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveView
            view = GetActiveView()
            if not view:
                return False, "Error: No active view."
            
            camera = view.GetActiveCamera()
            camera.Azimuth(azimuth)
            camera.Elevation(elevation)
            return True, f"Rotated camera by azimuth: {azimuth}, elevation: {elevation}"
        except Exception as e:
            self.logger.error(f"Error rotating camera: {str(e)}")
            return False, f"Error rotating camera: {str(e)}"
    
    def reset_colormaps(self, array_name=None):
        """
        Reset colormaps and transfer functions to default settings.

        Args:
            array_name: Specific array name to reset. If None, attempts to detect and reset the currently displayed array.

        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import (
                GetActiveSource, GetActiveView, GetDisplayProperties,
                GetColorTransferFunction, GetOpacityTransferFunction,
                Render
            )

            source = GetActiveSource()
            if not source:
                return False, "No active source found"

            view = GetActiveView()
            if not view:
                return False, "No active view found"

            display = GetDisplayProperties(source, view)
            if not display:
                return False, "No display properties found"

            # Get the actual array being colored if not specified
            if not array_name:
                if hasattr(display, 'ColorArrayName') and display.ColorArrayName:
                    # ColorArrayName is typically ['POINTS', 'arrayname'] or ['CELLS', 'arrayname']
                    array_name = display.ColorArrayName[1] if len(display.ColorArrayName) > 1 else None

            if not array_name:
                # Try common array names as fallback
                common_arrays = ['ImageFile', 'MetaImage', 'Scalars_', 'RTData', 'PointData']
                for arr in common_arrays:
                    try:
                        ctf = GetColorTransferFunction(arr)
                        if ctf:
                            array_name = arr
                            break
                    except:
                        continue

            if not array_name:
                return False, "No array specified or currently being colored"

            try:
                # Get proper data range
                data_range = None

                # Try to rescale using display method first (most reliable)
                if hasattr(display, 'RescaleTransferFunctionToDataRange'):
                    try:
                        display.RescaleTransferFunctionToDataRange(True)
                        self.logger.info(f"Rescaled transfer function to data range for {array_name}")
                    except Exception as e:
                        self.logger.warning(f"Could not rescale using display method: {e}")

                # Get the color transfer function
                ctf = GetColorTransferFunction(array_name)
                if ctf:
                    # Try to get actual data range from source
                    try:
                        data_info = source.GetDataInformation()

                        # Check point data first
                        point_info = data_info.GetPointDataInformation()
                        array_info = point_info.GetArrayInformation(array_name) if point_info else None

                        if not array_info:
                            # Check cell data
                            cell_info = data_info.GetCellDataInformation()
                            array_info = cell_info.GetArrayInformation(array_name) if cell_info else None

                        if array_info and array_info.GetNumberOfComponents() > 0:
                            data_range = list(array_info.GetComponentRange(0))
                            self.logger.info(f"Found data range from array info: {data_range}")
                    except Exception as e:
                        self.logger.warning(f"Could not get data range from array info: {e}")

                    # Fallback to current CTF range if we couldn't get actual data range
                    if not data_range and hasattr(ctf, 'RGBPoints') and len(ctf.RGBPoints) >= 4:
                        data_range = [ctf.RGBPoints[0], ctf.RGBPoints[-4]]
                        self.logger.info(f"Using existing CTF range: {data_range}")

                    # If still no range, use a sensible default
                    if not data_range:
                        data_range = [0.0, 1.0]
                        self.logger.warning(f"Using default range [0, 1]")

                    # Reset to data range
                    ctf.RescaleTransferFunction(data_range[0], data_range[1])

                    # Apply appropriate default preset
                    preset_applied = False
                    # Try common presets in order of preference
                    presets = ['Rainbow Desaturated', 'Cool to Warm', 'Blue to Red Rainbow', 'Jet', 'Rainbow']
                    for preset in presets:
                        try:
                            ctf.ApplyPreset(preset, True)
                            self.logger.info(f"Applied preset: {preset}")
                            preset_applied = True
                            break
                        except:
                            continue

                    if not preset_applied:
                        self.logger.warning("Could not apply any preset, keeping current colormap")

                # Reset opacity function
                otf = GetOpacityTransferFunction(array_name)
                if otf and data_range:
                    # Check if volume rendering is being used
                    is_volume = False
                    if hasattr(display, 'Representation'):
                        is_volume = display.Representation == 'Volume'

                    if is_volume:
                        # For volume rendering, set a more appropriate default opacity curve
                        # Linear ramp from transparent to opaque
                        mid_point = (data_range[0] + data_range[1]) / 2.0
                        otf.Points = [
                            data_range[0], 0.0, 0.5, 0.0,
                            mid_point, 0.5, 0.5, 0.0,
                            data_range[1], 1.0, 0.5, 0.0
                        ]
                        self.logger.info("Reset opacity for volume rendering")
                    else:
                        # For surface rendering, fully opaque
                        otf.Points = [
                            data_range[0], 1.0, 0.5, 0.0,
                            data_range[1], 1.0, 0.5, 0.0
                        ]
                        self.logger.info("Reset opacity for surface rendering")

                # Force render to show changes
                Render(view)

                return True, f"Successfully reset colormap for '{array_name}' with range [{data_range[0]:.3f}, {data_range[1]:.3f}]"

            except Exception as e:
                self.logger.error(f"Error resetting colormap for {array_name}: {str(e)}")
                return False, f"Error resetting colormap for {array_name}: {str(e)}"

        except Exception as e:
            self.logger.error(f"Error in reset_colormaps: {str(e)}")
            return False, f"Error in reset_colormaps: {str(e)}"

    def reset_camera(self, padding_factor=1.0):
        """
        Reset the camera to show all data with optional padding for better framing.

        Args:
            padding_factor (float): Multiplier for camera distance to add padding around objects.
                                   1.0 = no padding (default), 1.5 = 50% padding, 2.0 = 100% padding.
                                   Recommended range: 1.0-2.0.

        Returns:
            tuple: (success, message)
        """
        try:
            from paraview.simple import GetActiveView, ResetCamera, Render
            view = GetActiveView()
            if not view:
                return False, "Error: No active view."

            # Reset camera to fit all visible objects
            ResetCamera(view)

            # Apply padding by adjusting camera distance if requested
            if padding_factor > 1.0:
                camera = view.GetActiveCamera()
                if camera:
                    # Get current camera position and focal point
                    position = camera.GetPosition()
                    focal_point = camera.GetFocalPoint()

                    # Calculate direction vector from focal point to camera
                    import math
                    dx = position[0] - focal_point[0]
                    dy = position[1] - focal_point[1]
                    dz = position[2] - focal_point[2]

                    # Calculate current distance
                    distance = math.sqrt(dx*dx + dy*dy + dz*dz)

                    # Apply padding factor
                    if distance > 0:
                        scale = padding_factor
                        new_position = [
                            focal_point[0] + dx * scale,
                            focal_point[1] + dy * scale,
                            focal_point[2] + dz * scale
                        ]
                        camera.SetPosition(new_position)
                        Render(view)

            padding_msg = f" with {padding_factor}x padding" if padding_factor > 1.0 else ""
            return True, f"Camera reset{padding_msg}"
        except Exception as e:
            self.logger.error(f"Error resetting camera: {str(e)}")
            return False, f"Error resetting camera: {str(e)}"
    

    def plot_over_line(self, point1=None, point2=None, resolution=100):
        """
        Create a 'Plot Over Line' filter to sample data along a line between two points.

        Args:
            point1 (list/tuple or None): The [x, y, z] coordinates of the start point. If None, will use data bounds.
            point2 (list/tuple or None): The [x, y, z] coordinates of the end point. If None, will use data bounds.
            resolution (int): Number of sample points along the line (default: 100).

        Returns:
            tuple: (success: bool, message: str, plot_filter)
        """
        try:
            from paraview.simple import GetActiveSource, PlotOverLine, Show, GetActiveView, CreateView, AssignViewToLayout
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None

            # Create the PlotOverLine filter
            plot_filter = PlotOverLine(Input=source)
            if point1 is not None:
                plot_filter.Point1 = point1
            if point2 is not None:
                plot_filter.Point2 = point2
            plot_filter.Resolution = resolution

            # Show the result in the active view (usually a line chart view)
            view = GetActiveView()
            Show(plot_filter, view)
            # Create a new 'Line Chart View'
            lineChartView1 = CreateView('XYChartView')

            # show data in view
            plotOverLine1Display_1 = Show(plot_filter, lineChartView1, 'XYChartRepresentation')
            AssignViewToLayout(view=lineChartView1)

            return True, f"Plot over line created from {plot_filter.Point1} to {plot_filter.Point2} with {resolution} points.", plot_filter
        except Exception as e:
            self.logger.error(f"Error creating plot over line: {str(e)}")
            return False, f"Error creating plot over line: {str(e)}", None

    def warp_by_vector(self, vector_field=None, scale_factor=1.0):
        """
        Apply the 'Warp By Vector' filter to the active source.

        Args:
            vector_field (str, optional): The name of the vector field to use for warping. If None, the first available vector field will be used.
            scale_factor (float, optional): The scale factor for the warp (default: 1.0).

        Returns:
            tuple: (success: bool, message: str, warp_filter)
        """
        try:
            from paraview.simple import GetActiveSource, WarpByVector, Show, GetActiveView
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None

            # If vector_field is not specified, try to auto-detect a vector field
            if vector_field is None:
                data_info = source.GetDataInformation()
                point_info = data_info.GetPointDataInformation()
                num_arrays = point_info.GetNumberOfArrays()
                found = False
                for i in range(num_arrays):
                    array_info = point_info.GetArrayInformation(i)
                    if array_info.GetNumberOfComponents() > 1:
                        vector_field = array_info.GetName()
                        found = True
                        break
                if not found:
                    return False, "No vector field found in the active source.", None

            # Create the WarpByVector filter
            warp_filter = WarpByVector(Input=source)
            warp_filter.Vectors = ['POINTS', vector_field]
            warp_filter.ScaleFactor = scale_factor

            # Show the result in the active view
            view = GetActiveView()
            Show(warp_filter, view)

            return True, f"Warp by vector applied using field '{vector_field}' with scale factor {scale_factor}.", warp_filter
        except Exception as e:
            self.logger.error(f"Error creating warp by vector: {str(e)}")
            return False, f"Error creating warp by vector: {str(e)}", None

    def create_delaunay3d(self, alpha=0.0, offset=2.0, tolerance=0.001):
        """
        Create a 3D Delaunay triangulation of the active dataset.
        
        Args:
            alpha (float): Specify alpha (or distance) value to control output. For non-zero alpha value, 
                          only edges or triangles contained within alpha radius are output. 
                          Default is 0.0 which produces the convex hull.
            offset (float): Offset to multiply the radius of the circumsphere by. Default is 2.0.
            tolerance (float): Specify a tolerance to control discarding of degenerate tetrahedra. Default is 0.001.
        
        Returns:
            tuple: (success: bool, message: str, delaunay_filter, delaunay_name: str)
        """
        try:
            from paraview.simple import GetActiveSource, Delaunay3D, Show, GetActiveView, SetActiveSource
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""
            
            # Check if source has points to triangulate
            data_info = source.GetDataInformation()
            if data_info.GetNumberOfPoints() < 4:
                return False, "Error: Source must have at least 4 points for 3D Delaunay triangulation.", None, ""
            
            # Create the Delaunay3D filter
            delaunay_filter = Delaunay3D(Input=source)
            delaunay_filter.Alpha = alpha
            delaunay_filter.Offset = offset
            delaunay_filter.Tolerance = tolerance
            
            # Show the result in the active view
            view = GetActiveView()
            display = Show(delaunay_filter, view)
            
            # Set to wireframe representation to clearly show the triangulation mesh
            display.SetRepresentationType('Wireframe')
            
            # Set the delaunay filter as the active source
            SetActiveSource(delaunay_filter)
            
            # Get the source name using the helper function
            delaunay_name = self._get_source_name(delaunay_filter)
            
            message = f"Created 3D Delaunay triangulation with alpha={alpha}, offset={offset}, tolerance={tolerance}. Set to wireframe representation to show triangular mesh. Filter name: {delaunay_name}"
            return True, message, delaunay_filter, delaunay_name
            
        except Exception as e:
            self.logger.error(f"Error creating 3D Delaunay triangulation: {str(e)}")
            return False, f"Error creating 3D Delaunay triangulation: {str(e)}", None, ""

    def filter_data(self, filter_type="threshold", field_name=None, min_value=None, max_value=None, 
                    invert=False, all_points=False):
        """
        Apply data filtering operations including threshold and selection extraction.
        Combines threshold and extract selection functionality into a single versatile filter.
        
        Args:
            filter_type (str): Type of filter - "threshold", "clip_scalar", or "extract_selection"
            field_name (str): Name of the scalar field to filter by
            min_value (float, optional): Minimum threshold value
            max_value (float, optional): Maximum threshold value  
            invert (bool): Whether to invert the selection (keep values outside range)
            all_points (bool): For threshold - whether to include all points in cells that pass
            
        Returns:
            tuple: (success: bool, message: str, filter_object, filter_name: str)
        """
        try:
            from paraview.simple import GetActiveSource, Threshold, Show, GetActiveView, SetActiveSource
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""
            
            # Auto-detect field if not provided
            if field_name is None:
                data_info = source.GetDataInformation()
                point_info = data_info.GetPointDataInformation()
                if point_info.GetNumberOfArrays() > 0:
                    field_name = point_info.GetArrayInformation(0).GetName()
                else:
                    cell_info = data_info.GetCellDataInformation()
                    if cell_info.GetNumberOfArrays() > 0:
                        field_name = cell_info.GetArrayInformation(0).GetName()
                    else:
                        return False, "Error: No data arrays found for filtering.", None, ""
            
            # Create appropriate filter based on type
            if filter_type.lower() in ["threshold", "extract_selection"]:
                filter_obj = Threshold(Input=source)
                
                # Set the scalar array to threshold by
                filter_obj.Scalars = ['POINTS', field_name]  # Try points first
                
                # Handle threshold range - use LowerThreshold/UpperThreshold for ParaView 5.10+ compatibility
                if min_value is not None and max_value is not None:
                    filter_obj.LowerThreshold = min_value
                    filter_obj.UpperThreshold = max_value
                elif min_value is not None:
                    filter_obj.LowerThreshold = min_value
                    filter_obj.UpperThreshold = float('inf')
                elif max_value is not None:
                    filter_obj.LowerThreshold = float('-inf')
                    filter_obj.UpperThreshold = max_value
                else:
                    # Get data range for default thresholding
                    data_info = source.GetDataInformation()
                    point_info = data_info.GetPointDataInformation()
                    for i in range(point_info.GetNumberOfArrays()):
                        array_info = point_info.GetArrayInformation(i)
                        if array_info.GetName() == field_name:
                            data_range = array_info.GetComponentRange(0)
                            mid_value = (data_range[0] + data_range[1]) / 2
                            filter_obj.LowerThreshold = mid_value
                            filter_obj.UpperThreshold = data_range[1]
                            break
                
                # Set additional properties
                filter_obj.Invert = invert
                # Note: AllPoints is not a valid property for Threshold filter in current ParaView version
                
                operation_desc = f"threshold on field '{field_name}'"
                if min_value is not None and max_value is not None:
                    operation_desc += f" range [{min_value}, {max_value}]"
                elif min_value is not None:
                    operation_desc += f" >= {min_value}"
                elif max_value is not None:
                    operation_desc += f" <= {max_value}"
                
            else:
                return False, f"Error: Unsupported filter type '{filter_type}'.", None, ""
            
            # Show the result
            view = GetActiveView()
            Show(filter_obj, view)
            SetActiveSource(filter_obj)
            
            # Get filter name
            filter_name = self._get_source_name(filter_obj)
            
            message = f"Applied {operation_desc}. Filter name: {filter_name}"
            if invert:
                message += " (inverted)"
                
            return True, message, filter_obj, filter_name
            
        except Exception as e:
            self.logger.error(f"Error applying data filter: {str(e)}")
            return False, f"Error applying data filter: {str(e)}", None, ""

    def calculate_field(self, result_name, expression, attribute_mode="Point Data"):
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
            tuple: (success: bool, message: str, calculator, calculator_name: str)
        """
        try:
            from paraview.simple import GetActiveSource, Calculator, Show, GetActiveView, SetActiveSource
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""
            
            # Create calculator filter
            calc_filter = Calculator(Input=source)
            calc_filter.ResultArrayName = result_name
            calc_filter.Function = expression
            calc_filter.AttributeType = attribute_mode
            
            # Show the result
            view = GetActiveView()
            Show(calc_filter, view)
            SetActiveSource(calc_filter)
            
            # Get filter name
            calc_name = self._get_source_name(calc_filter)
            
            message = f"Created calculated field '{result_name}' using expression '{expression}'. Calculator name: {calc_name}"
            return True, message, calc_filter, calc_name
            
        except Exception as e:
            self.logger.error(f"Error creating calculated field: {str(e)}")
            return False, f"Error creating calculated field: {str(e)}", None, ""

    def transform_data(self, operation="translate", translate_x=0.0, translate_y=0.0, translate_z=0.0,
                      rotate_x=0.0, rotate_y=0.0, rotate_z=0.0, scale_x=1.0, scale_y=1.0, scale_z=1.0):
        """
        Apply geometric transformations to datasets.
        Combines translation, rotation, and scaling into a single versatile transform operation.
        
        Args:
            operation (str): Transform type - "translate", "rotate", "scale", or "combined"
            translate_x, translate_y, translate_z (float): Translation amounts
            rotate_x, rotate_y, rotate_z (float): Rotation angles in degrees
            scale_x, scale_y, scale_z (float): Scale factors
            
        Returns:
            tuple: (success: bool, message: str, transform, transform_name: str)
        """
        try:
            from paraview.simple import GetActiveSource, Transform, Show, GetActiveView, SetActiveSource
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""
            
            # Create transform filter
            transform_filter = Transform(Input=source)
            transform_filter.Transform = 'Transform'
            
            # Apply transformations based on operation type
            operations_applied = []
            
            if operation.lower() in ["translate", "combined"]:
                if translate_x != 0.0 or translate_y != 0.0 or translate_z != 0.0:
                    transform_filter.Transform.Translate = [translate_x, translate_y, translate_z]
                    operations_applied.append(f"translate({translate_x}, {translate_y}, {translate_z})")
            
            if operation.lower() in ["rotate", "combined"]:
                if rotate_x != 0.0 or rotate_y != 0.0 or rotate_z != 0.0:
                    transform_filter.Transform.Rotate = [rotate_x, rotate_y, rotate_z]
                    operations_applied.append(f"rotate({rotate_x}°, {rotate_y}°, {rotate_z}°)")
            
            if operation.lower() in ["scale", "combined"]:
                if scale_x != 1.0 or scale_y != 1.0 or scale_z != 1.0:
                    transform_filter.Transform.Scale = [scale_x, scale_y, scale_z]
                    operations_applied.append(f"scale({scale_x}, {scale_y}, {scale_z})")
            
            # Show the result
            view = GetActiveView()
            Show(transform_filter, view)
            SetActiveSource(transform_filter)
            
            # Get transform name
            transform_name = self._get_source_name(transform_filter)
            
            operations_str = " + ".join(operations_applied) if operations_applied else "identity transform"
            message = f"Applied geometric transform: {operations_str}. Transform name: {transform_name}"
            
            return True, message, transform_filter, transform_name
            
        except Exception as e:
            self.logger.error(f"Error applying transform: {str(e)}")
            return False, f"Error applying transform: {str(e)}", None, ""

    def create_vector_visualization(self, glyph_type="arrow", vector_field=None, scale_factor=None,
                                   scale_mode="vector", max_number_of_glyphs=5000, auto_scale=True,
                                   scale_percentage=0.01):
        """
        Create vector field visualizations using glyphs.
        Combines glyph functionality for arrows, cones, spheres to visualize vector data.

        Args:
            glyph_type (str): Type of glyph - "arrow", "cone", "sphere", "line"
            vector_field (str, optional): Name of vector field. Auto-detected if None.
            scale_factor (float, optional): Overall scaling factor for glyphs. Auto-computed if None.
            scale_mode (str): "vector", "scalar", or "off" - how to scale glyphs
            max_number_of_glyphs (int): Maximum number of glyphs to display
            auto_scale (bool): Automatically compute scale factor based on data bounds if scale_factor is None
            scale_percentage (float): Percentage of data diagonal to use for auto-scaling (default: 0.01 = 1%)

        Returns:
            tuple: (success: bool, message: str, glyph_filter, glyph_name: str)
        """
        try:
            from paraview.simple import (GetActiveSource, Glyph, Show, GetActiveView,
                                        SetActiveSource, Arrow, Cone, Sphere, Line)

            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""

            # Auto-detect vector field if not provided
            if vector_field is None:
                data_info = source.GetDataInformation()
                point_info = data_info.GetPointDataInformation()
                for i in range(point_info.GetNumberOfArrays()):
                    array_info = point_info.GetArrayInformation(i)
                    # Only use arrays with exactly 3 components (vectors)
                    # Skip gradient tensors (9 components) and other multi-component arrays
                    if array_info.GetNumberOfComponents() == 3:
                        vector_field = array_info.GetName()
                        break

                if vector_field is None:
                    return False, "Error: No 3-component vector field found for glyph visualization.", None, ""

            # Auto-compute scale factor based on data bounds if not provided
            if scale_factor is None and auto_scale:
                data_info = source.GetDataInformation()
                bounds = data_info.GetBounds()
                # Compute diagonal of bounding box
                diagonal = ((bounds[1] - bounds[0])**2 +
                           (bounds[3] - bounds[2])**2 +
                           (bounds[5] - bounds[4])**2)**0.5
                # Use scale_percentage of diagonal (default 1%)
                scale_factor = diagonal * scale_percentage
                self.logger.info(f"Auto-computed glyph scale factor: {scale_factor} ({scale_percentage*100}% of data diagonal)")
            elif scale_factor is None:
                scale_factor = 1.0  # Default if auto_scale is False

            # Map glyph type string to proper glyph source
            glyph_type_map = {
                "arrow": "Arrow",
                "cone": "Cone",
                "sphere": "Sphere",
                "line": "Line"
            }

            glyph_type_name = glyph_type_map.get(glyph_type.lower(), "Arrow")

            # Create glyph filter with proper GlyphType
            glyph_filter = Glyph(Input=source)
            glyph_filter.GlyphType = glyph_type_name

            # Set vector field for orientation and scaling
            glyph_filter.OrientationArray = ['POINTS', vector_field]
            glyph_filter.ScaleArray = ['POINTS', vector_field] if scale_mode == "vector" else ['POINTS', '']
            glyph_filter.ScaleFactor = scale_factor
            
            # Control glyph density
            glyph_filter.MaximumNumberOfSamplePoints = max_number_of_glyphs

            # Set scaling mode using the VectorScaleMode property (ParaView 5.x+)
            if scale_mode.lower() == "vector":
                # When scaling by vector, use VectorScaleMode property
                glyph_filter.VectorScaleMode = 'Scale by Magnitude'
                glyph_filter.ScaleFactor = scale_factor
            elif scale_mode.lower() == "scalar":
                # ScaleArray already set to appropriate scalar field above
                glyph_filter.ScaleFactor = scale_factor
            else:
                # No scaling - use uniform size
                glyph_filter.ScaleArray = ['POINTS', '']
                glyph_filter.ScaleFactor = scale_factor
            
            # Update pipeline before showing (ensures VTK objects are initialized)
            from paraview.simple import UpdatePipeline
            UpdatePipeline(proxy=glyph_filter)

            # Show the result
            view = GetActiveView()
            Show(glyph_filter, view)
            SetActiveSource(glyph_filter)

            # Get glyph name
            glyph_name = self._get_source_name(glyph_filter)
            
            message = f"Created {glyph_type} glyph visualization for vector field '{vector_field}'. Filter name: {glyph_name}"
            return True, message, glyph_filter, glyph_name
            
        except Exception as e:
            self.logger.error(f"Error creating vector visualization: {str(e)}")
            return False, f"Error creating vector visualization: {str(e)}", None, ""

    def analyze_field_data(self, analysis_type="gradient", field_name=None, compute_vorticity=False, 
                          compute_divergence=False, compute_qcriterion=False):
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
            tuple: (success: bool, message: str, analysis_filter, filter_name: str)
        """
        try:
            from paraview.simple import (GetActiveSource, Gradient,
                                       Connectivity, Show, GetActiveView, SetActiveSource)
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", None, ""
            
            # Auto-detect field if not provided
            if field_name is None and analysis_type.lower() in ["gradient", "combined"]:
                data_info = source.GetDataInformation()
                point_info = data_info.GetPointDataInformation()
                if point_info.GetNumberOfArrays() > 0:
                    # Prefer vector fields for gradient analysis
                    for i in range(point_info.GetNumberOfArrays()):
                        array_info = point_info.GetArrayInformation(i)
                        if array_info.GetNumberOfComponents() >= 3:
                            field_name = array_info.GetName()
                            break
                    # Fall back to any field
                    if field_name is None:
                        field_name = point_info.GetArrayInformation(0).GetName()
            
            results = []
            filter_objects = []
            
            # Apply gradient analysis
            if analysis_type.lower() in ["gradient", "combined"]:
                if field_name:
                    from paraview.simple import UpdatePipeline
                    gradient_filter = Gradient(Input=source)
                    gradient_filter.ScalarArray = ['POINTS', field_name]

                    # Configure gradient computation options
                    if compute_vorticity:
                        gradient_filter.ComputeVorticity = True
                    if compute_divergence:
                        gradient_filter.ComputeDivergence = True
                    if compute_qcriterion:
                        gradient_filter.ComputeQCriterion = True

                    # Update pipeline to ensure VTK objects are initialized
                    UpdatePipeline(proxy=gradient_filter)

                    Show(gradient_filter)
                    filter_objects.append(gradient_filter)
                    grad_name = self._get_source_name(gradient_filter)
                    results.append(f"gradient analysis of '{field_name}' -> {grad_name}")

                    # Set as active for further processing
                    SetActiveSource(gradient_filter)
                    primary_filter = gradient_filter
                else:
                    return False, "Error: No field available for gradient analysis.", None, ""
            
            # Apply connectivity analysis
            if analysis_type.lower() in ["connectivity", "combined"]:
                # Use current active source (might be gradient result)
                current_source = GetActiveSource()
                connectivity_filter = Connectivity(Input=current_source)
                
                Show(connectivity_filter)
                filter_objects.append(connectivity_filter)
                conn_name = self._get_source_name(connectivity_filter)
                results.append(f"connectivity analysis -> {conn_name}")
                
                SetActiveSource(connectivity_filter)
                primary_filter = connectivity_filter
            
            # Return information about primary filter
            if filter_objects:
                primary_name = self._get_source_name(primary_filter)
                analysis_desc = " + ".join(results)
                message = f"Applied field analysis: {analysis_desc}"
                return True, message, primary_filter, primary_name
            else:
                return False, "Error: No analysis operations were performed.", None, ""
            
        except Exception as e:
            self.logger.error(f"Error analyzing field data: {str(e)}")
            return False, f"Error analyzing field data: {str(e)}", None, ""

    def export_data(self, export_format="csv", filename=None, export_type="all"):
        """
        Export data in various formats with enhanced capabilities.
        Combines multiple export formats into a single versatile function.
        
        Args:
            export_format (str): "csv", "vtk", "stl", "ply", "obj" 
            filename (str, optional): Output filename. Auto-generated if None.
            export_type (str): "all", "points", "cells", "arrays" - what to export
            
        Returns:
            tuple: (success: bool, message: str, exported_path: str)
        """
        try:
            import os
            from paraview.simple import GetActiveSource, SaveData
            
            source = GetActiveSource()
            if not source:
                return False, "Error: No active source. Load data first.", ""
            
            # Generate filename if not provided
            if filename is None:
                source_name = self._get_source_name(source) or "data"
                filename = f"{source_name}_export.{export_format.lower()}"
            
            # Ensure we have the data folder path, use current directory as fallback
            if not hasattr(self, "_data_folder") or not self._data_folder:
                self._data_folder = os.getcwd()
                self.logger.info(f"No data folder set, using current directory: {self._data_folder}")
            export_path = os.path.join(self._data_folder, filename)
            
            # Configure export options based on format
            export_options = {}
            
            if export_format.lower() == "csv":
                # For CSV, we typically want point data
                # Note: Some ParaView versions may not support all options
                try:
                    # Try to set options if they exist
                    export_options['FieldAssociation'] = 'Point Data'
                except:
                    pass  # Option not available in this version
                
            elif export_format.lower() in ["vtk", "vtu", "vtp"]:
                # VTK formats support full data preservation
                export_options['DataMode'] = 'Binary'  # More efficient than ASCII
                
            elif export_format.lower() in ["stl", "ply", "obj"]:
                # Mesh formats - ensure we have surface data
                data_info = source.GetDataInformation()
                if data_info.GetDataSetType() not in [0, 4]:  # Not polydata or unstructured grid
                    return False, f"Error: {export_format.upper()} export requires surface/mesh data. Current data type not suitable.", ""
            
            # Perform the export
            SaveData(export_path, proxy=source, **export_options)
            
            # Verify export succeeded
            if os.path.exists(export_path):
                file_size = os.path.getsize(export_path)
                message = f"Successfully exported data to {export_format.upper()} format: {export_path} ({file_size} bytes)"
                return True, message, export_path
            else:
                return False, f"Error: Export to {export_path} failed - file not created.", ""
            
        except Exception as e:
            self.logger.error(f"Error exporting data: {str(e)}")
            return False, f"Error exporting data: {str(e)}", ""

    
    def save_state(self, save_directory: str, filename: str = "paraview_state.pvsm") -> tuple[bool, str, str]:
        """
        Save the current ParaView state to a file.
        
        Args:
            save_directory: Directory where the state file will be saved
            filename: Name of the state file (default: "paraview_state.pvsm")
            
        Returns:
            tuple: (success, message, file_path)
        """
        try:
            import os
            from pathlib import Path
            
            # Ensure the directory exists
            save_dir = Path(save_directory)
            save_dir.mkdir(parents=True, exist_ok=True)
            
            # Ensure filename has .pvsm extension
            if not filename.endswith('.pvsm'):
                filename += '.pvsm'
                
            # Full path to the state file
            state_file_path = save_dir / filename
            
            # Save the state
            SaveState(str(state_file_path))
            
            message = f"ParaView state saved successfully to: {state_file_path}"
            self.logger.info(message)
            return True, message, str(state_file_path)
            
        except Exception as e:
            error_msg = f"Error saving ParaView state: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg, ""
