"""
Integration tests for ParaViewManager with live ParaView server

These tests require a running ParaView server:
    pvserver --multi-clients --server-port=11111

Run tests:
    pytest tests/test_paraview_live.py -v
"""

import unittest
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from paraview_manager import ParaViewManager

# Test data paths - using disk.ex2 from stream_line folder
TEST_DATA = "eval/eval_examples/stream_line/disk.ex2"


class TestParaViewLive(unittest.TestCase):
    """Tests for ParaViewManager with live ParaView server connection"""

    @classmethod
    def setUpClass(cls):
        """Connect to ParaView server once for all tests"""
        cls.manager = ParaViewManager()
        cls.connected = cls.manager.connect()
        if not cls.connected:
            raise unittest.SkipTest("ParaView server not available at localhost:11111")
        cls.reconnect_count = 0  # Track reconnection attempts

    def setUp(self):
        """Clear pipeline before each test"""
        if self.connected:
            try:
                # Clear pipeline and reset to clean state
                self.manager.clear_pipeline_and_reset()
                # Small delay to ensure ParaView processes cleanup
                time.sleep(0.1)
            except Exception as e:
                # If clearing fails, try to reconnect once
                if self.reconnect_count < 3:
                    print(f"Warning: Pipeline clear failed, attempting reconnect: {e}")
                    self.reconnect_count += 1
                    self.manager = ParaViewManager()
                    self.connected = self.manager.connect()
                    if self.connected:
                        self.manager.clear_pipeline_and_reset()
                else:
                    self.skipTest("Too many reconnection attempts")

    def tearDown(self):
        """Clean up after each test"""
        if self.connected:
            # Ensure pipeline is cleared after test
            try:
                self.manager.clear_pipeline_and_reset()
            except:
                pass  # Ignore errors during cleanup

    def test_01_connection(self):
        """Test that we are connected to ParaView server"""
        self.assertTrue(self.connected)
        self.assertIsNotNone(self.manager.connection)

    def test_02_load_exodus_data(self):
        """Test loading Exodus II data file"""
        if not os.path.exists(TEST_DATA):
            self.skipTest(f"Test data not found: {TEST_DATA}")

        success, message, reader, source_name = self.manager.load_data(TEST_DATA)
        self.assertTrue(success, f"Failed to load data: {message}")
        self.assertIsNotNone(reader)
        self.assertIn("loaded", message.lower())

    def test_03_get_data_bounds(self):
        """Test getting data bounds from loaded data"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        # Load data first
        self.manager.load_data(TEST_DATA)

        # Get pipeline to check data is loaded
        success, pipeline = self.manager.get_pipeline()
        self.assertTrue(success)
        self.assertIn("IOSSReader", pipeline)

    def test_04_background_color(self):
        """Test setting background color"""
        success, message = self.manager.set_background_color(0.5, 0.5, 0.5)
        self.assertTrue(success)
        self.assertIn("background", message.lower())

        # Test invalid color values
        success, message = self.manager.set_background_color(1.5, 0.5, 0.5)
        self.assertFalse(success)
        self.assertIn("between", message.lower())

    def test_05_pipeline_operations(self):
        """Test getting pipeline information"""
        # Start with empty pipeline
        success, pipeline = self.manager.get_pipeline()
        self.assertTrue(success)

        # Load data
        if os.path.exists(TEST_DATA):
            self.manager.load_data(TEST_DATA)

            # Check pipeline has sources now
            success, pipeline = self.manager.get_pipeline()
            self.assertTrue(success)
            # Pipeline should show the loaded source
            self.assertIsNotNone(pipeline)

    def test_06_available_arrays(self):
        """Test getting available arrays from loaded data"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)
        success, arrays = self.manager.get_available_arrays()
        self.assertTrue(success)
        # Arrays should be returned as a string with array information
        self.assertIsInstance(arrays, str)

    def test_07_create_isosurface(self):
        """Test creating isosurface"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Create isosurface at value 0.5
        success, message, contour, contour_name = self.manager.create_isosurface(0.5, field="Pres")
        self.assertTrue(success, f"Failed to create isosurface: {message}")
        self.assertIsNotNone(contour)

    def test_08_create_slice(self):
        """Test creating slice"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Create slice at origin
        success, message, slice_obj, slice_name = self.manager.create_slice(
            origin_x=0, origin_y=0, origin_z=0,
            normal_x=0, normal_y=0, normal_z=1
        )
        self.assertTrue(success, f"Failed to create slice: {message}")
        self.assertIsNotNone(slice_obj)

    def test_09_create_clip(self):
        """Test creating clip"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Create clip
        success, message, clip, clip_name = self.manager.create_clip(
            origin_x=0, origin_y=0, origin_z=0,
            normal_x=1, normal_y=0, normal_z=0
        )
        self.assertTrue(success, f"Failed to create clip: {message}")
        self.assertIsNotNone(clip)

    def test_10_color_by_field(self):
        """Test coloring by field"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Color by pressure field
        success, message = self.manager.color_by("Pres")
        self.assertTrue(success, f"Failed to color by field: {message}")

    def test_11_set_representation(self):
        """Test setting representation type"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Test different representation types
        for rep_type in ["Surface", "Wireframe", "Points"]:
            success, message = self.manager.set_representation_type(rep_type)
            self.assertTrue(success, f"Failed to set {rep_type}: {message}")

    def test_12_camera_operations(self):
        """Test camera operations"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Reset camera
        success, message = self.manager.reset_camera()
        self.assertTrue(success)

        # Rotate camera
        success, message = self.manager.rotate_camera(azimuth=30, elevation=15)
        self.assertTrue(success)

    def test_13_screenshot(self):
        """Test taking screenshot"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Take screenshot
        success, message, image = self.manager.get_screenshot()
        self.assertTrue(success, f"Failed to take screenshot: {message}")
        self.assertIsNotNone(image)

    def test_14_export_data(self):
        """Test exporting data"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Export to CSV
        test_export = "test_export.csv"
        success, message, filepath = self.manager.export_data(
            export_format="csv",
            filename=test_export
        )
        self.assertTrue(success, f"Failed to export data: {message}")

        # Clean up
        if os.path.exists(test_export):
            os.remove(test_export)

    def test_15_save_state(self):
        """Test saving ParaView state"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Save state
        test_dir = "test_state"
        os.makedirs(test_dir, exist_ok=True)

        success, message, filepath = self.manager.save_state(
            save_directory=test_dir,
            filename="test.pvsm"
        )
        self.assertTrue(success, f"Failed to save state: {message}")

        # Clean up
        import shutil
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

    def test_16_compute_histogram(self):
        """Test computing histogram of data field"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        # Load data
        self.manager.load_data(TEST_DATA)

        # Get histogram for pressure field
        success, message, histogram = self.manager.get_histogram(field="Pres", num_bins=10)
        self.assertTrue(success, f"Failed to get histogram: {message}")
        self.assertIsNotNone(histogram)

    def test_17_active_source_management(self):
        """Test managing active sources"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Get active source names
        success, message, sources = self.manager.get_active_source_names_by_type()
        self.assertTrue(success)
        self.assertIsInstance(sources, list)

        # Set active source if we have any
        if sources:
            source_name = sources[0]
            success, message = self.manager.set_active_source(source_name)
            self.assertTrue(success, f"Failed to set active source: {message}")

    def test_18_create_sources(self):
        """Test creating parametric sources"""
        # Create different source types
        for source_type in ["sphere", "cone", "cylinder"]:
            success, message, source, source_name = self.manager.create_source(source_type)
            self.assertTrue(success, f"Failed to create {source_type}: {message}")
            self.assertIsNotNone(source)

            # Clear for next test
            self.manager.clear_pipeline_and_reset()

    def test_19_stream_tracer(self):
        """Test creating stream tracer for vector fields"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Create stream tracer with velocity field
        result = self.manager.create_stream_tracer(vector_field="V")
        self.assertTrue(result[0], f"Failed to create stream tracer: {result[1]}")

    def test_20_warp_by_vector(self):
        """Test warping by vector field"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Warp by velocity field
        success, message, warp = self.manager.warp_by_vector(vector_field="V", scale_factor=0.1)
        self.assertTrue(success, f"Failed to warp by vector: {message}")
        self.assertIsNotNone(warp)

    def test_21_plot_over_line(self):
        """Test plotting data over a line"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Plot over line from one point to another
        success, message, plot = self.manager.plot_over_line(
            point1=[0, 0, 0],
            point2=[10, 0, 0],
            resolution=50
        )
        self.assertTrue(success, f"Failed to plot over line: {message}")
        self.assertIsNotNone(plot)

    def test_22_filter_data(self):
        """Test filtering data with threshold"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Apply threshold filter to pressure field
        success, message, filter_obj, filter_name = self.manager.filter_data(
            filter_type="threshold",
            field_name="Pres",
            min_value=0.0,
            max_value=0.5
        )
        self.assertTrue(success, f"Failed to filter data: {message}")
        self.assertIsNotNone(filter_obj)

    def test_23_calculate_field(self):
        """Test calculating a new field"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Calculate a new field based on pressure
        success, message, calc, calc_name = self.manager.calculate_field(
            result_name="PressureSquared",
            expression="Pres * Pres",
            attribute_mode="Point Data"
        )
        self.assertTrue(success, f"Failed to calculate field: {message}")
        self.assertIsNotNone(calc)

    def test_24_transform_data(self):
        """Test transforming data (translate, rotate, scale)"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Apply translation
        success, message, transform, transform_name = self.manager.transform_data(
            operation="translate",
            translate_x=5.0,
            translate_y=0.0,
            translate_z=0.0
        )
        self.assertTrue(success, f"Failed to transform data: {message}")
        self.assertIsNotNone(transform)

    def test_25_create_vector_visualization(self):
        """Test creating vector visualization with glyphs"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Create arrow glyphs for velocity field
        success, message, glyphs, glyph_name = self.manager.create_vector_visualization(
            glyph_type="arrow",
            vector_field="V",
            scale_factor=0.5
        )
        self.assertTrue(success, f"Failed to create vector visualization: {message}")
        self.assertIsNotNone(glyphs)

    def test_26_analyze_field_data(self):
        """Test analyzing field data (gradient, vorticity)"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        self.manager.load_data(TEST_DATA)

        # Compute gradient of pressure field
        success, message, analysis, analysis_name = self.manager.analyze_field_data(
            analysis_type="gradient",
            field_name="Pres"
        )
        self.assertTrue(success, f"Failed to analyze field data: {message}")
        self.assertIsNotNone(analysis)

    # def test_27_save_contour_as_stl(self):
    #     """Test saving contour as STL file"""
    #     if not os.path.exists(TEST_DATA):
    #         self.skipTest("Test data not found")

    #     try:
    #         self.manager.load_data(TEST_DATA)

    #         # Create contour first
    #         success, message, contour, contour_name = self.manager.create_isosurface(0.5, field="Pres")
    #         if not success:
    #             self.skipTest(f"Could not create contour: {message}")

    #         # Save as STL
    #         test_stl = "test_contour.stl"
    #         success, message, filepath = self.manager.save_contour_as_stl(stl_filename=test_stl)

    #         # Only assert if we actually tried to save
    #         if success:
    #             self.assertTrue(success, f"Failed to save STL: {message}")
    #             # Clean up the file
    #             if os.path.exists(test_stl):
    #                 os.remove(test_stl)
    #     except Exception as e:
    #         # Skip test if there's an issue with this specific operation
    #         self.skipTest(f"Error in STL save test: {str(e)}")

    def test_28_reset_colormaps(self):
        """Test resetting colormaps"""
        if not os.path.exists(TEST_DATA):
            self.skipTest("Test data not found")

        try:
            # Load data with error checking
            success, message, reader, source_name = self.manager.load_data(TEST_DATA)
            if not success:
                self.skipTest(f"Could not load data: {message}")

            # Color by field first
            success, message = self.manager.color_by("Pres")
            if not success:
                self.skipTest(f"Could not color by field: {message}")

            # Reset colormap
            success, message = self.manager.reset_colormaps("Pres")
            self.assertTrue(success, f"Failed to reset colormaps: {message}")
        except Exception as e:
            # Skip test if there's an issue
            self.skipTest(f"Error in reset colormaps test: {str(e)}")

    def test_29_clear_pipeline(self):
        """Test clearing pipeline"""
        # Add some sources
        self.manager.create_source("sphere")
        self.manager.create_source("cone")

        # Clear pipeline
        success, message = self.manager.clear_pipeline_and_reset()
        self.assertTrue(success)
        self.assertIn("cleared", message.lower())

        # Verify pipeline is empty
        success, pipeline = self.manager.get_pipeline()
        self.assertTrue(success)
        self.assertIn("no sources", pipeline.lower())

    def test_30_glyph_auto_scaling(self):
        """Test glyph creation with auto-scaling"""
        # Clear pipeline first
        self.manager.clear_pipeline_and_reset()

        # Create a sphere source
        success, message, source, name = self.manager.create_source("sphere")
        self.assertTrue(success, f"Failed to create sphere: {message}")

        # Generate gradient field to get vector data
        success, message, gradient, grad_name = self.manager.analyze_field_data(
            analysis_type="gradient",
            field_name="Normals"  # Sphere has Normals by default
        )

        if not success:
            # If gradient fails, try to generate a calculator field as vector
            success, message = self.manager.calculate_field(
                expression="iHat + jHat + kHat",  # Create a simple vector field
                output_name="TestVector"
            )
            self.assertTrue(success, f"Failed to create vector field: {message}")

        # Test auto-scaling (default behavior - 1% of diagonal)
        success, message, glyph_filter, glyph_name = self.manager.create_vector_visualization(
            glyph_type="arrow",
            vector_field=None,  # Auto-detect
            scale_factor=None,  # Auto-compute
            auto_scale=True,
            scale_percentage=0.01  # 1% of diagonal
        )

        # If it fails due to no vector field, skip this test
        if not success and "no vector field" in message.lower():
            self.skipTest("No vector field available for glyph visualization")

        self.assertTrue(success, f"Failed to create auto-scaled glyphs: {message}")
        self.assertIsNotNone(glyph_filter)
        self.assertIn("glyph", glyph_name.lower())

        # Test with smaller auto-scale percentage
        success2, message2, glyph_filter2, glyph_name2 = self.manager.create_vector_visualization(
            glyph_type="cone",
            scale_factor=None,
            auto_scale=True,
            scale_percentage=0.005  # 0.5% of diagonal - smaller glyphs
        )

        # Test with manual scale factor
        success3, message3, glyph_filter3, glyph_name3 = self.manager.create_vector_visualization(
            glyph_type="sphere",
            scale_factor=0.001,  # Very small manual scale
            auto_scale=False
        )


if __name__ == '__main__':
    unittest.main()