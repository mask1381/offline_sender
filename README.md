# Offline Sender

A media processing and scheduled dispatching worker for Telegram channels.

## Overview
Offline Sender receives media batches (photos, videos, carousels), processes them according to defined formatting rules, and dispatches them to designated Telegram channels with scheduling intervals.

## Features
- **Batch Media Processing**: Handles downloads and optimizations for images, carousels, and video posts.
- **Dynamic Captions**: Applies hashtag taxonomy and dictionary lookups.
- **Decoupled Architecture**: No channel targets, profiles, or static footers are hardcoded; all configuration is dynamically supplied per batch.
- **Status Reporting**: Sends status acknowledgments (/ack) to a management chat.

## Repository Structure
- scripts/process_batch.py: Batch runner and dispatcher.
- character_dictionary.csv: Translation and name standardization dictionary.
- hashtag_rules.json: Hashtag rules and metadata mapping.
- 
equirements.txt: Python package dependencies.
- .github/workflows/process-batch.yml: Workflow runner for batch processing.

## Setup
`ash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
`

## Environment Variables
- TG_API_ID: Telegram API ID
- TG_API_HASH: Telegram API Hash
- TG_SESSION_STRING: Telethon StringSession
- BATCH_CHAT_ID: Telegram management chat ID for batch files and acks

## License
MIT
