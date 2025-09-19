#!/bin/bash
# Activate the virtual environment
source ./fastapi_env/bin/activate

# Run the FastAPI app with uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 8000

#make it executable with chmod +x test_commands.sh
#./test_commands.sh (to run the server)
