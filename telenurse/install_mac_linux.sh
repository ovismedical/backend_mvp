#!/bin/bash

echo "==================================="
echo "Ovis Telenurse Installation Script"
echo "==================================="
echo
echo "This script will install everything needed to run Ovis Telenurse."
echo
read -p "Press Enter to continue..."

# Determine the OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS_TYPE="macOS"
else
    OS_TYPE="Linux"
fi

# Install Python if not installed
if ! command -v python3 &> /dev/null; then
    echo "Python is not installed."
    
    if [[ "$OS_TYPE" == "macOS" ]]; then
        echo "Please download and install Python from:"
        echo "https://www.python.org/downloads/mac-osx/"
        echo "After installing Python, run this script again."
        exit 1
    else
        # Linux installation (assuming apt-based distro)
        echo "Installing Python..."
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip
        echo "Python installed successfully!"
    fi
else
    echo "Python is already installed."
fi

# Install required packages
echo "Installing required packages..."
python3 -m pip install openai azure-cognitiveservices-speech requests

# Configure OpenAI API key
echo
echo "Please enter your OpenAI API key:"
read -p "> " OPENAI_API_KEY

# Update the key in gpt_convo.py
if [[ "$OS_TYPE" == "macOS" ]]; then
    sed -i '' "s/client = openai.OpenAI(api_key=\".*\")/client = openai.OpenAI(api_key=\"$OPENAI_API_KEY\")/" gpt_convo.py
else
    sed -i "s/client = openai.OpenAI(api_key=\".*\")/client = openai.OpenAI(api_key=\"$OPENAI_API_KEY\")/" gpt_convo.py
fi

# Create launcher script
cat > start_telenurse.sh << EOL
#!/bin/bash
echo "Starting Ovis Telenurse..."
python3 test_interface.py
EOL

chmod +x start_telenurse.sh

echo
echo "==================================="
echo "Installation Complete!"
echo "==================================="
echo
echo "To start Ovis Telenurse, open a terminal in this directory and run:"
echo "./start_telenurse.sh"
echo

# Ask if user wants to start the application now
read -p "Would you like to start Ovis Telenurse now? (y/n) " -n 1 -r START_NOW
echo
if [[ $START_NOW =~ ^[Yy]$ ]]; then
    ./start_telenurse.sh
fi