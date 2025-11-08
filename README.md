# V2X Playground Documentation

### Container Management
Navigate to the **Containers** tab to see available images:

### Setting up Local Game Environment

Install UV package manager for managing CTF dependencies:
- Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
- Windows:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Now setup the CTF package to create a local game environment with all dependencies installed.

```bash
git clone https://github.com/purs3lab/ctf-package
```
Navigate to the `ctf-package` directory and run:
```bash
uv sync
```
If you get errors due to platform compatibility, you have to try installing the carla package manually:
```bash
uv pip install ./<one of the whl files>
```
This is only for windows users, as the ctf-package is configured for linux.

Now you can activate the environment using the following command:

```bash
source .venv/bin/activate # For Linux
.\.venv\Scripts\activate # For Windows
```

Verify the installation by running:
```bash
python -c "import carla"
```
If the import works without errors, you are good to go!

### Connecting to Simulator

- Use the file no_rendering_mode.py to connect to the CARLA simulator as follows:
Server IP and allocated port can be found in the UI under the Containers tab when you start the CARLA Container.
```bash
# Inside the ctf-package directory
python no_rendering_mode.py --host <Server IP> --port 2000
```
- You can have multiple instances of the client running to use one of the instances for top down view map to make things easy.

### Connecting to Simulator using API

```python
import carla

# Use the allocated port from environment or UI
client = carla.Client('IP', 2000)
client.set_timeout(5.0)
world = client.get_world()
```

## 📝 API Reference

### Authentication
```bash
# Register new user
curl -X POST http://localhost:3000/register \
  -H "Content-Type: application/json" \
  -d '{"username": "myuser", "password": "mypass"}'

# Login and get token
curl -X POST http://localhost:3000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "myuser", "password": "mypass"}'
```

### Container Operations
```bash
# List available images
curl http://localhost:3000/images

# List my containers  
curl -X POST http://localhost:3000/containers \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<your-token>"}'

# Start a container
curl -X POST http://localhost:3000/containers/operation \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<token>", "imagename": "v2x-playground-carla", "operation": "start"}'

# Get container status
curl -X POST http://localhost:3000/containers/operation \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<token>", "imagename": "v2x-playground-carla", "operation": "status"}'
```

### Monitoring
```bash
# Health check
curl -X POST http://localhost:3000/containers/health \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<token>", "container_name": "v2x-playground-carla"}'

# Get logs
curl -X POST http://localhost:3000/logs \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<token>", "container_name": "v2x-playground-carla"}'

# Port status
curl -X POST http://localhost:3000/ports/status \
  -H "Content-Type: application/json" \
  -d '{"accesstoken": "<token>"}'
```
