# Giphy Channel Status Dashboard

A web-based dashboard to check Giphy channel status, detecting whether a channel is shadow banned, banned, or working properly.

## Features

- 🔍 **Channel Status Detection**: Automatically detects if a Giphy channel is:
  - ✅ **Working**: Channel is active and accessible
  - 👻 **Shadow Banned**: Channel exists but content is not visible
  - 🚫 **Banned**: Channel has been explicitly banned
  - 🔍 **Not Found**: Channel doesn't exist

- 📊 **Detailed Information**: Displays comprehensive channel details including:
  - Username and display name
  - User ID
  - Profile information
  - Recent GIF count
  - Social media links
  - Channel description

- 🎨 **Modern UI**: Beautiful, responsive dashboard with real-time status updates

## Installation

1. **Clone or navigate to the project directory**

2. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

3. **Set up Giphy API Key (Optional)**:
   - The app uses a public beta key by default
   - For better rate limits, set your own API key:
   ```bash
   # Windows PowerShell
   $env:GIPHY_API_KEY="your_api_key_here"
   
   # Linux/Mac
   export GIPHY_API_KEY="your_api_key_here"
   ```

4. **Set up Notion integration**:
   - The app reads Notion credentials from server configuration only.
   - Add them to a `.env` file or use environment variables.
   ```bash
   # .env
   NOTION_API_KEY="your_notion_api_key_here"
   NOTION_DATABASE_ID="your_database_id_here"
   GIPHY_API_KEY="your_giphy_api_key_here"
   ```

5. **Run the application**:
```bash
python app.py
```

5. **Open your browser** and navigate to:
```
http://localhost:5000
```

## Usage

1. Enter a Giphy channel URL in one of these formats:
   - `https://giphy.com/channel/username`
   - `https://giphy.com/@username`

2. Click "Check Status" or press Enter

3. View the results:
   - Status badge showing the channel's current state
   - Detailed channel information
   - Detection results for shadow ban, ban, and working status

## How It Works

The dashboard uses the Giphy API to:
1. Extract channel information from the provided URL
2. Search for the channel/user in Giphy's database
3. Attempt to access the channel's content (GIFs)
4. Analyze the responses to determine:
   - If the channel exists
   - If content is accessible
   - If there are any restrictions (shadow ban or ban)

## Status Detection Logic

- **Working**: Channel exists and content is accessible
- **Shadow Banned**: Channel exists but no content is visible or accessible
- **Banned**: API returns 403 Forbidden when accessing channel
- **Not Found**: Channel doesn't exist in Giphy's database

## Project Structure

```
Channel_Status/
├── app.py                 # Flask backend API
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── templates/
│   └── index.html        # Dashboard HTML
└── static/
    ├── style.css         # Dashboard styles
    └── script.js         # Frontend JavaScript
```

## Notes

- The Giphy API has rate limits. For production use, consider using your own API key.
- Some channels may require authentication to access, which could affect detection accuracy.
- Shadow ban detection is based on heuristics and may not be 100% accurate.

## License

This project is provided as-is for educational and personal use.

