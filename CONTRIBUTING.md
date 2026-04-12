# Contributing to MacroTouch

Thank you for your interest in contributing to MacroTouch! We welcome contributions from the community.

## Development Setup

1. Fork the repository
2. Clone your fork: `git clone https://github.com/yourusername/MacroTouch.git`
3. Create a virtual environment: `python -m venv .venv`
4. Activate the environment: `source .venv/bin/activate` (Linux/Mac) or `.venv\Scripts\activate` (Windows)
5. Install dependencies: `pip install -r requirements.txt`
6. Run the application: `python desktop-app/main.py`

## Project Structure

- `firmware/` - ESP32-S3 firmware code
- `desktop-app/` - Python PyQt6 desktop application
  - `main.py` - Application entry point
  - `modules/` - Core application modules
  - `ui/` - Qt Designer UI files
  - `assets/` - Icons and images
- `images/` - Screenshots and device photos
- `example-config/` - Sample configuration files

## Coding Standards

- Follow PEP 8 style guidelines
- Use type hints where possible
- Write docstrings for functions and classes
- Keep lines under 100 characters

## Testing

Run tests with: `python -m pytest tests/`

## Releases

GitHub releases are built automatically through GitHub Actions.

1. Update the version tag you want to publish.
2. Push a tag like `v1.0.1`.
3. The workflow in `.github/workflows/release.yml` will build Windows and Linux artifacts and upload them to GitHub Releases.

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/your-feature-name`
2. Make your changes
3. Run tests and ensure they pass
4. Commit your changes: `git commit -m "Add your commit message"`
5. Push to your fork: `git push origin feature/your-feature-name`
6. Create a Pull Request

## Reporting Issues

When reporting bugs, please include:
- Operating system and version
- Python version
- Steps to reproduce the issue
- Expected vs actual behavior
- Any relevant error messages or logs

## License

By contributing to MacroTouch, you agree that your contributions will be licensed under the MIT License.
