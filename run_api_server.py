#!/usr/bin/env python3
"""
Cross-platform API server launcher with OS detection and venv activation.
"""
import os
import sys
import subprocess
import platform
from pathlib import Path


def detect_os():
    """Detect the operating system."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "mac"
    elif system == "linux":
        return "linux"
    else:
        return "unknown"


def get_venv_path():
    """Get the virtual environment path based on OS."""
    base_dir = Path(__file__).parent
    os_type = detect_os()
    
    if os_type == "windows":
        venv_path = base_dir / "venv" / "Scripts"
        python_executable = venv_path / "python.exe"
    else:
        # macOS and Linux
        venv_path = base_dir / "venv" / "bin"
        python_executable = venv_path / "python3"
    
    return venv_path, python_executable


def check_venv_exists():
    """Check if virtual environment exists."""
    venv_path, python_executable = get_venv_path()
    return venv_path.exists() and python_executable.exists()


def create_venv():
    """Create virtual environment if it doesn't exist."""
    base_dir = Path(__file__).parent
    venv_dir = base_dir / "venv"
    
    print("Creating virtual environment...")
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        print("Virtual environment created successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error creating virtual environment: {e}")
        return False


def install_requirements():
    """Install required packages."""
    _, python_executable = get_venv_path()
    requirements_file = Path(__file__).parent / "requirements.txt"
    
    # Create requirements.txt if it doesn't exist
    if not requirements_file.exists():
        requirements = [
            "fastapi>=0.100.0",
            "uvicorn[standard]>=0.23.0",
            "websockets>=11.0",
            "python-telegram-bot>=20.0",
            "instaloader>=4.10",
            "yt-dlp>=2023.7.6",
            "aiohttp>=3.8.0",
            "python-dotenv>=1.0.0",
            "requests>=2.31.0",
        ]
        with open(requirements_file, "w") as f:
            f.write("\n".join(requirements))
        print(f"Created requirements.txt with dependencies")
    
    print("Installing dependencies...")
    try:
        subprocess.run([str(python_executable), "-m", "pip", "install", "-r", str(requirements_file)], check=True)
        print("Dependencies installed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error installing dependencies: {e}")
        return False


def get_activation_command():
    """Get the command to activate the virtual environment."""
    os_type = detect_os()
    venv_path, _ = get_venv_path()
    
    if os_type == "windows":
        activate_script = venv_path / "activate.bat"
        if not activate_script.exists():
            activate_script = venv_path / "Activate.ps1"
        return str(activate_script)
    else:
        activate_script = venv_path / "activate"
        return f"source {activate_script}"


def print_activation_instructions():
    """Print instructions for manual activation."""
    os_type = detect_os()
    activate_cmd = get_activation_command()
    
    print("\n" + "="*60)
    print(f"OS DETECTED: {os_type.upper()}")
    print("="*60)
    print("\nTo manually activate the virtual environment, run:")
    print(f"\n  {activate_cmd}")
    print("\nThen run the API server:")
    print("\n  python api_server.py")
    print("\n" + "="*60)


def run_server():
    """Run the API server with the correct Python interpreter."""
    _, python_executable = get_venv_path()
    api_server = Path(__file__).parent / "api_server.py"
    
    print(f"\nStarting API server using: {python_executable}")
    print(f"Server script: {api_server}")
    print("\n" + "="*60)
    
    try:
        # Use the venv Python to run the server
        subprocess.run([str(python_executable), str(api_server)])
    except KeyboardInterrupt:
        print("\n\nServer stopped by user.")
    except Exception as e:
        print(f"\nError running server: {e}")
        return False
    
    return True


def main():
    """Main entry point."""
    os_type = detect_os()
    
    print("="*60)
    print("  API SERVER LAUNCHER")
    print("="*60)
    print(f"\nDetected OS: {os_type.upper()}")
    
    # Check if venv exists
    if not check_venv_exists():
        print("\nVirtual environment not found!")
        
        # Ask user if they want to create it
        response = input("\nWould you like to create a virtual environment? (y/n): ").strip().lower()
        
        if response in ('y', 'yes'):
            if not create_venv():
                print("\nFailed to create virtual environment. Please create it manually.")
                print_activation_instructions()
                return
            
            # Ask to install requirements
            response = input("\nInstall dependencies? (y/n): ").strip().lower()
            if response in ('y', 'yes'):
                if not install_requirements():
                    print("\nFailed to install dependencies. Please install them manually.")
                    print_activation_instructions()
                    return
        else:
            print("\nPlease set up the virtual environment manually.")
            print_activation_instructions()
            return
    
    # Check if dependencies are installed
    try:
        _, python_executable = get_venv_path()
        result = subprocess.run(
            [str(python_executable), "-c", "import fastapi"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print("\nDependencies not found in virtual environment.")
            response = input("Install dependencies now? (y/n): ").strip().lower()
            if response in ('y', 'yes'):
                if not install_requirements():
                    print("\nFailed to install dependencies.")
                    print_activation_instructions()
                    return
            else:
                print("\nPlease install dependencies manually.")
                print_activation_instructions()
                return
    except Exception as e:
        print(f"\nError checking dependencies: {e}")
    
    # Run the server
    print("\nVirtual environment is ready!")
    print(f"Activation command: {get_activation_command()}")
    print("\nStarting server...")
    print("-" * 60)
    
    run_server()


if __name__ == "__main__":
    main()
