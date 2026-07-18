#!/usr/bin/env python3
"""
Dataset Anonymizer for ParaView-MCP and SciVisAgentBench

This script anonymizes datasets and test files to prevent evaluation bias from
metadata or file naming patterns.

Usage:
    # Anonymize a YAML test file and copy referenced datasets
    python anonymize_dataset.py test.yaml [options]

    # Just anonymize paths in YAML without copying data (quick mode)
    python anonymize_dataset.py test.yaml --quick [options]

Options:
    --output-dir, -o: Output directory for anonymized data (default: eval/anonymized_datasets)
    --mapping, -m: Save/load anonymization mapping JSON file
    --dry-run: Preview changes without writing files
    --quick: Only update YAML paths without copying data files
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
import yaml


class DatasetAnonymizer:
    """Handles dataset anonymization for evaluation files."""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or Path("eval/anonymized_datasets")
        self.dataset_mapping: Dict[str, str] = {}  # Original dataset name -> anonymous
        self.file_mapping: Dict[str, str] = {}     # Original file path -> anonymous path
        self.counter = 0

    def generate_anonymous_name(self, original_path: str) -> str:
        """Generate anonymous name for a file path."""
        if original_path in self.file_mapping:
            return self.file_mapping[original_path]

        # Extract dataset name and file info
        path_parts = Path(original_path).parts

        # Find the dataset name (usually the folder before /data/)
        dataset_name = None
        for i, part in enumerate(path_parts):
            if part == "data" and i > 0:
                dataset_name = path_parts[i-1]
                break

        if not dataset_name:
            # If no /data/ folder, use the first meaningful directory
            for part in path_parts:
                if part not in ["..", ".", "SciVisAgentBench-tasks"]:
                    dataset_name = part
                    break

        if dataset_name and dataset_name not in self.dataset_mapping:
            self.counter += 1
            self.dataset_mapping[dataset_name] = f"dataset_{self.counter:03d}"

        # Build anonymous path
        anon_dataset = self.dataset_mapping.get(dataset_name, "unknown")
        filename = Path(original_path).name

        # Anonymize filename but keep extension and dimensions for compatibility
        if self._should_anonymize_filename(filename):
            ext = ''.join(Path(filename).suffixes)
            # Extract dimensions and data type from filename
            dimensions, dtype = self._extract_dimensions_and_type(filename)
            if dimensions and dtype and ext == '.raw':
                # Preserve dimensions and data type for RAW files
                anon_filename = f"data_{len(self.file_mapping):03d}_{dimensions}_{dtype}{ext}"
            else:
                # For non-RAW files or files without dimension info
                anon_filename = f"data_{len(self.file_mapping):03d}{ext}"
        else:
            anon_filename = filename

        # Create the anonymous path
        anon_path = str(self.output_dir / anon_dataset / "data" / anon_filename)
        self.file_mapping[original_path] = anon_path

        return anon_path

    def _should_anonymize_filename(self, filename: str) -> bool:
        """Check if a filename should be anonymized based on content hints."""
        # Keep generic names, anonymize descriptive ones
        generic_patterns = [
            r'^data_\d+',  # Already generic
            r'^file_\d+',  # Already generic
            r'^\d+x\d+x\d+',  # Just dimensions
        ]

        for pattern in generic_patterns:
            if re.match(pattern, filename, re.IGNORECASE):
                return False

        # Anonymize if contains descriptive words
        descriptive_words = [
            'aneurism', 'backpack', 'blunt', 'fin', 'bonsai', 'brain',
            'bunny', 'chest', 'christmas', 'tree', 'csafe', 'duct',
            'engine', 'foot', 'frog', 'head', 'hydrogen', 'atom',
            'knee', 'lobster', 'mag', 'field', 'marmoset', 'neurons',
            'mri', 'mrt', 'angio', 'neghip', 'nucleon', 'orange',
            'present', 'prone', 'stag', 'beetle', 'statue', 'leg',
            'supernova', 'teapot', 'tooth', 'tornado', 'toutatis',
            'vessel', 'visible', 'woman', 'male', 'zebrafish'
        ]

        filename_lower = filename.lower()
        for word in descriptive_words:
            if word in filename_lower:
                return True

        return False

    def _extract_dimensions_and_type(self, filename: str) -> tuple:
        """Extract dimensions and data type from RAW filename."""
        # Pattern: name_XxYxZ_datatype.raw or name_XxYxZxW_datatype.raw
        pattern = r'(\d+x\d+(?:x\d+)?(?:x\d+)?)_(\w+)\.raw'
        match = re.search(pattern, filename)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def extract_file_paths_from_yaml(self, yaml_file: Path) -> List[str]:
        """Extract all file paths from a YAML test file."""
        with open(yaml_file, 'r') as f:
            content = f.read()

        file_paths = []

        # Common patterns for file paths in YAML
        patterns = [
            # RAW files with dimensions in name
            r'["\']?([^"\'\\ ]+\.raw)["\']?',
            # Other data formats
            r'["\']?([^"\'\\ ]+\.(?:vtk|vti|vtp|vtu|ex2|nc|h5|hdf5))["\']?',
            # Paths with ../
            r'["\']?(\.\./.+?\.(?:raw|vtk|vti|vtp|vtu|ex2|nc|h5|hdf5))["\']?',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, content, re.MULTILINE)
            for match in matches:
                path = match.group(1)
                # Clean up the path
                path = path.strip('"\'')
                if path and path not in file_paths and not path.startswith('eval/anonymized'):
                    file_paths.append(path)

        return file_paths

    def copy_dataset_file(self, original_path: str, anonymous_path: str, dry_run: bool = False) -> bool:
        """Copy a dataset file to its anonymous location."""
        # Try to find the original file
        original = None
        search_paths = [
            Path(original_path),  # As-is
            Path.cwd() / original_path,  # Relative to CWD
            Path.cwd().parent / original_path.lstrip('../'),  # Parent directory
        ]

        for search_path in search_paths:
            if search_path.exists():
                original = search_path
                break

        if not original:
            print(f"  Warning: Could not find file: {original_path}")
            return False

        target = Path(anonymous_path)

        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            print(f"  Copied: {original_path} -> {anonymous_path}")
        else:
            print(f"  Would copy: {original_path} -> {anonymous_path}")

        return True

    def process_yaml_file(self, yaml_file: Path, quick_mode: bool = False, dry_run: bool = False) -> Path:
        """Process a YAML file to anonymize dataset references."""
        print(f"\nProcessing YAML file: {yaml_file}")

        # Extract file paths
        file_paths = self.extract_file_paths_from_yaml(yaml_file)
        print(f"Found {len(file_paths)} file references")

        # Generate anonymous mappings
        for original_path in file_paths:
            anon_path = self.generate_anonymous_name(original_path)
            print(f"  {original_path} -> {Path(anon_path).relative_to(self.output_dir)}")

        # Copy files if not in quick mode
        if not quick_mode:
            print(f"\nCopying dataset files to {self.output_dir}...")
            for original_path, anon_path in self.file_mapping.items():
                self.copy_dataset_file(original_path, anon_path, dry_run)

        # Create anonymized YAML
        output_yaml = yaml_file.parent / f"{yaml_file.stem}_anonymized{yaml_file.suffix}"

        if not dry_run:
            print(f"\nCreating anonymized YAML: {output_yaml}")
            content = yaml_file.read_text()

            # Replace all file paths
            for original, anonymous in self.file_mapping.items():
                # Handle different quote styles and formats
                patterns = [
                    (f'"{original}"', f'"{anonymous}"'),
                    (f"'{original}'", f"'{anonymous}'"),
                    (f' {original} ', f' {anonymous} '),
                    (f'({original})', f'({anonymous})'),
                ]
                for old_pattern, new_pattern in patterns:
                    content = content.replace(old_pattern, new_pattern)

            # Also handle paths without quotes that end with file extension
            for original, anonymous in self.file_mapping.items():
                # Use regex for unquoted paths
                content = re.sub(
                    rf'\b{re.escape(original)}\b',
                    anonymous,
                    content
                )

            output_yaml.write_text(content)
            print(f"  Saved to: {output_yaml}")
        else:
            print(f"\n[DRY RUN] Would create: {output_yaml}")

        return output_yaml

    def save_mapping(self, filepath: Path) -> None:
        """Save anonymization mappings to a JSON file."""
        mapping_data = {
            'output_dir': str(self.output_dir),
            'dataset_mapping': self.dataset_mapping,
            'file_mapping': self.file_mapping,
            'reverse_mapping': {v: k for k, v in self.file_mapping.items()},
            'statistics': {
                'total_datasets': len(self.dataset_mapping),
                'total_files': len(self.file_mapping)
            }
        }

        with open(filepath, 'w') as f:
            json.dump(mapping_data, f, indent=2)

        print(f"\nMapping saved to: {filepath}")
        print(f"  Datasets anonymized: {len(self.dataset_mapping)}")
        print(f"  Files anonymized: {len(self.file_mapping)}")

    def load_mapping(self, filepath: Path) -> None:
        """Load anonymization mappings from a JSON file."""
        with open(filepath, 'r') as f:
            mapping_data = json.load(f)

        self.output_dir = Path(mapping_data.get('output_dir', self.output_dir))
        self.dataset_mapping = mapping_data.get('dataset_mapping', {})
        self.file_mapping = mapping_data.get('file_mapping', {})
        self.counter = len(self.dataset_mapping)

    def generate_summary_report(self) -> str:
        """Generate an anonymization summary."""
        lines = [
            "=" * 60,
            "Anonymization Summary",
            "=" * 60,
            f"Output directory: {self.output_dir}",
            f"Datasets: {len(self.dataset_mapping)}",
            f"Files: {len(self.file_mapping)}",
            "",
            "Dataset Mappings:"
        ]

        for original, anonymous in sorted(self.dataset_mapping.items()):
            lines.append(f"  {anonymous} <- {original}")

        lines.append("=" * 60)
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Dataset Anonymizer for ParaView-MCP evaluation files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Anonymize a test file and copy datasets:
  python anonymize_dataset.py test.yaml

  # Quick mode - only update paths without copying:
  python anonymize_dataset.py test.yaml --quick

  # Custom output directory:
  python anonymize_dataset.py test.yaml -o my_output_dir

  # Dry run to preview changes:
  python anonymize_dataset.py test.yaml --dry-run
        """
    )
    parser.add_argument(
        "yaml_file",
        type=Path,
        help="YAML test file to anonymize"
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("eval/anonymized_datasets"),
        help="Output directory for anonymized datasets (default: eval/anonymized_datasets)"
    )
    parser.add_argument(
        "-m", "--mapping",
        type=Path,
        help="Save/load anonymization mapping to/from JSON file"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Only update YAML paths without copying data files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing any files"
    )

    args = parser.parse_args()

    # Validate input
    if not args.yaml_file.exists():
        print(f"Error: File '{args.yaml_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    if not args.yaml_file.suffix in ['.yaml', '.yml']:
        print(f"Error: Input must be a YAML file, got '{args.yaml_file}'", file=sys.stderr)
        sys.exit(1)

    # Initialize anonymizer
    anonymizer = DatasetAnonymizer(output_dir=args.output_dir)

    # Load existing mapping if specified
    if args.mapping and args.mapping.exists():
        anonymizer.load_mapping(args.mapping)
        print(f"Loaded existing mapping from '{args.mapping}'")

    # Process the YAML file
    output_yaml = anonymizer.process_yaml_file(
        args.yaml_file,
        quick_mode=args.quick,
        dry_run=args.dry_run
    )

    # Save mapping if specified
    if args.mapping and not args.dry_run:
        anonymizer.save_mapping(args.mapping)

    # Print summary
    print("\n" + anonymizer.generate_summary_report())

    if args.quick:
        print("\n⚠️  Quick mode: Only YAML was updated. Data files were not copied.")
        print(f"   To use the anonymized test, copy data files to: {args.output_dir}")
    elif not args.dry_run:
        print(f"\n✅ Anonymization complete!")
        print(f"   Anonymized YAML: {output_yaml}")
        print(f"   Data files in: {args.output_dir}")


if __name__ == "__main__":
    main()